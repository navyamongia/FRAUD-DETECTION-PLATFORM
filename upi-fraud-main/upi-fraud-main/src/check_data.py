"""check_data.py - sanity checks on the generated data. Run: python src/check_data.py"""
import pandas as pd
try:
    df = pd.read_parquet("data/transactions.parquet")
except Exception:
    df = pd.read_csv("data/transactions.csv", parse_dates=["ts"])
win = df[df.split != "warmup"]

print("=== 1. SIZE AND SPLITS ===")
print(win.groupby("split").agg(txns=("label", "size"), fraud=("label", "sum"), fraud_pct=("label", lambda s: round(s.mean() * 100, 2))))

print("\n=== 2. FRAUD BY SCENARIO (window) ===")
print(win[win.label == 1].groupby("fraud_type").agg(txns=("amount", "size"), incidents=("incident_id", "nunique"),
      median_amt=("amount", "median"), pct_call=("call_active", "mean"), pct_screen=("screen_share", "mean")).round(2))

print("\n=== 3. HARD GENUINE CASES (label 0) ===")
print(win[(win.label == 0) & (win.hard_type != "")].hard_type.value_counts())

print("\n=== 4. LEAKAGE CHECK: flag rates by label ===")
print(win.groupby("label")[["call_active", "screen_share"]].mean().round(3))
print(win.groupby("label").amount.describe()[["mean", "50%", "75%", "max"]].round(0))