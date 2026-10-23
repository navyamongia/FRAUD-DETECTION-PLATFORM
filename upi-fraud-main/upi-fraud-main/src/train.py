"""
train.py - LightGBM fraud model, time-based evaluation, ablation, decision layer.

Run:  python src/train.py        (about 3-8 minutes)

Split is by TIME (never random): train = days 22-61, val = days 62-71, test = days 72-81.
Warm-up days (0-21) only fed the feature history. Thresholds are chosen on VAL and
applied unchanged to TEST, so the test numbers are honest.

Outputs: models/*.txt, models/model_meta.json, reports/*.csv|json, data/scores.parquet
"""
import json
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from features import ALL_FEATURES, FEATURES_BEHAV, FEATURES_CONTEXT, FEATURES_NETWORK
from reasons import explain

SEED_FEATS = ["recv_hops_seed", "recv_is_seed", "send_hops_seed", "send_is_seed"]
GRAPH_FEATS = [f for f in FEATURES_NETWORK if f not in SEED_FEATS]
TIERS = {
    "A_behavioural": FEATURES_BEHAV + FEATURES_CONTEXT,
    "B_plus_graph": FEATURES_BEHAV + FEATURES_CONTEXT + GRAPH_FEATS,
    "C_plus_known_mules": ALL_FEATURES,
}
FPR_POINTS = [0.001, 0.005, 0.01]
WARN_FPR, HOLD_FPR = 0.005, 0.001         # default decision thresholds = these FPRs measured on VAL (tune with explore_thresholds.py)
HEED_WARN, HOLD_EFFECT = 0.5, 0.9         # ASSUMPTIONS: share of fraud amount stopped by a warn / by a hold
PARAMS = dict(objective="binary", metric="average_precision", learning_rate=0.05, num_leaves=63,
              min_data_in_leaf=40, feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
              lambda_l2=1.0, verbose=-1, seed=42)

D, M, R = Path("data"), Path("models"), Path("reports")


def load(name):
    p = D / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.read_csv(D / f"{name}.csv", low_memory=False)


def save(df, name):
    try:
        df.to_parquet(D / f"{name}.parquet", index=False)
    except ImportError:
        df.to_csv(D / f"{name}.csv", index=False)


def thr_at_fpr(neg_scores, fpr):
    return float(np.quantile(neg_scores, 1.0 - fpr))


def conf(y, flagged):
    tp = int((flagged & (y == 1)).sum()); fp = int((flagged & (y == 0)).sum())
    fn = int(((~flagged) & (y == 1)).sum()); tn = int(((~flagged) & (y == 0)).sum())
    return dict(precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1), fpr=fp / max(fp + tn, 1), tp=tp, fp=fp)


def fit(feats, tr, va):
    pos = float(tr.label.sum()); neg = len(tr) - pos
    w = np.where(tr.label.values == 1, np.sqrt(neg / pos), 1.0)       # moderate class weight
    dtr = lgb.Dataset(tr[feats].values, tr.label.values, weight=w, feature_name=feats, free_raw_data=False)
    dva = lgb.Dataset(va[feats].values, va.label.values, reference=dtr)
    return lgb.train(PARAMS, dtr, num_boost_round=2000, valid_sets=[dva],
                     callbacks=[lgb.early_stopping(100, verbose=False)])


