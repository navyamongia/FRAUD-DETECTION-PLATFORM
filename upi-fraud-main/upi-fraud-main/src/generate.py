"""
generate.py - synthetic UPI transaction stream with documented fraud scenarios.

Run:   python src/generate.py                 (full: 20k users, ~500k txns in the 60-day window)
       python src/generate.py --users 2000    (quick test)

Timeline (82 days total, day 0 = 2026-08-01):
  days  0-21  warm-up   : builds user history only, never used for training/eval
  days 22-61  train
  days 62-71  validation (pick thresholds here)
  days 72-81  test       (report final numbers here, touch once)

Outputs (in data/):  transactions.parquet, users.parquet, accounts.parquet, meta.json
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

# ----------------------------------------------------------------- constants
START = pd.Timestamp("2026-08-01")
N_DAYS = 82
WARM = 22
TRAIN_END, VAL_END = 62, 72          # train: [22,62)  val: [62,72)  test: [72,82)
DAY = 86400.0

CITIES = {  # name: (lat, lon, weight)
    "Chennai": (13.08, 80.27, 0.16), "Mumbai": (19.08, 72.88, 0.16),
    "Delhi": (28.61, 77.21, 0.16), "Bengaluru": (12.97, 77.59, 0.14),
    "Hyderabad": (17.39, 78.49, 0.10), "Kolkata": (22.57, 88.36, 0.07),
    "Pune": (18.52, 73.86, 0.06), "Ahmedabad": (23.02, 72.57, 0.05),
    "Jaipur": (26.91, 75.79, 0.04), "Kochi": (9.93, 76.27, 0.03),
    "Coimbatore": (11.02, 76.96, 0.03),
}
CITY_NAMES = list(CITIES)
CITY_P = np.array([CITIES[c][2] for c in CITY_NAMES])
CITY_P = CITY_P / CITY_P.sum()

# festival windows (absolute day index): Ganesh Chaturthi Sep 14-24, Navratri/Dussehra Oct 11-20
FESTIVALS = [(44, 54, 1.5), (71, 80, 1.7)]
RENT_DAYS = {"aug": 0, "sep": 31, "oct": 61}   # rent paid on the 1st-5th of each month

DEFAULTS = dict(qr_merchant="", call_active=0, screen_share=0, label=0,
                fraud_type="", hard_type="", incident_id="")
COLS = ["t", "sender", "receiver", "amount", "channel", "device_id", "city"] + list(DEFAULTS)


class Buf:
    """Collects blocks of rows as column arrays; scalars are broadcast."""
    def __init__(self):
        self.parts = []

    def add(self, **c):
        n = len(c["t"])
        if n == 0:
            return
        row = {}
        for k in COLS:
            v = c.get(k, DEFAULTS.get(k))
            if np.ndim(v) == 0:
                v = np.full(n, v, dtype=object) if isinstance(v, str) else np.full(n, v)
            row[k] = np.asarray(v)
        self.parts.append(row)

    def frame(self):
        return pd.DataFrame({k: np.concatenate([p[k] for p in self.parts]) for k in COLS})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stealth", type=float, default=0.35, help="share of fraud incidents that mimic normal behaviour")
    ap.add_argument("--out", default="data")
    a = ap.parse_args()
    STEALTH = a.stealth
    rng = np.random.default_rng(a.seed)
    N = a.users
    scale = N / 20000.0
    out = Path(a.out)
    out.mkdir(exist_ok=True)

    # ------------------------------------------------------------- accounts
    acc = {}   # id -> (kind, created_day (float, relative to START), city)

    def reg(aid, kind, created_day, city):
        acc[aid] = (kind, float(created_day), city)

    U_ids = np.array([f"U{i:05d}" for i in range(N)], dtype=object)
    home = rng.choice(len(CITY_NAMES), N, p=CITY_P)
    med = np.clip(rng.lognormal(np.log(350), 0.8, N), 60, 5000)
    sig = rng.uniform(0.5, 1.0, N)
    center = rng.choice([10, 13, 19, 21], N, p=[.2, .3, .3, .2]) + rng.normal(0, 0.7, N)
    spread = rng.uniform(1.5, 3.5, N)
    senior = rng.random(N) < 0.12
    rate = rng.lognormal(np.log(0.33) - 0.245, 0.7, N)           # txns/user/day, mean ~0.33
    is_new_user = rng.random(N) < 0.05                            # genuinely new accounts
    created = np.where(is_new_user, rng.uniform(0, N_DAYS - 10, N), -rng.uniform(200, 2500, N))
    users_by_city = [np.where(home == c)[0] for c in range(len(CITY_NAMES))]
    for i in range(N):
        reg(U_ids[i], "user", created[i], CITY_NAMES[home[i]])

    # merchants (each lives in one city; Zipf popularity inside the city)
    NM = 600
    m_city = rng.choice(len(CITY_NAMES), NM, p=CITY_P)
    M_ids_all = np.array([f"M{i:04d}" for i in range(NM)], dtype=object)
    M_ids, M_p = [], []
    for c in range(len(CITY_NAMES)):
        ids = M_ids_all[m_city == c]
        p = 1.0 / np.arange(1, len(ids) + 1) ** 0.8
        rng.shuffle(ids)
        M_ids.append(ids)
        M_p.append(p / p.sum())
    for i in range(NM):
        reg(M_ids_all[i], "merchant", -rng.uniform(300, 3000), CITY_NAMES[m_city[i]])

    # ------------------------------------------------------------- calendar
    days_all = np.arange(N_DAYS)
    dow = (START + pd.to_timedelta(days_all, unit="D")).weekday.values
    w = np.where(dow >= 5, 1.15, 1.0)
    FEST = np.zeros(N_DAYS, dtype=bool)
    for s, e, mult in FESTIVALS:
        w[s:e + 1] *= mult
        FEST[s:e + 1] = True

    buf = Buf()
    dev_of = {}   # for mules: device and city

    # ----------------------------------------------------- GENUINE behaviour
    rent_plan = rng.choice([0, 1, 2, 3], N, p=[.92, .056, .012, .012])  # 0 none,1 stable,2 moved Sep,3 moved Oct
    rent_plan[created > 0] = 0
    has_trip = rng.random(N) < 0.05
    has_rel = rng.random(N) < 0.04
    new_dev_day = np.where(rng.random(N) < 0.025, rng.integers(WARM, N_DAYS - 5, N), -1)
    has_dev2 = rng.random(N) < 0.04

    for i in range(N):
        uid = U_ids[i]
        h = home[i]
        # regular payees
        k = int(rng.integers(3, 7))
        nm = int(rng.binomial(k, 0.45))
        nm = min(nm, len(M_ids[h]))
        reg_m = list(rng.choice(M_ids[h], nm, p=M_p[h], replace=False)) if nm else []
        cands = rng.choice(users_by_city[h], min(k - nm + 1, len(users_by_city[h])), replace=False)
        reg_u = [U_ids[j] for j in cands if j != i][:max(k - nm, 1)]
        regs = np.array(reg_m + reg_u, dtype=object)
        regw = rng.dirichlet(np.ones(len(regs)) * 0.7)

        first_day = max(0, int(np.ceil(created[i])))
        ww = w.copy()
        ww[:first_day] = 0
        n = rng.poisson(rate[i] * ww.sum())
        trip = None
        if has_trip[i]:
            ts_ = int(rng.integers(WARM + 2, N_DAYS - 8))
            trip = (ts_, ts_ + int(rng.integers(3, 8)), int(rng.choice([c for c in range(len(CITY_NAMES)) if c != h])))

        if n > 0:
            days = rng.choice(N_DAYS, n, p=ww / ww.sum())
            hr = (center[i] + rng.normal(0, spread[i], n)) % 24
            t = (days * 24 + hr) * 3600.0
            fest = FEST[days]
            amt = med[i] * rng.lognormal(0, sig[i], n) * (1 + 0.6 * fest)
            city = np.full(n, h)
            intrip = np.zeros(n, dtype=bool)
            if trip:
                intrip = (days >= trip[0]) & (days <= trip[1])
                city[intrip] = trip[2]
            u = rng.random(n)
            p_reg = 0.60 - 0.12 * fest
            p_m = 0.28 + 0.08 * fest
            kind = np.where(u < p_reg, 0, np.where(u < p_reg + p_m, 1, 2))
            recv = np.empty(n, dtype=object)
            m0 = kind == 0
            recv[m0] = rng.choice(regs, m0.sum(), p=regw)
            m1 = kind == 1
            for c in np.unique(city[m1]):
                mm = m1 & (city == c)
                recv[mm] = rng.choice(M_ids[c], mm.sum(), p=M_p[c])
            m2 = kind == 2
            j = rng.integers(0, N, m2.sum())
            j = np.where(j == i, (j + 1) % N, j)
            recv[m2] = U_ids[j]
            rs = recv.astype(str)
            is_m = np.char.startswith(rs, "M")
            chan = np.full(n, "p2p", dtype=object)
            qr = is_m & (rng.random(n) < 0.8)
            chan[qr] = "qr"
            qrm = np.where(qr, recv, "")
            chan[(~is_m) & (rng.random(n) < 0.05)] = "collect"
            amt = np.where(is_m, amt * 0.6, amt)
            amt = np.clip(np.round(amt), 10, 100000)
            dev = np.full(n, f"D{i}a", dtype=object)
            if has_dev2[i]:
                dev[rng.random(n) < 0.12] = f"D{i}b"
            ht = np.full(n, "", dtype=object)
            ht[fest & (amt > 4 * med[i])] = "festival_spike"
            ht[intrip] = "travel"
            if new_dev_day[i] >= 0:
                after = np.where(t >= new_dev_day[i] * DAY)[0]
                dev[after] = f"D{i}c"
                first3 = after[np.argsort(t[after])][:3]
                ht[first3] = "new_device"
            buf.add(t=t, sender=uid, receiver=recv, amount=amt, channel=chan, device_id=dev,
                    city=np.array(CITY_NAMES, dtype=object)[city], qr_merchant=qrm,
                    call_active=(rng.random(n) < 0.025).astype(int),
                    screen_share=(rng.random(n) < 0.004).astype(int), hard_type=ht)

        if trip:  # hotel / local spending while travelling
            ne = int(rng.integers(2, 6))
            d = rng.integers(trip[0], trip[1] + 1, ne)
            tt = (d * 24 + rng.uniform(9, 23, ne)) * 3600.0
            buf.add(t=tt, sender=uid, receiver=rng.choice(M_ids[trip[2]], ne, p=M_p[trip[2]]),
                    amount=np.round(rng.uniform(300, 5000, ne)), channel="qr",
                    device_id=f"D{i}a", city=CITY_NAMES[trip[2]], hard_type="travel")
            buf.parts[-1]["qr_merchant"] = buf.parts[-1]["receiver"].astype(object)

        if has_rel[i]:  # first-time transfer to a relative
            d = int(rng.integers(max(WARM, first_day), N_DAYS))
            j = int(rng.integers(0, N))
            j = (j + 1) % N if j == i else j
            buf.add(t=np.array([(d * 24 + (center[i] % 24)) * 3600.0]), sender=uid, receiver=U_ids[j],
                    amount=np.array([np.round(np.clip(med[i] * rng.uniform(3, 8), 1500, 30000))]),
                    channel="p2p", device_id=f"D{i}a", city=CITY_NAMES[h], hard_type="first_relative")

        if rent_plan[i] > 0:  # monthly rent; new landlord on the move month is a hard genuine case
            rent = float(np.round(np.clip(rng.lognormal(np.log(12000), 0.5), 4000, 60000) / 500) * 500)
            la, lb = f"L{i:05d}a", f"L{i:05d}b"
            reg(la, "landlord", -rng.uniform(300, 3000), CITY_NAMES[h])
            plan = rent_plan[i]
            if plan >= 2:
                reg(lb, "landlord", -rng.uniform(300, 3000), CITY_NAMES[h])
            land = {"aug": la, "sep": lb if plan == 2 else la, "oct": lb if plan >= 2 else la}
            for mon, d0 in RENT_DAYS.items():
                d = d0 + int(rng.integers(0, 5))
                newp = (mon != "aug") and ((mon == "sep" and plan == 2) or (mon == "oct" and plan == 3))
                buf.add(t=np.array([(d * 24 + rng.uniform(8, 22)) * 3600.0]), sender=uid, receiver=land[mon],
                        amount=np.array([rent]), channel="p2p", device_id=f"D{i}a", city=CITY_NAMES[h],
                        hard_type="rent_new_payee" if newp else "")

    # ---------------------------------- GROUP COLLECTIONS (genuine fan-in/out)
    n_events = max(2, int(40 * scale))
    for e in range(n_events):
        c = int(rng.choice(len(CITY_NAMES), p=CITY_P))
        if len(users_by_city[c]) < 40:
            continue
        org = f"G{e:04d}"
        reg(org, "organizer", -rng.uniform(300, 2000), CITY_NAMES[c])
        d = int(rng.integers(WARM, N_DAYS - 2))
        k = int(rng.integers(12, 31))
        mem = rng.choice(users_by_city[c], k, replace=False)
        fixed = float(rng.choice([100, 200, 500, 1000, 1500]))
        amts = np.where(rng.random(k) < 0.7, fixed, np.round(rng.uniform(100, 2000, k) / 50) * 50)
        tt = (d * 24 + rng.uniform(8, 22, k)) * 3600.0
        buf.add(t=tt, sender=U_ids[mem], receiver=org, amount=amts, channel="p2p",
                device_id=np.array([f"D{m}a" for m in mem], dtype=object), city=CITY_NAMES[c],
                hard_type="group_collect")
        vendor = rng.choice(M_ids[c], 1, p=M_p[c])[0]
        buf.add(t=np.array([(d + 1) * DAY + rng.uniform(9, 15) * 3600]), sender=org, receiver=vendor,
                amount=np.array([np.round(amts.sum() * rng.uniform(0.9, 1.0))]), channel="p2p",
                device_id=f"OD{e}", city=CITY_NAMES[c], hard_type="group_collect")

    # ================================================================ FRAUD
    def uhour(u):
        return (center[u] + rng.normal(0, spread[u])) % 24

    def victim(boost=1.0, size=None):
        p = np.where(senior, boost, 1.0) * (created <= 0)
        return rng.choice(N, size, p=p / p.sum())

    def new_mule(kind, use_t, aged_p=0.15):
        mid = f"Z{len(acc):06d}"
        d_use = use_t / DAY
        cd = d_use - (rng.uniform(100, 600) if rng.random() < aged_p else rng.uniform(0.1, 4.0))
        reg(mid, kind, cd, CITY_NAMES[int(rng.choice(len(CITY_NAMES), p=CITY_P))])
        dev_of[mid] = f"MD{mid}"
        return mid

    # pool of single-hop scam receiver accounts, each active ~8 days
    n_pool = max(20, int(150 * scale))
    pool_first = rng.uniform(WARM, N_DAYS - 6, n_pool)
    pool = [new_mule("mule", f * DAY, 0.30) for f in pool_first]

    def scam_acc(day):
        ok = np.where((pool_first <= day) & (day <= pool_first + 8))[0]
        j = rng.choice(ok) if len(ok) else int(np.argmin(np.abs(pool_first - day)))
        return pool[j]

    def fday():
        return int(rng.integers(WARM, N_DAYS))

    def fr(t, sender, receiver, amount, channel, device, city, ftype, inc, call=0, ss=0, qrm=""):
        t = np.asarray(t, dtype=float)
        buf.add(t=t, sender=sender, receiver=receiver, amount=np.round(amount), channel=channel,
                device_id=device, city=city, qr_merchant=qrm, call_active=call, screen_share=ss,
                label=1, fraud_type=ftype, incident_id=inc)

    # 1. fake collect request ------------------------------------------------
    for k in range(int(650 * scale)):
        u = victim(2.5); d = fday()
        fr([(d * 24 + uhour(u)) * 3600], U_ids[u], scam_acc(d), [np.clip(med[u] * (rng.uniform(0.8, 2.5) if rng.random() < STEALTH else rng.uniform(1.5, 8)), 300, 60000)],
           "collect", f"D{u}a", CITY_NAMES[home[u]], "fake_collect", f"FC{k}",
           call=int(rng.random() < 0.25), ss=int(rng.random() < 0.03))

    # 2. impersonation / vishing --------------------------------------------
    for k in range(int(450 * scale)):
        u = victim(2.5); d = fday()
        t0 = (d * 24 + uhour(u)) * 3600
        st = rng.random() < STEALTH
        call = int(rng.random() < (0.5 if st else 0.8)); ss = int(rng.random() < 0.15)
        a1 = np.clip(med[u] * (rng.uniform(1.5, 5) if st else rng.uniform(4, 25)), 1000 if st else 3000, 99000)
        ts_, am = [t0], [a1]
        if rng.random() < 0.15:
            ts_.append(t0 + rng.uniform(180, 1200)); am.append(a1 * rng.uniform(0.3, 0.8))
        fr(ts_, U_ids[u], scam_acc(d), am, "p2p", f"D{u}a", CITY_NAMES[home[u]], "vishing", f"VI{k}",
           call=call, ss=ss)

    # 3. remote access / screen share burst ----------------------------------
    for k in range(int(130 * scale)):
        u = victim(2.0); d = fday()
        st = rng.random() < STEALTH
        n_t = int(rng.integers(2, 4)) if st else int(rng.integers(3, 7))
        t0 = (d * 24 + uhour(u)) * 3600
        tt = t0 + np.cumsum(rng.uniform(60, 360, n_t))
        recvs = [scam_acc(d) for _ in range(int(rng.integers(1, 4)))]
        rc = np.array([recvs[j % len(recvs)] for j in range(n_t)], dtype=object)
        fr(tt, U_ids[u], rc, np.maximum(med[u] * (rng.uniform(0.8, 2.5, n_t) if st else rng.uniform(2, 6, n_t)), 500 if st else 1000), "p2p", f"D{u}a",
           CITY_NAMES[home[u]], "screen_share", f"SS{k}", call=int(rng.random() < 0.6),
           ss=int(rng.random() < (0.5 if st else 0.85)))

    # 4. account takeover ----------------------------------------------------
    for k in range(int(180 * scale)):
        u = victim(1.0); d = fday()
        st = rng.random() < STEALTH
        hr = rng.uniform(0, 5) if (not st and rng.random() < 0.35) else uhour(u)
        n_t = int(rng.integers(1, 4))
        tt = (d * 24 + hr) * 3600 + np.cumsum(rng.uniform(30, 600, n_t))
        far = int(home[u]) if st else int(rng.choice([c for c in range(len(CITY_NAMES)) if c != home[u]]))
        base = np.clip(med[u] * (rng.uniform(2, 8) if st else rng.uniform(8, 40)), 1500 if st else 5000, 99000)
        am = base * np.array([1.0, 0.6, 0.4])[:n_t]
        fr(tt, U_ids[u], scam_acc(d), am, "p2p", f"X{k:05d}", CITY_NAMES[far], "account_takeover", f"AT{k}",
           call=int(rng.random() < 0.05), ss=int(rng.random() < 0.05))

    # 5. QR swap: shop QR now resolves to a mule account ----------------------
    for k in range(max(3, int(24 * scale))):
        m = int(rng.integers(0, NM)); c = int(m_city[m])
        if len(users_by_city[c]) < 30:
            continue
        d0 = int(rng.integers(WARM, N_DAYS - 4)); dur = int(rng.integers(1, 4))
        mule = new_mule("mule", (d0 - rng.uniform(0.3, 3)) * DAY, 0.4 if rng.random() < STEALTH else 0.15)
        n_t = max(6, int(rng.poisson(15)))
        cust = rng.choice(users_by_city[c], n_t, replace=True)
        dd = d0 + rng.integers(0, dur, n_t)
        tt = (dd * 24 + rng.uniform(9, 21, n_t)) * 3600
        am = np.clip(med[cust] * 0.5 * rng.lognormal(0, 0.7, n_t), 20, 5000)
        fr(tt, U_ids[cust], mule, am, "qr", np.array([f"D{x}a" for x in cust], dtype=object), CITY_NAMES[c],
           "qr_swap", f"QR{k}", call=(rng.random(n_t) < 0.02).astype(int), ss=(rng.random(n_t) < 0.004).astype(int),
           qrm=M_ids_all[m])

    # 6. mule chain: fan-in to fresh accounts, fast fan-out through 3-4 hops ----
    for c_i in range(max(3, int(36 * scale))):
        d = int(rng.integers(WARM, N_DAYS - 2))
        t0 = (d * 24 + rng.uniform(8, 20)) * 3600
        H = int(rng.choice([3, 4]))
        sizes = [int(rng.integers(2, 4))] + [int(rng.integers(2, 5)) for _ in range(H - 2)] + [2]
        slow = rng.random() < STEALTH
        layers = [[new_mule("mule", t0, 0.4 if slow else 0.15) for _ in range(s)] for s in sizes]
        kv = int(rng.integers(8, 15))
        vt = t0 + np.sort(rng.uniform(0, 5 * 3600, kv))
        vic = victim(1.5, kv)
        v_am = np.round(np.clip(med[vic] * rng.uniform(2, 12, kv), 1000, 80000))
        v_rc = np.array(rng.choice(layers[0], kv), dtype=object)
        fr(vt, U_ids[vic], v_rc, v_am, np.where(rng.random(kv) < 0.3, "collect", "p2p").astype(object),
           np.array([f"D{x}a" for x in vic], dtype=object), np.array([CITY_NAMES[home[x]] for x in vic], dtype=object),
           "mule_chain", f"MC{c_i}", call=(rng.random(kv) < 0.4).astype(int), ss=(rng.random(kv) < 0.1).astype(int))
        bal = {m: 0.0 for L in layers for m in L}
        last = {m: 0.0 for m in bal}
        for m, a_, t_ in zip(v_rc, v_am, vt):
            bal[m] += a_; last[m] = max(last[m], t_)
        for li in range(H - 1):
            for m in layers[li]:
                if bal[m] <= 0:
                    continue
                tgt = list(rng.choice(layers[li + 1], int(min(rng.integers(1, 4), len(layers[li + 1]))), replace=False))
                part = rng.dirichlet(np.ones(len(tgt)))
                total = bal[m] * rng.uniform(0.93, 0.99)
                if slow:
                    tt = last[m] + rng.uniform(1200, 7200) + np.cumsum(rng.uniform(60, 900, len(tgt)))
                else:
                    tt = last[m] + rng.uniform(60, 600) + np.cumsum(rng.uniform(20, 240, len(tgt)))
                am = np.round(total * part)
                fr(tt, m, np.array(tgt, dtype=object), am, "p2p", dev_of[m], acc[m][2], "mule_chain", f"MC{c_i}")
                for g, a_, t_ in zip(tgt, am, tt):
                    bal[g] += a_; last[g] = max(last[g], t_)

    # 7. rapid new-payee sequence --------------------------------------------
    for k in range(int(120 * scale)):
        u = victim(1.0); d = fday()
        st = rng.random() < STEALTH
        n_t = int(rng.integers(3, 6)) if st else int(rng.integers(3, 9))
        tt = (d * 24 + uhour(u)) * 3600 + np.cumsum(rng.uniform(300, 1500, n_t) if st else rng.uniform(60, 480, n_t))
        rc = np.empty(n_t, dtype=object)
        for j in range(n_t):
            if rng.random() < 0.6:
                rc[j] = scam_acc(d)
            else:
                x = int(rng.integers(0, N)); x = (x + 1) % N if x == u else x
                rc[j] = U_ids[x]
        fr(tt, U_ids[u], rc, np.maximum(med[u] * (rng.uniform(0.8, 2, n_t) if st else rng.uniform(1.5, 4, n_t)), 300 if st else 500), "p2p", f"D{u}a",
           CITY_NAMES[home[u]], "rapid_new_payee", f"RP{k}", call=int(rng.random() < 0.1), ss=int(rng.random() < 0.05))

    # ============================================================ finalize
    df = buf.frame()
    df = df[df["t"] < N_DAYS * DAY].sort_values("t", kind="mergesort").reset_index(drop=True)
    df.insert(0, "txn_id", np.arange(len(df)))
    df["ts"] = START + pd.to_timedelta(df["t"], unit="s")
    df["day"] = (df["t"] // DAY).astype(int)
    df["split"] = np.select([df.day < WARM, df.day < TRAIN_END, df.day < VAL_END], ["warmup", "train", "val"], "test")
    lat = df["city"].map({c: CITIES[c][0] for c in CITY_NAMES}).values + rng.normal(0, 0.05, len(df))
    lon = df["city"].map({c: CITIES[c][1] for c in CITY_NAMES}).values + rng.normal(0, 0.05, len(df))
    df["lat"], df["lon"] = lat.round(4), lon.round(4)
    df["amount"] = df["amount"].astype(float)
    for c_ in ["call_active", "screen_share", "label"]:
        df[c_] = df[c_].astype("int8")
    df = df.drop(columns=["t"])
    df = df[["txn_id", "ts", "day", "split", "sender", "receiver", "amount", "channel", "qr_merchant", "device_id",
             "city", "lat", "lon", "call_active", "screen_share", "label", "fraud_type", "hard_type", "incident_id"]]

    users = pd.DataFrame({"user_id": U_ids, "home_city": np.array(CITY_NAMES)[home], "median_amt": med.round(1),
                          "sigma": sig.round(3), "senior": senior, "created_day": created.round(2)})
    accounts = pd.DataFrame([(k, v[0], START + pd.Timedelta(days=v[1]), v[2]) for k, v in acc.items()],
                            columns=["account_id", "kind", "created_ts", "city"])

    def save(d, name):
        try:
            d.to_parquet(out / f"{name}.parquet", index=False)
        except ImportError:
            d.to_csv(out / f"{name}.csv", index=False)
    save(df, "transactions"); save(users, "users"); save(accounts, "accounts")
    json.dump(dict(start=str(START), n_days=N_DAYS, warm=WARM, train_end=TRAIN_END, val_end=VAL_END),
              open(out / "meta.json", "w"))
    win = df[df.split != "warmup"]
    print(f"saved {len(df):,} txns total | {len(win):,} in the 60-day window | "
          f"fraud {int(win.label.sum()):,} ({win.label.mean() * 100:.2f}%) | accounts {len(accounts):,}")


if __name__ == "__main__":
    main()