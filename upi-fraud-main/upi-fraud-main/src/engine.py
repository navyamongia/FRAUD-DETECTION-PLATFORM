"""
engine.py - the scoring core. No web code here, so it can be tested alone (python src/selftest.py).

HOT PATH  (score_txn): features -> LightGBM score -> decision (+ SHAP reasons only when flagged).
EXPLAIN PATH (trace, graph, simulate): computed on request, never inside the scoring call.
The account-graph rebuild runs in its own background thread (see _graph_loop).
"""
import gc
import json
import math
import threading
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from features import (ALL_FEATURES, DAY, FEATURES_BEHAV, FEATURES_CONTEXT, FeatureEngine)
from reasons import REASONS, explain

ROOT = Path(__file__).resolve().parent.parent
HEED_WARN, HOLD_EFFECT = 0.5, 0.9          # same assumptions as train.py
CITY_LATLON = {
    "Chennai": (13.08, 80.27), "Mumbai": (19.08, 72.88), "Delhi": (28.61, 77.21), "Bengaluru": (12.97, 77.59),
    "Hyderabad": (17.39, 78.49), "Kolkata": (22.57, 88.36), "Pune": (18.52, 73.86), "Ahmedabad": (23.02, 72.57),
    "Jaipur": (26.91, 75.79), "Kochi": (9.93, 76.27), "Coimbatore": (11.02, 76.96),
}
GROUP = {n: ("behavioural" if n in FEATURES_BEHAV else "context" if n in FEATURES_CONTEXT else "network")
         for n in ALL_FEATURES}

# Override rules: they can only ESCALATE a decision to hold, never downgrade it.
OVERRIDES = [
    ("collect_to_flagged_mule", lambda f: f["is_collect"] == 1 and f["recv_hops_seed"] <= 1, ["COLLECT", "KNOWN_MULE"]),
    ("qr_swap_new_account", lambda f: f["qr_mismatch"] == 1 and f["recv_age_days"] < 14, ["QR", "NEW_ACCOUNT"]),
    ("screen_share_new_payee_large",
     lambda f: f["screen_share"] == 1 and f["is_new_payee"] == 1 and f["amt_over_median"] >= 3, ["SCREEN", "NEW_PAYEE"]),
]
OVERRIDE_DESCRIPTIONS = {
    "collect_to_flagged_mule": "Collect request to an account linked to known mules",
    "qr_swap_new_account": "QR code resolves to a different, very new account",
    "screen_share_new_payee_large": "Screen-share active + new payee + unusually large amount",
}