def main():
    for p in (M, R):
        p.mkdir(exist_ok=True)
    df = load("features")
    for c in ["fraud_type", "hard_type", "incident_id"]:
        df[c] = df[c].fillna("")
    df["split"] = df["split"].astype(str)
    tr, va, te = (df[df.split == s].reset_index(drop=True) for s in ("train", "val", "test"))
    print("=== 1. DATA (time split) ===")
    for n, d in (("train", tr), ("val", va), ("test", te)):
        print(f"{n:5s} days {d.day.min()}-{d.day.max()}  txns {len(d):>8,}  fraud {int(d.label.sum()):>5,}  "
              f"({d.label.mean() * 100:.2f}%)")

    # --------------------------------------------------------------- ablation
    models, s_va, s_te = {}, {}, {}
    for name, feats in TIERS.items():
        t0 = time.time()
        b = fit(feats, tr, va)
        models[name] = (b, feats)
        s_va[name] = b.predict(va[feats].values, num_iteration=b.best_iteration)
        s_te[name] = b.predict(te[feats].values, num_iteration=b.best_iteration)
        print(f"trained {name}: {b.best_iteration} trees in {time.time() - t0:.0f}s")

    yv, yt = va.label.values, te.label.values
    thr = {n: {f: thr_at_fpr(s_va[n][yv == 0], f) for f in FPR_POINTS} for n in TIERS}
    rows = []
    for n in TIERS:
        r = {"model": n, "PR_AUC": average_precision_score(yt, s_te[n]), "ROC_AUC": roc_auc_score(yt, s_te[n])}
        for f in FPR_POINTS:
            c = conf(yt, s_te[n] >= thr[n][f])
            r[f"recall@{f * 100:g}%FPR"] = c["recall"]
            if f == 0.005:
                r["precision@0.5%FPR"] = c["precision"]; r["actual_FPR@0.5%"] = c["fpr"]
        rows.append(r)
    abl = pd.DataFrame(rows).set_index("model")
    print(f"\n=== 2. ABLATION on TEST (thresholds picked on VAL; fraud base rate {yt.mean() * 100:.2f}%) ===")
    print(abl.round(3).to_string())
    abl.round(4).to_csv(R / "ablation.csv")

    # ------------------------------------------------------ per-scenario recall
    fr = te[te.label == 1]
    ps = pd.DataFrame(index=sorted(fr.fraud_type.unique()))
    ps["n_txns"] = fr.groupby("fraud_type").size()
    for n in TIERS:
        flag = pd.Series(s_te[n] >= thr[n][0.005], index=te.index)
        ps[f"recall_{n[0]}"] = fr.assign(f=flag[fr.index]).groupby("fraud_type").f.mean()
    flagC = pd.Series(s_te["C_plus_known_mules"] >= thr["C_plus_known_mules"][0.005], index=te.index)
    ps["incident_recall_C"] = fr.assign(f=flagC[fr.index]).groupby(["fraud_type", "incident_id"]).f.max().groupby("fraud_type").mean()
    print("\n=== 3. PER-SCENARIO RECALL on TEST at 0.5% FPR (A=behavioural, B=+graph, C=+known mules) ===")
    print(ps.round(2).to_string())
    ps.round(4).to_csv(R / "per_scenario.csv")

    # ------------------------------------------------------ hard genuine cases
    flagC_w = pd.Series(s_te["C_plus_known_mules"] >= thr["C_plus_known_mules"][0.01], index=te.index)
    g = te[te.label == 0].copy()
    g["hard"] = g.hard_type.replace("", "ordinary_genuine")
    g["flag"] = flagC_w[g.index]
    hg = g.groupby("hard").agg(n=("flag", "size"), flagged_pct=("flag", lambda s: s.mean() * 100)).sort_values("flagged_pct")
    print("\n=== 4. FALSE POSITIVES BY GENUINE TYPE (model C, test, flagged at 1% FPR threshold) ===")
    print(hg.round(2).to_string())
    hg.round(3).to_csv(R / "hard_genuine.csv")

    # ------------------------------------------------------------ decision layer
    bC, fC = models["C_plus_known_mules"]
    warn_t = thr_at_fpr(s_va["C_plus_known_mules"][yv == 0], WARN_FPR)
    hold_t = thr_at_fpr(s_va["C_plus_known_mules"][yv == 0], HOLD_FPR)
    sc = s_te["C_plus_known_mules"]
    act = np.where(sc >= hold_t, "hold", np.where(sc >= warn_t, "warn", "allow"))
    te["action"] = act
    loss_mask = (te.label == 1) & te.sender.astype(str).str.startswith("U")   # victim-origin legs only
    eff = np.where(act == "hold", HOLD_EFFECT, np.where(act == "warn", HEED_WARN, 0.0))
    prevented = float((te.amount * loss_mask * eff).sum())
    total_loss = float((te.amount * loss_mask).sum())
    gen = te.label == 0
    dec = {
        "warn_t": warn_t, "hold_t": hold_t, "warn_fpr_val": WARN_FPR, "hold_fpr_val": HOLD_FPR,
        "fraud_txns_warned_or_held_pct": float((te.label.eq(1) & (act != "allow")).sum() / te.label.sum() * 100),
        "fraud_txns_held_pct": float((te.label.eq(1) & (act == "hold")).sum() / te.label.sum() * 100),
        "genuine_warned_pct": float((gen & (act == "warn")).sum() / gen.sum() * 100),
        "genuine_held_pct": float((gen & (act == "hold")).sum() / gen.sum() * 100),
        "genuine_value_delayed_inr": float(te.amount[gen & (act == "hold")].sum()),
        "victim_loss_total_inr": total_loss, "victim_loss_prevented_inr": prevented,
        "prevented_pct": prevented / max(total_loss, 1) * 100,
        "assumption_warn_heeded": HEED_WARN, "assumption_hold_effective": HOLD_EFFECT,
    }
    print("\n=== 5. DECISION LAYER on TEST (default thresholds from VAL) ===")
    print(f"warn_t = {warn_t:.4f}  hold_t = {hold_t:.4f}")
    print(pd.crosstab(te.label.map({0: "genuine", 1: "fraud"}), te.action).to_string())
    print(f"fraud txns warned or held: {dec['fraud_txns_warned_or_held_pct']:.1f}%   (held: {dec['fraud_txns_held_pct']:.1f}%)")
    print(f"genuine friction: {dec['genuine_warned_pct']:.2f}% warned, {dec['genuine_held_pct']:.2f}% held")
    print(f"victim loss in test period: INR {total_loss:,.0f} | prevented (assuming {HEED_WARN:.0%} heed a warn, "
          f"{HOLD_EFFECT:.0%} of holds stop the fraud): INR {prevented:,.0f} ({dec['prevented_pct']:.1f}%)")

    # --------------------------------------------- SHAP (LightGBM TreeSHAP) importance
    rng = np.random.default_rng(0)
    fi = te[te.label == 1].index.tolist() + list(rng.choice(te[te.label == 0].index, 5000, replace=False))
    contrib = bC.predict(te.loc[fi, fC].values, pred_contrib=True, num_iteration=bC.best_iteration)[:, :-1]
    imp = pd.Series(np.abs(contrib).mean(0), index=fC).sort_values(ascending=False)
    print("\n=== 6. TOP 10 FEATURES by mean |SHAP| (model C) ===")
    print(imp.head(10).round(3).to_string())
    imp.round(4).to_csv(R / "feature_importance.csv", header=["mean_abs_shap"])
    fi_df = te.loc[fi]
    by_sc = {}
    for ft in sorted(te[te.label == 1].fraud_type.unique()):
        mask = (fi_df.fraud_type == ft).values
        by_sc[ft] = pd.Series(np.abs(contrib[mask]).mean(0), index=fC).sort_values(ascending=False).head(3).round(3).to_dict()
    json.dump(by_sc, open(R / "shap_top_by_scenario.json", "w", encoding="utf-8"), indent=1)

    # --------------------------------------------------------- example explanations
    ex = []
    cands = te[(te.label == 1) | ((te.hard_type != "") & (te.action != "allow"))]
    for key, grp in cands.assign(k=np.where(cands.label == 1, cands.fraud_type, "GENUINE_" + cands.hard_type)).groupby("k"):
        for i in grp.sort_values("amount", ascending=False).head(1).index:
            c = bC.predict(te.loc[[i], fC].values, pred_contrib=True, num_iteration=bC.best_iteration)[0][:-1]
            ex.append({"txn_id": int(te.txn_id[i]), "case": key, "score": float(sc[i]), "action": te.action[i],
                       "amount": float(te.amount[i]),
                       "reasons": explain(c, te.loc[i, fC].values, fC, k=3, user_facing=True)})
    json.dump(ex, open(R / "example_explanations.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ------------------------------------------------------------ model latency
    X1 = te.loc[rng.choice(te.index, 400, replace=False), fC].values
    tp_, tc_ = [], []
    for x in X1:
        t0 = time.perf_counter(); bC.predict(x.reshape(1, -1), num_iteration=bC.best_iteration); tp_.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter(); bC.predict(x.reshape(1, -1), pred_contrib=True, num_iteration=bC.best_iteration); tc_.append((time.perf_counter() - t0) * 1000)
    lat = {"predict_p50_ms": float(np.percentile(tp_, 50)), "predict_p99_ms": float(np.percentile(tp_, 99)),
           "predict_plus_shap_p50_ms": float(np.percentile(tc_, 50)), "predict_plus_shap_p99_ms": float(np.percentile(tc_, 99))}
    print("\n=== 7. MODEL LATENCY (single transaction, ms) ===")
    print(f"score only: p50 {lat['predict_p50_ms']:.2f} p99 {lat['predict_p99_ms']:.2f} | "
          f"score + SHAP reasons: p50 {lat['predict_plus_shap_p50_ms']:.2f} p99 {lat['predict_plus_shap_p99_ms']:.2f}")

    # ------------------------------------------------------------------- save
    for n, (b, ft) in models.items():
        b.save_model(str(M / f"model_{n}.txt"), num_iteration=b.best_iteration)
    bC.save_model(str(M / "final_model.txt"), num_iteration=bC.best_iteration)
    meta = {"features": fC, "tiers": {n: ft for n, (_, ft) in models.items()},
            "best_iteration": {n: b.best_iteration for n, (b, _) in models.items()},
            "thresholds": {"warn_t": warn_t, "hold_t": hold_t}, "decision_layer": dec,
            "model_latency_ms": lat, "params": PARAMS,
            "split_days": {"train": [22, 61], "val": [62, 71], "test": [72, 81]}}
    json.dump(meta, open(M / "model_meta.json", "w", encoding="utf-8"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    json.dump({"ablation": abl.round(4).reset_index().to_dict("records"), "per_scenario": ps.round(4).reset_index().to_dict("records"),
               "hard_genuine": hg.round(3).reset_index().to_dict("records"), "decision_layer": dec, "latency": lat},
              open(R / "metrics.json", "w", encoding="utf-8"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    keep = ["txn_id", "ts", "split", "sender", "receiver", "amount", "label", "fraud_type", "hard_type", "incident_id"]
    out = pd.concat([va[keep], te[keep]], ignore_index=True)
    out["score"] = np.concatenate([s_va["C_plus_known_mules"], s_te["C_plus_known_mules"]])
    out["score_behavioural"] = np.concatenate([s_va["A_behavioural"], s_te["A_behavioural"]])
    out["score_graph"] = np.concatenate([s_va["B_plus_graph"], s_te["B_plus_graph"]])
    save(out, "scores")
    print("\nsaved: models/final_model.txt, models/model_meta.json, reports/*, data/scores")


if __name__ == "__main__":
    main()