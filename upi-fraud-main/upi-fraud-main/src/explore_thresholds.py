"""
explore_thresholds.py - how should warn_t and hold_t be set?

Run:  python src/explore_thresholds.py      (needs data/scores.parquet from train.py)

Thresholds are picked on VAL (as a % of genuine payments flagged) and judged on TEST.
"""
from pathlib import Path

import numpy as np
import pandas as pd

HEED_WARN, HOLD_EFFECT = 0.5, 0.9
D = Path("data")
df = pd.read_parquet(D / "scores.parquet") if (D / "scores.parquet").exists() else pd.read_csv(D / "scores.csv", low_memory=False)
va, te = df[df.split == "val"], df[df.split == "test"]
neg_va = va.score[va.label == 0].values
loss = (te.label == 1) & te.sender.astype(str).str.startswith("U")
tot_loss = float((te.amount * loss).sum())
fraud, gen = te.label == 1, te.label == 0

rows = []
for wf in [0.02, 0.01, 0.005, 0.002]:
    for hf in [0.002, 0.001, 0.0005, 0.0002]:
        if hf >= wf:
            continue
        wt, ht = np.quantile(neg_va, 1 - wf), np.quantile(neg_va, 1 - hf)
        hold, warn = te.score >= ht, (te.score >= wt) & (te.score < ht)
        eff = np.where(hold, HOLD_EFFECT, np.where(warn, HEED_WARN, 0.0))
        rows.append({
            "warn_FPR%": wf * 100, "hold_FPR%": hf * 100,
            "fraud_held%": (fraud & hold).sum() / fraud.sum() * 100,
            "fraud_warned%": (fraud & warn).sum() / fraud.sum() * 100,
            "fraud_missed%": (fraud & ~hold & ~warn).sum() / fraud.sum() * 100,
            "genuine_warned%": (gen & warn).sum() / gen.sum() * 100,
            "genuine_held%": (gen & hold).sum() / gen.sum() * 100,
            "warn_precision%": (fraud & warn).sum() / max(warn.sum(), 1) * 100,
            "hold_precision%": (fraud & hold).sum() / max(hold.sum(), 1) * 100,
            "prevented%": float((te.amount * loss * eff).sum()) / tot_loss * 100,
        })
out = pd.DataFrame(rows)
print("=== WARN / HOLD THRESHOLD GRID (picked on val, judged on test) ===")
print(out.round(2).to_string(index=False))
out.round(3).to_csv("reports/threshold_grid.csv", index=False)
