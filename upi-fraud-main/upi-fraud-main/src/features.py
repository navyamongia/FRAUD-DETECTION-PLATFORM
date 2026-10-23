"""
features.py - ONE feature engine used for both offline training and the live scorer.

Why one engine: training and serving use the exact same code, so there is no
train/serve skew. It is also CAUSAL: features(x) only sees transactions that
happened BEFORE x; commit(x) then adds x to the state.

    eng = FeatureEngine(accounts_created)
    f = eng.features(txn)          # dict of features, computed from the past only
    eng.commit(txn)                # now txn becomes history
    eng.observe_label(t, receiver) # a fraud label arrives; receiver becomes a
                                   # "known mule seed" only 24h later (feedback delay)

Run:  python src/features.py        -> data/features.parquet  (about 3-5 minutes)
"""
import heapq
import json
import math
import time
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

DAY = 86400.0
POP_MEDIAN = 350.0          # fallback "normal amount" for users with <3 past txns
GRAPH_WIN = 6 * 3600.0      # rolling account graph covers the last 6 hours
SEED_DELAY = 24 * 3600.0    # a fraud label reaches the system 24h after the txn
NAN = float("nan")

FEATURES_BEHAV = [
    "log_amount", "amt_over_median", "amt_over_max", "hour", "hour_dev", "is_new_payee", "payee_txn_count",
    "is_new_device", "dist_km", "vel_1h", "vel_24h", "amt_sum_1h", "new_payees_1h", "sender_hist_n", "sender_age_days",
]
FEATURES_CONTEXT = ["is_collect", "is_qr", "call_active", "screen_share", "qr_mismatch"]
FEATURES_NETWORK = [
    "recv_age_days", "recv_in_1h", "recv_in_24h", "recv_out_24h", "recv_uniq_senders_24h", "recv_in_out_ratio",
    "recv_pt_min_24h", "recv_in_total", "recv_pagerank", "recv_comp_size", "recv_hops_seed", "recv_is_seed",
    "send_in_24h", "send_in_amt_24h", "send_since_last_in", "send_flowthrough", "send_hops_seed", "send_is_seed",
]
FEATURE_GROUPS = {"behavioural": FEATURES_BEHAV + FEATURES_CONTEXT,
                  "behavioural+network": FEATURES_BEHAV + FEATURES_CONTEXT + FEATURES_NETWORK}
ALL_FEATURES = FEATURES_BEHAV + FEATURES_CONTEXT + FEATURES_NETWORK


class UserState:
    __slots__ = ("amts", "amax", "n", "payees", "devices", "times", "np_times", "hs", "hc", "lat", "lon")

    def __init__(self):
        self.amts = deque(maxlen=50)
        self.amax = 0.0
        self.n = 0
        self.payees = {}
        self.devices = set()
        self.times = deque()      # (t, amount) for the last 24h
        self.np_times = deque()   # times of new-payee txns, last 1h
        self.hs = 0.0
        self.hc = 0.0
        self.lat = 0.0
        self.lon = 0.0


class AccState:
    __slots__ = ("ins", "outs", "uniq", "in_amt", "out_amt", "last_in", "pt", "in_total")

    def __init__(self):
        self.ins = deque()        # (t, sender, amt) last 24h
        self.outs = deque()       # (t, amt) last 24h
        self.uniq = {}            # sender -> count inside the 24h window
        self.in_amt = 0.0
        self.out_amt = 0.0
        self.last_in = None
        self.pt = deque()         # (t, gap_seconds): money-in -> money-out gaps
        self.in_total = 0


