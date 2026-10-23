"""
selftest.py - tests the scoring engine WITHOUT the web server, and measures hot-path latency.

Run:  python src/selftest.py            (replays 20,000 test transactions)
      python src/selftest.py 50000      (more)
"""
import sys
import time

import numpy as np

from engine import Engine

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
eng = Engine()
eng.start_threads()          # the graph is rebuilt in a background thread, exactly as in the server

print("\n=== 1. DEMO SCENARIOS (expected vs what the model actually decides) ===")
print(f"{'scenario':22s} {'expected':9s} {'observed':9s} {'risk':>6s}")
for s in eng.scenarios:
    flag = "" if s["expected"] == s["observed"] else "   <-- differs"
    print(f"{s['id']:22s} {s['expected']:9s} {s['observed']:9s} {s['risk']:6.1f}{flag}")

print(f"\n=== 2. REPLAY {N:,} TRANSACTIONS through the full hot path ===")
tot, feat, mod, expl = [], [], [], []
t0 = time.time()
for _ in range(N):
    r = eng.step()
    if r is None:
        break
    L = r["latency_ms"]
    tot.append(L["total"]); feat.append(L["features"]); mod.append(L["model"])
    if r["action"] != "allow":
        expl.append(L["explain"])
wall = time.time() - t0
tot = np.array(tot)
print(f"throughput: {len(tot) / wall:,.0f} txns/sec on one thread")
print("HOT-PATH LATENCY (ms)   p50 %.3f | p95 %.3f | p99 %.3f | p99.9 %.3f | max %.2f   (budget 200)" % (
    *np.percentile(tot, [50, 95, 99, 99.9]), tot.max()))
print("breakdown (mean ms):    features %.3f | model %.3f | reasons (flagged only) %.3f" % (
    np.mean(feat), np.mean(mod), np.mean(expl) if expl else 0))
print(f"transactions over 200 ms: {(tot > 200).sum()}")

print("\n=== 3. LIVE OUTCOMES (should look like the offline test results) ===")
print("actions:", eng.counts)
o = eng.outcomes
fr = o["fraud_caught"] + o["fraud_missed"]
print(f"fraud caught {o['fraud_caught']} / {fr}  ({o['fraud_caught'] / max(fr, 1) * 100:.1f}%) | "
      f"false alarms {o['false_alarms']} ({o['false_alarms'] / max(len(tot) - fr, 1) * 100:.2f}% of genuine)")
eng.close()