def clean(o):
    """Make anything JSON-safe (NaN/inf -> null, numpy -> python)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    return o


def _read(name):
    p = ROOT / "data" / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.read_csv(ROOT / "data" / f"{name}.csv", low_memory=False)


class EdgeIndex:
    """All payer->payee pairs seen (aggregated), used for the investigation graph."""
    def __init__(self):
        self.out = defaultdict(dict)    # sender -> {receiver: [count, total_amount, last_t, last_txn]}
        self.inn = defaultdict(set)     # receiver -> {senders}

    def add(self, s, r, amt, t, txn_id):
        e = self.out[s].get(r)
        if e is None:
            self.out[s][r] = [1, amt, t, txn_id]
        else:
            e[0] += 1; e[1] += amt; e[2] = t; e[3] = txn_id
        self.inn[r].add(s)


class Engine:
    def __init__(self, store=None, log=print):
        self.log = log
        self.store = store
        self.emit = None                                  # server sets: emit(type, data)
        self.lock = threading.RLock()
        t_start = time.time()
        meta = json.load(open(ROOT / "models" / "model_meta.json", encoding="utf-8"))
        self.names = meta["features"]
        self.booster = lgb.Booster(model_file=str(ROOT / "models" / "final_model.txt"))
        dmeta = json.load(open(ROOT / "data" / "meta.json", encoding="utf-8"))
        self.start = datetime.fromisoformat(dmeta["start"])
        self.val_end = int(dmeta["val_end"])

        self.log("loading data ...")
        tx = _read("transactions")
        tx["ts"] = pd.to_datetime(tx["ts"])
        tx["t"] = (tx["ts"] - pd.Timestamp(dmeta["start"])).dt.total_seconds()
        tx["qr_merchant"] = tx["qr_merchant"].fillna("")
        for c in ("fraud_type", "hard_type"):
            tx[c] = tx[c].fillna("")
        acc = _read("accounts")
        acc["created_ts"] = pd.to_datetime(acc["created_ts"])
        self.created = dict(zip(acc["account_id"], (acc["created_ts"] - pd.Timestamp(dmeta["start"])).dt.total_seconds()))
        self.acc_kind = dict(zip(acc["account_id"], acc["kind"]))
        users = _read("users")
        self.users_home = dict(zip(users["user_id"], users["home_city"]))
        self.users_med = dict(zip(users["user_id"], users["median_amt"]))
        self.users_senior = dict(zip(users["user_id"], users["senior"]))

        # ---- warm up the feature store with everything BEFORE the replay (test) period
        self.fe = FeatureEngine(self.created, refresh_sec=float("inf"))   # graph rebuilt by a background thread
        self.edges = EdgeIndex()
        pre = tx[tx.day < self.val_end]
        self.log(f"warming up feature history with {len(pre):,} past transactions ...")
        cols = {k: pre[k].tolist() for k in ["t", "sender", "receiver", "amount", "device_id", "lat", "lon", "label",
                                             "txn_id"]}
        edge_from = int(np.searchsorted(pre["day"].values, self.val_end - 7))
        for i in range(len(pre)):
            x = {"t": cols["t"][i], "sender": cols["sender"][i], "receiver": cols["receiver"][i],
                 "amount": cols["amount"][i], "device_id": cols["device_id"][i], "lat": cols["lat"][i],
                 "lon": cols["lon"][i]}
            self.fe.commit(x)
            if cols["label"][i] == 1:
                self.fe.observe_label(x["t"], x["receiver"])
            if i >= edge_from:
                self.edges.add(x["sender"], x["receiver"], x["amount"], x["t"], cols["txn_id"][i])
        self.clock = float(cols["t"][-1])
        self.fe._advance(self.clock)
        self.fe.apply_graph(*self.fe.compute_graph(self.fe.snapshot_edges(self.clock), self.fe.seeds))
        self.last_graph_clock = self.clock
        del pre, cols

        # ---- replay data (test period)
        te = tx[tx.day >= self.val_end].reset_index(drop=True)
        self.R = {k: te[k].tolist() for k in ["t", "txn_id", "sender", "receiver", "amount", "channel", "qr_merchant",
                                              "device_id", "lat", "lon", "call_active", "screen_share", "label",
                                              "fraud_type", "hard_type"]}
        self.n_replay = len(te)
        self.ridx = 0

        # ---- thresholds, score distribution, curve (from scores.parquet: val + test)
        sc = _read("scores")
        va, tst = sc[sc.split == "val"], sc[sc.split == "test"]
        self.neg_sorted = np.sort(va.score[va.label == 0].values)
        self.sc_te, self.y_te = tst.score.values, tst.label.values.astype(int)
        victim = (tst.label == 1) & tst.sender.astype(str).str.startswith("U")
        self.loss_te = (tst.amount.values * victim.values)
        self.cfg = {"warn_t": float(meta["thresholds"]["warn_t"]), "hold_t": float(meta["thresholds"]["hold_t"]),
                    "overrides_enabled": True}
        self.curve = self._build_curve()
        self.normals = self._load_normals()

        # ---- live state
        self.ring = OrderedDict()                         # txn_id -> record for the Decision Trace
        self.pending = OrderedDict()                      # ad-hoc payments waiting for the user's answer
        self.node_risk = {}
        self.alert_counter = 1
        self.adhoc_counter = 9_000_000
        self.lat_ring = deque(maxlen=3000)
        self.recent = deque(maxlen=4000)                  # real timestamps of replayed txns
        self.counts = {"allow": 0, "warn": 0, "hold": 0}
        self.outcomes = {"fraud_caught": 0, "fraud_missed": 0, "false_alarms": 0}
        self.replay = {"running": False, "speed": 600.0, "finished": False}
        self._anchor = None
        self._stop = False
        self._threads = []

        self.scenarios = self._build_scenarios()
        # The warm-up history is millions of long-lived objects. Without this, Python's garbage collector
        # occasionally scans all of them, which shows up as a rare 300-500 ms stall on one transaction.
        gc.collect()
        gc.freeze()
        gc.set_threshold(100_000, 50, 100)
        self.log(f"engine ready in {time.time() - t_start:.0f}s | replay: {self.n_replay:,} txns "
                 f"({self.R['t'][0] / DAY:.1f}-{self.R['t'][-1] / DAY:.1f} days)")

    # ------------------------------------------------------------- helpers
    def iso(self, t):
        return (self.start + timedelta(seconds=float(t))).isoformat(timespec="seconds")

    def _load_normals(self):
        p = ROOT / "reports" / "feature_normals.json"
        if p.exists():
            return json.load(open(p, encoding="utf-8"))
        fdf = _read("features")
        med = fdf[(fdf.split == "train") & (fdf.label == 0)][self.names].median()
        out = {k: (None if pd.isna(v) else round(float(v), 3)) for k, v in med.items()}
        json.dump(out, open(p, "w", encoding="utf-8"))
        return out

    def pct_from_thr(self, thr):
        """% of genuine payments that score at least `thr` (this is the user-friendly slider scale)."""
        n = len(self.neg_sorted)
        return 100.0 * (1.0 - np.searchsorted(self.neg_sorted, thr, side="left") / n)

    def thr_from_pct(self, pct):
        return float(np.quantile(self.neg_sorted, 1.0 - float(pct) / 100.0))

    def _risk(self, score):
        n = len(self.neg_sorted)
        cdf = np.searchsorted(self.neg_sorted, score, side="right") / n
        tail = max(1.0 - cdf, 1e-4)
        return min(1.0, -math.log10(tail) / 4.0), tail * 100.0   # (0..1 risk, flag%)

    # -------------------------------------------------------------- config
    def get_config(self):
        c = self.cfg
        return {"warn_t": c["warn_t"], "hold_t": c["hold_t"], "warn_pct": round(self.pct_from_thr(c["warn_t"]), 4),
                "hold_pct": round(self.pct_from_thr(c["hold_t"]), 4), "overrides_enabled": c["overrides_enabled"],
                "overrides": [{"name": n, "description": OVERRIDE_DESCRIPTIONS[n]} for n, _, _ in OVERRIDES],
                "languages": ["en", "ta", "hi"]}

    def set_config(self, warn_t=None, hold_t=None, warn_pct=None, hold_pct=None, overrides_enabled=None):
        with self.lock:
            wt = self.thr_from_pct(warn_pct) if warn_pct is not None else (warn_t if warn_t is not None else self.cfg["warn_t"])
            ht = self.thr_from_pct(hold_pct) if hold_pct is not None else (hold_t if hold_t is not None else self.cfg["hold_t"])
            if ht < wt:
                raise ValueError("hold threshold must be at least the warn threshold (hold_pct must be <= warn_pct)")
            self.cfg["warn_t"], self.cfg["hold_t"] = float(wt), float(ht)
            if overrides_enabled is not None:
                self.cfg["overrides_enabled"] = bool(overrides_enabled)
        cfg = self.get_config()
        if self.emit:
            self.emit("config_changed", cfg)
        return cfg

    # ------------------------------------------------------------ decision
    def _decide(self, score, f):
        c = self.cfg
        action = "hold" if score >= c["hold_t"] else "warn" if score >= c["warn_t"] else "allow"
        override = None
        if c["overrides_enabled"] and action != "hold":
            for name, rule, _ in OVERRIDES:
                try:
                    hit = rule(f)
                except TypeError:
                    hit = False
                if hit:
                    override, action = name, "hold"
                    break
        return action, override

    # ------------------------------------------------------------ HOT PATH
    def score_txn(self, x, commit=False, source="replay", user_facing=False, truth=None, txn_id=None, emit=True):
        t0 = time.perf_counter()
        with self.lock:
            f = self.fe.features(x)
            if commit:
                self.fe.commit(x)
        t1 = time.perf_counter()
        vec = np.array([[f[n] for n in self.names]], dtype=np.float64)
        score = float(self.booster.predict(vec, num_threads=1)[0])
        t2 = time.perf_counter()
        action, override = self._decide(score, f)
        t3 = time.perf_counter()
        reasons = []
        if action != "allow":
            contrib = self.booster.predict(vec, pred_contrib=True, num_threads=1)[0][:-1]
            reasons = explain(contrib, vec[0], self.names, k=3, user_facing=user_facing)
            if override:
                ids = [r["id"] for r in reasons]
                for rid in reversed([o for o in OVERRIDES if o[0] == override][0][2]):
                    if rid not in ids:
                        reasons.insert(0, {"id": rid, "weight": 9.9, "text": REASONS[rid]})
                reasons = reasons[:3]
        t4 = time.perf_counter()
        total = (t4 - t0) * 1000
        risk01, flag_pct = self._risk(score)
        if txn_id is None:
            with self.lock:
                self.adhoc_counter += 1
                txn_id = self.adhoc_counter
        res = {
            "txn_id": txn_id, "ts": self.iso(x["t"]), "sender": x["sender"], "receiver": x["receiver"],
            "amount": x["amount"], "channel": x["channel"], "score": round(score, 6),
            "risk": round(risk01 * 100, 1), "flag_pct": round(flag_pct, 4), "action": action, "override": override,
            "thresholds": {"warn_t": self.cfg["warn_t"], "hold_t": self.cfg["hold_t"]},
            "reasons": reasons,
            "latency_ms": {"features": round((t1 - t0) * 1000, 3), "model": round((t2 - t1) * 1000, 3),
                           "decision": round((t3 - t2) * 1000, 4), "explain": round((t4 - t3) * 1000, 3),
                           "total": round(total, 3)},
            "next_step": {"allow": "settle", "warn": "confirm_or_cancel", "hold": "verify"}[action],
            "source": source,
        }
        if truth is not None:
            res["truth"] = truth
        if emit:
            self.ring[txn_id] = {"x": x, "f": f, "vec": vec, "res": res, "truth": truth}
            if len(self.ring) > 20000:
                self.ring.popitem(last=False)
            self.lat_ring.append(total)
            self.counts[action] += 1
            if truth is not None and source == "replay":
                if truth["fraud"]:
                    self.outcomes["fraud_caught" if action != "allow" else "fraud_missed"] += 1
                elif action != "allow":
                    self.outcomes["false_alarms"] += 1
            if self.emit:
                self.emit("txn_scored", {k: res[k] for k in ("txn_id", "ts", "sender", "receiver", "amount", "score",
                                                             "risk", "action", "source")}
                          | {"latency_ms": res["latency_ms"]["total"], "truth": truth})
            if action != "allow":
                with self.lock:
                    aid = self.alert_counter
                    self.alert_counter += 1
                alert = {"alert_id": aid, "txn_id": txn_id, "ts": res["ts"], "sender": x["sender"],
                         "receiver": x["receiver"], "amount": x["amount"], "score": res["score"], "risk": res["risk"],
                         "action": action, "override": override, "reasons": reasons, "case_id": None,
                         "source": source, "truth": truth}
                res["alert_id"] = aid
                if self.store:
                    self.store.add_alert(alert)
                if self.emit:
                    self.emit("alert", alert)
        return res

    # ---------------------------------------------------------- replay loop
    def step(self):
        """Score the next transaction of the replay (also used by selftest.py)."""
        i = self.ridx
        if i >= self.n_replay:
            self.replay["finished"] = True
            self.replay["running"] = False
            return None
        R = self.R
        t = R["t"][i]
        x = {"t": t, "sender": R["sender"][i], "receiver": R["receiver"][i], "amount": R["amount"][i],
             "channel": R["channel"][i], "qr_merchant": R["qr_merchant"][i], "device_id": R["device_id"][i],
             "lat": R["lat"][i], "lon": R["lon"][i], "call_active": R["call_active"][i],
             "screen_share": R["screen_share"][i]}
        truth = {"fraud": bool(R["label"][i]), "type": R["fraud_type"][i], "hard": R["hard_type"][i]}
        res = self.score_txn(x, commit=True, source="replay", truth=truth, txn_id=int(R["txn_id"][i]))
        with self.lock:
            if truth["fraud"]:
                self.fe.observe_label(t, x["receiver"])             # label reaches the system (24h feedback delay)
            self.edges.add(x["sender"], x["receiver"], x["amount"], t, res["txn_id"])
            self.clock = max(self.clock, t)
        r = x["receiver"]
        if res["score"] > self.node_risk.get(r, 0.0):
            self.node_risk[r] = res["score"]
        self.recent.append(time.time())
        self.ridx = i + 1
        return res

    def _replay_loop(self):
        while not self._stop:
            if not self.replay["running"]:
                time.sleep(0.05)
                self._anchor = None
                continue
            if self.ridx >= self.n_replay:
                self.replay["running"] = False
                self.replay["finished"] = True
                continue
            t_next = self.R["t"][self.ridx]
            if self._anchor is None or self._anchor[2] != self.replay["speed"]:
                self._anchor = (time.perf_counter(), t_next, self.replay["speed"])
            real0, sim0, spd = self._anchor
            due = real0 + (t_next - sim0) / spd
            now = time.perf_counter()
            if due > now:
                time.sleep(min(due - now, 0.05))
                continue
            self.step()

    def _graph_loop(self):
        """Background thread: rebuild PageRank / components / hops-to-mule every 600 simulated seconds."""
        while not self._stop:
            time.sleep(0.1)
            if self.clock - self.last_graph_clock >= 600.0:
                with self.lock:
                    edges = self.fe.snapshot_edges(self.clock)
                    seeds = set(self.fe.seeds)
                    self.last_graph_clock = self.clock
                self.fe.apply_graph(*self.fe.compute_graph(edges, seeds))   # heavy part runs without the lock

    def start_threads(self):
        for fn, name in ((self._replay_loop, "replay"), (self._graph_loop, "graph")):
            th = threading.Thread(target=fn, daemon=True, name=name)
            th.start()
            self._threads.append(th)

    def close(self):
        self._stop = True

    def replay_control(self, action, speed=None):
        if speed is not None:
            self.replay["speed"] = float(min(max(speed, 1.0), 20000.0))
        if action == "start":
            if self.replay["finished"]:
                raise ValueError("replay finished; restart the server for a fresh run")
            self.replay["running"] = True
        elif action in ("pause", "stop"):
            self.replay["running"] = False
        elif action != "speed":
            raise ValueError("action must be start, pause, stop or speed")
        st = self.replay_state()
        if self.emit:
            self.emit("replay_state", st)
        return st

    def replay_state(self):
        i = min(self.ridx, self.n_replay - 1)
        return {"running": self.replay["running"], "speed": self.replay["speed"], "finished": self.replay["finished"],
                "replay_ts": self.iso(self.R["t"][i]), "progress_pct": round(100.0 * self.ridx / self.n_replay, 2)}

    # --------------------------------------------------------------- stats
    def get_stats(self):
        lat = np.array(self.lat_ring) if self.lat_ring else np.array([0.0])
        now = time.time()
        rec = [t for t in list(self.recent)[-2000:] if now - t <= 5.0]
        return {"replay_ts": self.replay_state()["replay_ts"], "txns_scored": sum(self.counts.values()),
                "txns_per_sec": round(len(rec) / 5.0, 1), "actions": dict(self.counts),
                "latency_ms": {"p50": round(float(np.percentile(lat, 50)), 3), "p95": round(float(np.percentile(lat, 95)), 3),
                               "p99": round(float(np.percentile(lat, 99)), 3), "budget": 200},
                "known_outcomes": dict(self.outcomes)}

    # ----------------------------------------------------- ad-hoc payments
    def _build_x(self, p):
        s = p["sender"]
        home = self.users_home.get(s, "Chennai")
        city = p.get("city") or home
        lat, lon = CITY_LATLON.get(city, CITY_LATLON["Chennai"])
        device = p.get("device_id") or (f"D{int(s[1:])}a" if s[1:].isdigit() else "D0a")
        return {"t": self.clock + 1.0, "sender": s, "receiver": p["receiver"], "amount": float(p["amount"]),
                "channel": p.get("channel") or "p2p", "qr_merchant": p.get("qr_merchant") or "", "device_id": device,
                "lat": lat, "lon": lon, "call_active": int(bool(p.get("call_active"))),
                "screen_share": int(bool(p.get("screen_share")))}

    def adhoc(self, p, emit=True):
        """Score a payment typed in the payment simulator. It does NOT change the feature history,
        so clicking the same scenario twice gives the same answer."""
        res = self.score_txn(self._build_x(p), commit=False, source="simulator", user_facing=True, emit=emit)
        if emit:
            self.pending[res["txn_id"]] = res
            if len(self.pending) > 1000:
                self.pending.popitem(last=False)
        return res

    def respond(self, txn_id, response):
        res = self.pending.get(txn_id)
        if res is None:
            raise KeyError("unknown payment")
        a, amt = res["action"], f"INR {res['amount']:,.0f}"
        if response == "cancel":
            return {"status": "cancelled", "message": f"Payment of {amt} cancelled."}
        if a == "allow" or (a == "warn" and response == "proceed"):
            return {"status": "settled", "message": f"Payment of {amt} completed."}
        if a == "hold" and response == "verified":
            return {"status": "released", "message": f"Verification passed. Payment of {amt} released."}
        if a == "hold" and response == "failed_verification":
            return {"status": "rejected", "message": f"Verification failed. Payment of {amt} was blocked."}
        if a == "hold" and response == "proceed":
            raise ValueError("a held payment must be verified first")
        raise ValueError("response must be proceed, cancel, verified or failed_verification")

    # ------------------------------------------------------------ scenarios
    def _build_scenarios(self):
        rng = np.random.default_rng(7)
        users = self.fe.users
        cands = sorted(u for u, s in users.items() if u.startswith("U") and s.n >= 30
                       and 200 <= self.users_med.get(u, 0) <= 700)
        senders = [cands[i] for i in rng.choice(len(cands), 8, replace=False)]
        clock = self.clock
        mules = [a for a, k in self.acc_kind.items() if k == "mule" and clock - 4 * DAY <= self.created[a] <= clock]
        if len(mules) < 6:
            mules = sorted((a for a, k in self.acc_kind.items() if k == "mule" and self.created[a] <= clock),
                           key=lambda a: -self.created[a])[:12]
        mules = [mules[i] for i in rng.permutation(len(mules))[:6]]
        landlords = sorted(a for a, k in self.acc_kind.items() if k == "landlord")
        med = self.users_med

        def merchant_of(u):
            ms = [(r, c) for r, c in users[u].payees.items() if r.startswith("M")]
            return max(ms, key=lambda kv: kv[1])[0] if ms else "M0001"

        def stranger(u):
            home = self.users_home[u]
            pool = [v for v in cands if self.users_home[v] == home and v != u and v not in users[u].payees]
            return pool[0] if pool else cands[0]

        m = lambda u, k: max(300.0, round(med[u] * k))
        S = senders
        sc = [
            ("normal_grocery", "Normal grocery payment", "allow",
             dict(sender=S[0], receiver=merchant_of(S[0]), amount=m(S[0], 0.8), channel="qr", qr_merchant=merchant_of(S[0]))),
            ("rent_new_landlord", "Rent to a new landlord", "allow",
             dict(sender=S[1], receiver=[l for l in landlords if l not in users[S[1]].payees][0], amount=14000, channel="p2p")),
            ("first_time_relative", "First transfer to a relative", "allow",
             dict(sender=S[2], receiver=stranger(S[2]), amount=m(S[2], 6), channel="p2p")),
            ("vishing_call", "Large transfer to a new account while on a call", "hold",
             dict(sender=S[3], receiver=mules[0], amount=m(S[3], 15), channel="p2p", call_active=True)),
            ("fake_collect", "Approve a collect request", "hold",
             dict(sender=S[4], receiver=mules[1], amount=m(S[4], 4), channel="collect")),
            ("qr_swap", "Shop QR now pays a new account", "hold",
             dict(sender=S[5], receiver=mules[2], amount=350, channel="qr", qr_merchant=merchant_of(S[5]))),
            ("account_takeover", "New device in a far city", "hold",
             dict(sender=S[6], receiver=mules[3], amount=m(S[6], 25), channel="p2p", device_id="XNEWDEVICE",
                  city="Kolkata" if self.users_home[S[6]] != "Kolkata" else "Mumbai")),
            ("screen_share", "Screen-share app active during payment", "hold",
             dict(sender=S[7], receiver=mules[4], amount=m(S[7], 3), channel="p2p", screen_share=True, call_active=True)),
        ]
        out = []
        for sid, title, expected, pay in sc:
            r = self.adhoc(pay, emit=False)
            out.append({"id": sid, "title": title, "expected": expected, "observed": r["action"], "risk": r["risk"],
                        "payment": pay})
        return out

    # --------------------------------------------------------------- trace
    def trace(self, txn_id):
        rec = self.ring.get(txn_id)
        if rec is None:
            return None
        res, f, vec = rec["res"], rec["f"], rec["vec"]
        t0 = time.perf_counter()
        contrib = self.booster.predict(vec, pred_contrib=True, num_threads=1)[0]
        explain_ms = (time.perf_counter() - t0) * 1000
        shap = [{"feature": n, "group": GROUP[n], "value": f[n], "shap": float(c)}
                for n, c in zip(self.names, contrib[:-1])]
        shap.sort(key=lambda d: -abs(d["shap"]))
        x = rec["x"]
        lat = res["latency_ms"]
        out = {
            "txn": {"txn_id": txn_id, "ts": res["ts"], "sender": x["sender"], "receiver": x["receiver"],
                    "amount": x["amount"], "channel": x["channel"], "device_id": x["device_id"],
                    "lat": x["lat"], "lon": x["lon"], "qr_merchant": x["qr_merchant"],
                    "call_active": bool(x["call_active"]), "screen_share": bool(x["screen_share"])},
            "features": [{"name": n, "group": GROUP[n], "value": f[n], "normal": self.normals.get(n)}
                         for n in self.names],
            "shap": {"bias": float(contrib[-1]), "contributions": shap[:15],
                     "raw_logodds": float(contrib.sum()), "score": res["score"]},
            "graph": {"pagerank_rel": f["recv_pagerank"], "component_size": f["recv_comp_size"],
                      "hops_to_known_mule": f["recv_hops_seed"], "receiver_is_known_mule": bool(f["recv_is_seed"]),
                      "receiver_age_days": f["recv_age_days"], "receiver_unique_senders_24h": f["recv_uniq_senders_24h"]},
            "decision": {"score": res["score"], "risk": res["risk"], "flag_pct": res["flag_pct"],
                         "warn_t": res["thresholds"]["warn_t"], "hold_t": res["thresholds"]["hold_t"],
                         "override": res["override"], "action": res["action"]},
            "reasons": res["reasons"],
            "stages_ms": [{"stage": "features", "ms": lat["features"]}, {"stage": "model", "ms": lat["model"]},
                          {"stage": "decision", "ms": lat["decision"]},
                          {"stage": "reasons", "ms": lat["explain"]}, {"stage": "full_shap (trace)", "ms": round(explain_ms, 3)}],
            "total_ms": lat["total"], "source": res["source"],
        }
        if rec["truth"] is not None:
            out["ground_truth"] = rec["truth"]      # demo only: the simulator knows the real label
        return out

    # --------------------------------------------------------------- graph
    def neighbourhood(self, center, hops=2, cap=60):
        E = self.edges
        if center not in E.out and center not in E.inn:
            return None
        seen, keep, frontier = {center}, [center], [center]
        for _ in range(max(1, min(int(hops), 3))):
            nxt = []
            for a in frontier:
                if a != center and self.acc_kind.get(a) == "merchant":
                    continue                                   # do not expand huge hubs
                cand = [(e[1], b) for b, e in E.out.get(a, {}).items()]
                cand += [(E.out[b][a][1], b) for b in E.inn.get(a, ())]
                cand.sort(reverse=True)
                for _, b in cand[:14]:
                    if b not in seen and len(keep) < cap:
                        seen.add(b); keep.append(b); nxt.append(b)
            frontier = nxt
        nodes = []
        for a in keep:
            sc = self.node_risk.get(a)
            risk = self._risk(sc)[0] if sc is not None else 0.05
            kind = "merchant" if self.acc_kind.get(a) == "merchant" else "user"
            nodes.append({"id": a, "kind": kind, "age_days": round((self.clock - self.created.get(a, self.clock)) / DAY, 1),
                          "in_deg": len(E.inn.get(a, ())), "out_deg": len(E.out.get(a, {})),
                          "is_known_mule": a in self.fe.seeds, "risk": round(risk, 3)})
        edges = [{"source": a, "target": b, "amount": round(e[1]), "ts": self.iso(e[2]), "count": e[0]}
                 for a in keep for b, e in E.out.get(a, {}).items() if b in seen]
        return {"center": center, "nodes": nodes, "edges": edges}

    # ------------------------------------------- trade-off (slider) + curve
    def simulate(self, warn_t=None, hold_t=None, warn_pct=None, hold_pct=None):
        wt = self.thr_from_pct(warn_pct) if warn_pct is not None else (warn_t if warn_t is not None else self.cfg["warn_t"])
        ht = self.thr_from_pct(hold_pct) if hold_pct is not None else (hold_t if hold_t is not None else self.cfg["hold_t"])
        if ht < wt:
            raise ValueError("hold threshold must be at least the warn threshold")
        s, y = self.sc_te, self.y_te
        hold = s >= ht
        warn = (s >= wt) & ~hold
        flag = hold | warn
        npos, nneg = int((y == 1).sum()), int((y == 0).sum())
        tp, fp = int((flag & (y == 1)).sum()), int((flag & (y == 0)).sum())
        eff = np.where(hold, HOLD_EFFECT, np.where(warn, HEED_WARN, 0.0))
        total = float(self.loss_te.sum())
        prevented = float((self.loss_te * eff).sum())
        return {"warn_t": float(wt), "hold_t": float(ht), "warn_pct": round(self.pct_from_thr(wt), 4),
                "hold_pct": round(self.pct_from_thr(ht), 4),
                "precision": tp / max(tp + fp, 1), "recall": tp / max(npos, 1), "fpr": fp / max(nneg, 1),
                "warn_precision": float(((warn) & (y == 1)).sum() / max(warn.sum(), 1)),
                "hold_precision": float(((hold) & (y == 1)).sum() / max(hold.sum(), 1)),
                "friction": {"warned_pct": round(float((warn & (y == 0)).sum() / nneg * 100), 3),
                             "held_pct": round(float((hold & (y == 0)).sum() / nneg * 100), 3),
                             "genuine_per_1000_flagged": round(fp / max(nneg, 1) * 1000, 2)},
                "fraud_caught": tp, "fraud_total": npos, "genuine_flagged": fp,
                "fraud_prevented_inr": round(prevented), "fraud_total_inr": round(total),
                "assumptions": {"warn_heeded": HEED_WARN, "hold_effective": HOLD_EFFECT},
                "note": "Score thresholds only; override rules are not included. Computed on the held-out test period."}

    def _build_curve(self):
        s, y = self.sc_te, self.y_te
        npos, nneg = int((y == 1).sum()), int((y == 0).sum())
        pts = []
        for pct in np.geomspace(0.01, 5.0, 60):
            thr = self.thr_from_pct(pct)
            flag = s >= thr
            tp, fp = int((flag & (y == 1)).sum()), int((flag & (y == 0)).sum())
            pts.append({"flag_pct": round(float(pct), 4), "threshold": thr, "precision": tp / max(tp + fp, 1),
                        "recall": tp / max(npos, 1), "fpr": fp / max(nneg, 1)})
        return {"points": pts}

    def evaluation(self):
        out = json.load(open(ROOT / "reports" / "metrics.json", encoding="utf-8"))
        p = ROOT / "reports" / "shap_top_by_scenario.json"
        if p.exists():
            out["shap_top_by_scenario"] = json.load(open(p, encoding="utf-8"))
        return out