def haversine(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


class FeatureEngine:
    def __init__(self, accounts_created, refresh_sec=600.0):
        self.created = accounts_created          # account_id -> created time (sec since START)
        self.refresh_sec = refresh_sec           # how often (in data-time) the account graph is rebuilt
        self.users = {}
        self.accs = {}
        self.edges = deque()                     # (t, sender, receiver) last 6h
        self.seeds = set()
        self.pending = []                        # heap of (effective_time, account)
        self.last_refresh = -1e18
        self.pr, self.comp, self.hops = {}, {}, {}

    # ------------------------------------------------------------ labels
    def observe_label(self, t, receiver):
        heapq.heappush(self.pending, (t + SEED_DELAY, receiver))

    # --------------------------------------------------- rolling graph (6h)
    def _advance(self, t):
        while self.pending and self.pending[0][0] <= t:
            self.seeds.add(heapq.heappop(self.pending)[1])
        if t - self.last_refresh >= self.refresh_sec:
            self._refresh_graph(t)

    def snapshot_edges(self, t):
        """Trim to the 6h window and return a copy. Call while holding the engine lock."""
        E = self.edges
        while E and E[0][0] < t - GRAPH_WIN:
            E.popleft()
        return list(E)

    @staticmethod
    def compute_graph(edges, seeds):
        """PURE function (safe to run in a background thread): PageRank, connected-component sizes and
        hops-to-nearest-known-mule for a list of (t, sender, receiver) edges."""
        idx = {}
        src, dst = [], []
        for _, s, r in edges:
            src.append(idx.setdefault(s, len(idx)))
            dst.append(idx.setdefault(r, len(idx)))
        n = len(idx)
        if n == 0:
            return {}, {}, {}
        names = list(idx)
        src = np.array(src); dst = np.array(dst)
        outdeg = np.bincount(src, minlength=n).astype(float)
        pr = np.full(n, 1.0 / n)
        dang = outdeg == 0
        for _ in range(20):                                   # PageRank by power iteration
            contrib = pr[src] / outdeg[src]
            new = np.bincount(dst, weights=contrib, minlength=n)
            pr = 0.15 / n + 0.85 * (new + pr[dang].sum() / n)
        pr_d = dict(zip(names, (pr * n).tolist()))            # x n so 1.0 = "average importance"
        parent = list(range(n))                               # union-find: connected components

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        adj = [[] for _ in range(n)]
        for a, b in zip(src.tolist(), dst.tolist()):
            adj[a].append(b); adj[b].append(a)
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        sizes = {}
        roots = [find(i) for i in range(n)]
        for r in roots:
            sizes[r] = sizes.get(r, 0) + 1
        comp = {names[i]: sizes[roots[i]] for i in range(n)}
        dist = [9] * n                                        # hops to nearest known mule seed (<=4)
        q = deque()
        for s_ in seeds:
            i = idx.get(s_)
            if i is not None:
                dist[i] = 0; q.append(i)
        while q:
            a = q.popleft()
            if dist[a] >= 4:
                continue
            for b in adj[a]:
                if dist[b] == 9:
                    dist[b] = dist[a] + 1; q.append(b)
        hops = {names[i]: dist[i] for i in range(n) if dist[i] < 9}
        return pr_d, comp, hops

    def apply_graph(self, pr, comp, hops):
        self.pr, self.comp, self.hops = pr, comp, hops        # atomic swap

    def _refresh_graph(self, t):
        self.last_refresh = t
        self.apply_graph(*self.compute_graph(self.snapshot_edges(t), self.seeds))

    # -------------------------------------------------------- windows
    @staticmethod
    def _trim_acc(a, t):
        c = t - DAY
        while a.ins and a.ins[0][0] < c:
            _, s, amt = a.ins.popleft()
            a.in_amt -= amt
            k = a.uniq[s] - 1
            if k: a.uniq[s] = k
            else: del a.uniq[s]
        while a.outs and a.outs[0][0] < c:
            a.out_amt -= a.outs.popleft()[1]
        while a.pt and a.pt[0][0] < c:
            a.pt.popleft()

    # ------------------------------------------------------- features
    def features(self, x):
        t = x["t"]
        self._advance(t)
        s, r, amt = x["sender"], x["receiver"], x["amount"]
        hour = (t % DAY) / 3600.0
        f = {"log_amount": math.log1p(amt), "hour": hour}

        us = self.users.get(s)
        if us is None or us.n == 0:
            f.update(amt_over_median=amt / POP_MEDIAN, amt_over_max=amt / 2000.0, hour_dev=0.0, is_new_payee=1,
                     payee_txn_count=0, is_new_device=0, dist_km=0.0, vel_1h=0, vel_24h=0, amt_sum_1h=0.0,
                     new_payees_1h=1, sender_hist_n=0)
        else:
            med = float(np.median(us.amts)) if len(us.amts) >= 3 else POP_MEDIAN
            f["amt_over_median"] = amt / max(med, 1.0)
            f["amt_over_max"] = amt / max(us.amax, 1.0) if us.n >= 3 else amt / 2000.0
            mh = (math.atan2(us.hs, us.hc) / (2 * math.pi) * 24.0) % 24.0
            f["hour_dev"] = abs((hour - mh + 12.0) % 24.0 - 12.0)
            cnt = us.payees.get(r, 0)
            f["is_new_payee"] = int(cnt == 0)
            f["payee_txn_count"] = cnt
            f["is_new_device"] = int(x["device_id"] not in us.devices)
            f["dist_km"] = haversine(us.lat / us.n, us.lon / us.n, x["lat"], x["lon"])
            c24, c1 = t - DAY, t - 3600.0
            while us.times and us.times[0][0] < c24:
                us.times.popleft()
            while us.np_times and us.np_times[0] < c1:
                us.np_times.popleft()
            v1, s1 = 0, 0.0
            for tt, aa in reversed(us.times):
                if tt < c1:
                    break
                v1 += 1; s1 += aa
            f["vel_1h"], f["vel_24h"], f["amt_sum_1h"] = v1, len(us.times), s1
            f["new_payees_1h"] = len(us.np_times) + f["is_new_payee"]
            f["sender_hist_n"] = us.n
        f["sender_age_days"] = (t - self.created.get(s, t)) / DAY

        # context
        f["is_collect"] = int(x["channel"] == "collect")
        f["is_qr"] = int(x["channel"] == "qr")
        f["call_active"] = int(x["call_active"])
        f["screen_share"] = int(x["screen_share"])
        qm = x["qr_merchant"]
        # the merchant registry knows each shop's registered account (here: the merchant id itself)
        f["qr_mismatch"] = int(bool(qm) and r != qm)

        # network: receiver side
        f["recv_age_days"] = (t - self.created.get(r, t)) / DAY
        ra = self.accs.get(r)
        if ra is None:
            f.update(recv_in_1h=0, recv_in_24h=0, recv_out_24h=0, recv_uniq_senders_24h=0, recv_in_out_ratio=0.0,
                     recv_pt_min_24h=NAN, recv_in_total=0)
        else:
            self._trim_acc(ra, t)
            c1 = t - 3600.0
            i1 = 0
            for e in reversed(ra.ins):
                if e[0] < c1:
                    break
                i1 += 1
            f["recv_in_1h"], f["recv_in_24h"] = i1, len(ra.ins)
            f["recv_out_24h"] = len(ra.outs)
            f["recv_uniq_senders_24h"] = len(ra.uniq)
            f["recv_in_out_ratio"] = (ra.in_amt + 1.0) / (ra.out_amt + 1.0)
            f["recv_pt_min_24h"] = min((g for _, g in ra.pt), default=NAN)
            f["recv_in_total"] = ra.in_total
        f["recv_pagerank"] = self.pr.get(r, 0.0)
        f["recv_comp_size"] = self.comp.get(r, 1)
        f["recv_is_seed"] = int(r in self.seeds)
        f["recv_hops_seed"] = 0 if r in self.seeds else self.hops.get(r, 9)

        # network: sender side (is the sender passing money through?)
        sa = self.accs.get(s)
        if sa is None:
            f.update(send_in_24h=0, send_in_amt_24h=0.0, send_since_last_in=NAN)
            f["send_flowthrough"] = 0.0
        else:
            self._trim_acc(sa, t)
            f["send_in_24h"], f["send_in_amt_24h"] = len(sa.ins), sa.in_amt
            f["send_since_last_in"] = (t - sa.last_in) if (sa.last_in is not None and t - sa.last_in < DAY) else NAN
            f["send_flowthrough"] = min(amt / (sa.in_amt + 1.0), 5.0) if sa.in_amt > 0 else 0.0
        f["send_is_seed"] = int(s in self.seeds)
        f["send_hops_seed"] = 0 if s in self.seeds else self.hops.get(s, 9)
        return f

    # ---------------------------------------------------------- state update
    def commit(self, x):
        t, s, r, amt = x["t"], x["sender"], x["receiver"], x["amount"]
        us = self.users.get(s)
        if us is None:
            us = self.users[s] = UserState()
        if r not in us.payees:
            us.np_times.append(t)
        us.payees[r] = us.payees.get(r, 0) + 1
        us.devices.add(x["device_id"])
        us.amts.append(amt)
        us.amax = max(us.amax, amt)
        us.n += 1
        us.times.append((t, amt))
        h = (t % DAY) / 3600.0 * 2 * math.pi / 24.0
        us.hs += math.sin(h); us.hc += math.cos(h)
        us.lat += x["lat"]; us.lon += x["lon"]

        sa = self.accs.get(s)
        if sa is None:
            sa = self.accs[s] = AccState()
        sa.outs.append((t, amt)); sa.out_amt += amt
        if sa.last_in is not None and t - sa.last_in < DAY:
            sa.pt.append((t, t - sa.last_in))
        ra = self.accs.get(r)
        if ra is None:
            ra = self.accs[r] = AccState()
        ra.ins.append((t, s, amt)); ra.in_amt += amt
        ra.uniq[s] = ra.uniq.get(s, 0) + 1
        ra.last_in = t
        ra.in_total += 1
        self.edges.append((t, s, r))


# ============================================================ offline build
def _load(name):
    p = Path("data") / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.read_csv(Path("data") / f"{name}.csv", low_memory=False)


def _save(df, name):
    try:
        df.to_parquet(Path("data") / f"{name}.parquet", index=False)
    except ImportError:
        df.to_csv(Path("data") / f"{name}.csv", index=False)


def main():
    meta = json.load(open("data/meta.json"))
    start = pd.Timestamp(meta["start"])
    df = _load("transactions")
    acc = _load("accounts")
    df["ts"] = pd.to_datetime(df["ts"])
    acc["created_ts"] = pd.to_datetime(acc["created_ts"])
    created = dict(zip(acc["account_id"], (acc["created_ts"] - start).dt.total_seconds()))
    eng = FeatureEngine(created)
    t_arr = (df["ts"] - start).dt.total_seconds().values
    cols = {k: df[k].values for k in ["sender", "receiver", "amount", "channel", "qr_merchant", "device_id", "lat",
                                      "lon", "call_active", "screen_share", "label"]}
    qrm = np.where(pd.isna(cols["qr_merchant"]), "", cols["qr_merchant"])
    rows, lat_ms = [], []
    n = len(df)
    print(f"building features for {n:,} transactions ...")
    t_wall = time.perf_counter()
    for i in range(n):
        x = {"t": float(t_arr[i]), "sender": cols["sender"][i], "receiver": cols["receiver"][i],
             "amount": float(cols["amount"][i]), "channel": cols["channel"][i], "qr_merchant": qrm[i],
             "device_id": cols["device_id"][i], "lat": float(cols["lat"][i]), "lon": float(cols["lon"][i]),
             "call_active": int(cols["call_active"][i]), "screen_share": int(cols["screen_share"][i])}
        t0 = time.perf_counter()
        rows.append(eng.features(x))
        lat_ms.append((time.perf_counter() - t0) * 1000)
        eng.commit(x)
        if cols["label"][i] == 1:
            eng.observe_label(x["t"], x["receiver"])
        if (i + 1) % 100000 == 0:
            print(f"  {i + 1:,} / {n:,}   ({time.perf_counter() - t_wall:.0f}s)")
    feats = pd.DataFrame(rows, columns=ALL_FEATURES).astype("float32")
    keep = ["txn_id", "ts", "day", "split", "sender", "receiver", "amount", "label", "fraud_type", "hard_type",
            "incident_id"]
    out = pd.concat([df[keep].reset_index(drop=True), feats], axis=1)
    _save(out, "features")
    lat = np.array(lat_ms)
    stats = {"p50_ms": float(np.percentile(lat, 50)), "p95_ms": float(np.percentile(lat, 95)),
             "p99_ms": float(np.percentile(lat, 99)), "max_ms": float(lat.max())}
    json.dump(stats, open("data/feature_latency.json", "w"))
    print(f"\nsaved data/features: {out.shape[0]:,} rows x {len(ALL_FEATURES)} features")
    print("feature latency (ms)  p50 %.3f | p95 %.3f | p99 %.3f | max %.1f" % tuple(stats.values()))

    w = out[out.split != "warmup"]
    print("\n=== key features: genuine vs each fraud type (window, medians) ===")
    key = ["amt_over_median", "is_new_payee", "is_new_device", "dist_km", "new_payees_1h", "qr_mismatch",
           "recv_age_days", "recv_in_24h", "recv_uniq_senders_24h", "send_since_last_in", "recv_hops_seed"]
    g = w.assign(grp=np.where(w.label == 0, "GENUINE", w.fraud_type)).groupby("grp")[key].median().round(2)
    print(g.to_string())


if __name__ == "__main__":
    main()
