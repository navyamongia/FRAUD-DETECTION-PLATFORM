# Real-Time UPI Fraud Alert System

Scores every UPI transaction in real time, decides **allow / warn / hold**, explains the decision in plain English, Tamil and Hindi, and gives analysts a live dashboard with account-network (mule) investigation.

Built for **Hackathon Track 4.3 (FinTech & GovTech)**.

> **Note on data:** all data is synthetic, produced by `src/generate.py`. Absolute scores are therefore much higher than real traffic would give. The meaningful result is the *relative* gain from each model tier (see [Results](#results)).

---

## Contents

1. [What it does](#what-it-does)
2. [Architecture](#architecture)
3. [Project structure](#project-structure)
4. [Setup](#setup)
5. [Running the pipeline](#running-the-pipeline)
6. [Running the app](#running-the-app)
7. [Raspberry Pi payment terminal demo](#raspberry-pi-payment-terminal-demo)
8. [Synthetic data and fraud scenarios](#synthetic-data-and-fraud-scenarios)
9. [Features](#features)
10. [Model and decision layer](#model-and-decision-layer)
11. [Results](#results)
12. [API overview](#api-overview)
13. [Assumptions and limitations](#assumptions-and-limitations)

---

## What it does

- **Real-time scoring** of each transaction (target < 200 ms) using transaction details, the sender's behaviour, and account-network patterns.
- **Three-way decision** with configurable thresholds:
  - `allow`: payment settles.
  - `warn`: the user sees a simple warning and can cancel or proceed.
  - `hold`: the payment is paused until the user verifies (simulated OTP / trusted contact).
- **Explainable warnings** for users: up to 3 reasons, shown in English, Tamil (`ta`) or Hindi (`hi`).
- **Analyst dashboard**: live alert stream, per-transaction *Decision Trace* (features → graph → model + SHAP → thresholds → action, with milliseconds per stage), threshold slider with live precision / recall / friction, account-network graph, and case management.
- **Network-based mule detection**: rolling account graph with PageRank, connected components, pass-through timing and hop distance to known mule accounts.
- **Evaluation view** for judges: ablation, per-scenario recall, false positives on hard genuine cases, and value prevented.

## Architecture

```
Replay simulator (test-period transactions, adjustable speed)
   -> FastAPI  POST /api/score                [HOT PATH]
        FeatureEngine (in-memory feature store + rolling account graph)
        LightGBM score + top-3 SHAP reasons (pred_contrib)
        Decision layer: warn / hold thresholds + override rules
   -> returns decision + reasons + per-stage latency
   -> [EXPLAIN PATH, off the hot path]
        full Decision Trace, graph neighbourhood, SQLite writes
        pushed over WebSocket /ws/stream to the React app

React app: Demo control | Payment simulator | Live dashboard | Investigation | Evaluation
```

Key design choices:

- **One feature engine for training and serving.** `FeatureEngine` is causal: `features(txn)` only uses earlier transactions, then `commit(txn)` adds the transaction to state. Offline and live code paths are identical, which avoids train/serve skew.
- **Delayed labels.** Fraud labels reach the system 24 h late; a receiver becomes a "known mule seed" only after that delay.
- **Graph rebuild runs in a background thread**, never inline. (An inline rebuild caused a 221 ms spike.)
- **SQLite writes happen on a background writer thread**, so scoring never waits on the database.
- Not used, but would be the production choices: Kafka, Redis, Neo4j, Postgres.

## Project structure

```
upi-fraud/
├── src/
│   ├── generate.py            # synthetic users, accounts, transactions, fraud scenarios
│   ├── check_data.py          # sanity and leakage checks on generated data
│   ├── features.py            # FeatureEngine (shared by training and live scoring)
│   ├── train.py               # LightGBM, ablation, decision layer, reports, saved models
│   ├── explore_thresholds.py  # grid of warn/hold settings to choose defaults
│   ├── reasons.py             # SHAP -> grouped reasons with en / ta / hi text
│   ├── engine.py              # scoring engine: model, thresholds, overrides, replay, simulate
│   ├── store.py               # SQLite store (decisions, alerts, cases, config)
│   ├── server.py              # FastAPI app: REST + WebSocket, serves built frontend
│   ├── demo.py                # payment-terminal demo sessions (Pi + phone + dashboard)
│   └── selftest.py            # engine test without the web server + latency benchmark
├── frontend/                  # React + Vite app
├── demo_web/                  # terminal.html (Pi screen) and phone.html (customer phone)
├── pi_terminal/terminal.py    # pygame payment terminal for a Raspberry Pi
├── data/                      # generated parquet files, app.db
├── models/                    # trained LightGBM models + model_meta.json
├── reports/                   # metrics, ablation, SHAP importance, threshold grid, etc.
├── docs/
│   ├── API_CONTRACT.md        # endpoint and WebSocket contract
│   └── PROJECT_STATUS.md      # build notes and handoff
├── check.py                   # quick check that the key libraries import
└── requirements.txt
```

## Setup

**Requirements:** Python 3.12 and (for the frontend) Node.js 18+.

```bash
# from the project folder
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python check.py          # should print: OK <lightgbm version> <shap version>
```

Frontend (only needed for development or to build the UI):

```bash
cd frontend
npm install
```

> Tip: keep the project folder out of cloud-sync folders such as OneDrive. File-sync stalls caused latency outliers during development.

## Running the pipeline

The repository ships with generated data and trained models in `data/` and `models/`. To regenerate everything from scratch, run the steps **in this order**:

```bash
python src/generate.py             # ~2 min  -> data/transactions, users, accounts, meta.json
python src/check_data.py           # sanity and leakage checks
python src/features.py             # ~2 min  -> data/features.parquet, feature_latency.json
python src/train.py                # ~1-3 min -> models/*, reports/*, data/scores.parquet
python src/explore_thresholds.py   # optional: warn/hold grid for choosing defaults
```

Re-run all three of `generate` → `features` → `train` after any change to `generate.py`.

`generate.py` options: `--users N` (default 20,000), `--stealth 0.35`, `--seed 42`.

To test the engine without the web server and measure hot-path latency:

```bash
python src/selftest.py             # replays 20,000 test transactions
python src/selftest.py 50000       # more
```

## Running the app

### Production-style (one command)

```bash
cd frontend && npm run build && cd ..
python src/server.py
```

Open **http://127.0.0.1:8000**. The server serves the built React app from `frontend/dist`; interactive API docs are at **http://127.0.0.1:8000/docs**.

Start-up takes about 15–40 seconds, because the server replays history to warm up the feature store.

Equivalent: `uvicorn server:app --app-dir src --port 8000`.

### Development (hot reload)

```bash
# terminal 1
python src/server.py

# terminal 2
cd frontend
npm run dev                        # http://localhost:5173
```

The Vite dev server proxies `/api` and `/ws` to `127.0.0.1:8000`.

### Environment variables (server)

| Variable | Default | Effect |
|---|---|---|
| `AUTOSTART` | unset | Set to `1` to start the transaction replay automatically |
| `SPEED` | `600` | Replay speed when `AUTOSTART=1` |
| `KEEP_DB` | unset | Set to `1` to keep `data/app.db` between runs (otherwise it is reset) |
| `RESULT_SECONDS` | `10` | How long the Pi terminal shows the result |
| `MULE_ACCOUNT` | `Z022921` | Receiver account used by the "mule" demo scenario |

### Frontend views

| Route | View |
|---|---|
| `/demo` | Demo control: pick a scenario for the payment-terminal demo |
| `/simulator` | Phone-style payment simulator with warnings in en / ta / hi |
| `/dashboard` | Live alerts, Decision Trace, latency, threshold slider |
| `/investigate` | Account network graph and case management |
| `/evaluation` | Ablation, per-scenario recall, value prevented |

## Raspberry Pi payment terminal demo

A hardware-style demo shows the full loop: a shop terminal displays a payment QR, the customer scans it on a phone and pays, the system scores it, and the terminal shows success or failure while the dashboard shows the decision live.

1. Run the backend on a laptop reachable by the Pi and the phone (same network or hotspot).
2. **Pi:** `python3 pi_terminal/terminal.py` (pygame full-screen app).
   - `SERVER=http://<laptop-ip>:8000` (default `http://192.168.137.1:8000`)
   - `SIZE=480x320` to draw at a logical size and scale to the screen
   - `WINDOWED=1` to run in a window for testing
   - Keys: `Esc` / `Q` quits; touch or click on a result screen returns to idle.
3. **Browser alternative to the Pi:** open `http://<laptop-ip>:8000/terminal`.
4. **Phone:** scan the QR shown on the terminal; it opens `/p/<session>`.
5. **Laptop:** on the `/demo` page, choose a preset:
   - *Normal payment*: no warning; terminal shows success.
   - *Risky payment (alert)*: phone shows a warning; Continue → success, Cancel → failed.
   - *Mule account (hold)*: the shop QR was swapped to a mule account; payment is held and the dashboard shows the mule network.

## Synthetic data and fraud scenarios

`generate.py` simulates 20,000 users over 82 days (about 650k transactions). Day 0 is 2026-08-01.

| Period | Days | Use |
|---|---|---|
| Warm-up | 0–21 | History only |
| Train | 22–61 | Model training |
| Validation | 62–71 | Threshold selection |
| Test | 72–81 | Evaluation and live replay |

The split is **by time, never random**. Festivals are included on purpose: Ganesh Chaturthi (Sep 14–24, in train) and Navratri/Dussehra (Oct 11–20, mostly in test) to test threshold drift.

### Fraud scenarios (7)

| Scenario | Description |
|---|---|
| `fake_collect` | Victim is tricked into approving a collect request |
| `vishing` | Phone-call scam with urgency |
| `screen_share` | Victim shares screen; burst of transfers |
| `account_takeover` | New device and new location |
| `qr_swap` | A shop QR resolves to a mule account |
| `mule_chain` | Fan-in to fresh accounts, fast fan-out through 3–4 hops |
| `rapid_new_payee` | Rapid payments to new payees |

About **35% of incidents in each scenario are "stealthy"**: normal-looking amounts, aged mule accounts, slow pass-through and no call flag. This was added because the first version was unrealistically easy.

The `call_active` / `screen_share` flags are deliberately noisy. They also occur on genuine payments (about 2.4% and 0.4%) and are missing on some fraud, so the model cannot simply read them.

### Hard genuine cases (6)

Legitimate behaviour that looks suspicious, used to measure false positives: `festival_spike`, `travel`, `new_device`, `group_collect` (society fund fan-in then payout), `first_relative` (first payment to a relative), `rent_new_payee`.

Only raw fields are stored. Derived values (new payee, new device, distance, etc.) are computed in `features.py`, so offline and live code are identical.

## Features

38 model features in three groups (the full list is in `models/model_meta.json`).

- **Behavioural:** `log_amount`, `amt_over_median`, `amt_over_max`, `hour`, `hour_dev`, `is_new_payee`, `payee_txn_count`, `is_new_device`, `dist_km`, `vel_1h`, `vel_24h`, `amt_sum_1h`, `new_payees_1h`, `sender_hist_n`, `sender_age_days`
- **Context:** `is_collect`, `is_qr`, `call_active`, `screen_share`, `qr_mismatch`
- **Network (receiver and sender side):** receiver account age, in/out counts (1 h / 24 h), unique senders, in/out ratio, pass-through time, total inflow, PageRank, component size, hops to known mule seeds, seed flag, plus sender-side money-in-then-out pass-through features

The rolling graph covers the last 6 hours and is rebuilt every 600 s of data time, using its own PageRank, union-find components and BFS to seeds (no networkx or scipy needed at serving time).

## Model and decision layer

- **Model:** LightGBM, trained on the train period, thresholds chosen on validation, reported on test.
- **Three-tier ablation:**
  - **A:** behavioural + context features only
  - **B:** A + graph features
  - **C:** B + known-mule seeds (delayed labels). This is the final model.
- **Explanations:** SHAP contributions (`pred_contrib`) are grouped into user-facing reasons (`AMOUNT`, `NEW_PAYEE`, `NEW_DEVICE`, `LOCATION`, `TIME`, `BURST`, `COLLECT`, `CALL`, `SCREEN`, `QR`, `NEW_ACCOUNT`, `MULE_PATTERN`, `KNOWN_MULE`, `PASSTHROUGH`) with en / ta / hi text in `src/reasons.py`.
- **Decision layer:** `score ≥ hold_t` → hold; `score ≥ warn_t` → warn; else allow. Defaults are the score thresholds giving 0.5% (warn) and 0.1% (hold) false-positive rate on the validation set. The dashboard slider works in "% of genuine payments flagged" because raw scores are not calibrated probabilities (class weighting makes them very small).
- **Override rules** (can be toggled): a collect request to an account linked to known mules goes straight to hold (`collect_to_flagged_mule`); a QR mismatch against a new account (< 14 days) goes to hold (`qr_swap_new_account`).

## Results

Test period (days 72–81), generated by `src/train.py`; full tables in `reports/`.

### Ablation

| Model | PR-AUC | Recall @ 0.1% FPR | Recall @ 0.5% FPR | Recall @ 1% FPR |
|---|---|---|---|---|
| A: behavioural only | 0.819 | 0.760 | 0.895 | 0.925 |
| B: + graph features | 0.968 | 0.956 | 0.976 | 0.983 |
| C: + known-mule seeds | 0.975 | 0.969 | 0.977 | 0.983 |

The graph features carry most of the gain; the known-mule blacklist adds comparatively little.

### Per-scenario recall @ 0.5% FPR (A / B / C)

| Scenario | A | B | C |
|---|---|---|---|
| account_takeover | 1.00 | 1.00 | 1.00 |
| fake_collect | 0.91 | 1.00 | 1.00 |
| mule_chain | 0.92 | 1.00 | 1.00 |
| qr_swap | 0.98 | 1.00 | 1.00 |
| screen_share | 0.93 | 1.00 | 1.00 |
| vishing | 0.91 | 1.00 | 1.00 |
| rapid_new_payee | 0.73 | 0.87 | 0.88 |

Incident-level recall (at least one transaction of an incident caught) is 1.0 for every scenario.

### False positives on hard genuine payments (flagged at 1% FPR)

| Type | Flagged |
|---|---|
| travel | 0.63% |
| ordinary genuine | 1.08% |
| new_device | 3.02% |
| festival_spike | 4.76% |
| first_relative | 13.82% |
| group_collect | 35.76% |

### Decision layer (default thresholds)

| Metric | Value |
|---|---|
| Fraud transactions warned or held | 97.7% (96.9% held) |
| Genuine payments warned / held | 0.56% / 0.12% |
| Victim loss in test period | INR 1,695,694 |
| Loss prevented | INR 1,513,183 (89.2%) |

### Latency

| Stage | p50 | p99 |
|---|---|---|
| Feature computation (excl. graph rebuild) | 0.11 ms | 5.1 ms |
| Model prediction | 0.17 ms | 1.1 ms |
| Prediction + SHAP | 6.5 ms | 15.9 ms |

The 200 ms budget applies to the scoring path. Measure end-to-end API latency on your own machine with `python src/selftest.py` and the `latency_ms` field of `/api/score` and `/api/stats`.

### Value-prevented assumptions

- A **warn** stops 50% of the fraud amount (some users heed the warning).
- A **hold** stops 90% (verification can still be defeated by social engineering).
- Only **victim-originated** fraud legs count as loss; mule-to-mule hops are excluded to avoid double counting.

## API overview

Full contract with example payloads: [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md). Interactive docs at `/docs` when the server runs.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Model and replay status |
| `GET/POST /api/config` | Read / change warn and hold thresholds, toggle overrides |
| `GET/POST /api/replay` | Start, pause, change speed of the transaction replay |
| `GET /api/stats` | Counts, latency percentiles, known outcomes |
| `GET /api/scenarios` | Preset payments for the demo |
| `POST /api/score` | Score one payment (hot path) |
| `POST /api/payments/{txn_id}/respond` | User response: proceed / cancel / verified / failed_verification |
| `GET /api/alerts` | Recent warn / hold alerts |
| `GET /api/trace/{txn_id}` | Decision Trace for one transaction |
| `GET /api/simulate` | Threshold slider: precision, recall, friction, value prevented |
| `GET /api/curve` | Precision / recall / friction curve |
| `GET /api/evaluation` | Evaluation results for the Evaluation tab |
| `GET /api/graph/{account_id}` | Account neighbourhood (capped at 60 nodes) |
| `POST/GET /api/cases`, `GET/PATCH /api/cases/{id}` | Case management |
| `/api/demo/*` | Payment-terminal demo sessions, QR codes, presets |
| `WS /ws/stream` | Live push: `txn_scored`, `alert`, `stats`, `config_changed`, `case_update`, `replay_state` |

## Assumptions and limitations

- **Synthetic data.** Absolute numbers are optimistic; real traffic would score lower. Read the ablation as the finding, not the headline recall.
- **Threshold drift.** Thresholds picked on validation gave roughly 0.7% actual FPR on test (aimed for 0.5%), because the Navratri/Dussehra festival falls in the test period.
- **Weakest scenario:** `rapid_new_payee` (recall 0.88 at 0.5% FPR).
- **Hardest genuine case:** `group_collect` payments look like mule fan-in, so 35.8% are flagged at 1% FPR.
- **Receiver-side network features dominate** (top SHAP features include `recv_in_total`, `recv_age_days`, `recv_in_out_ratio`, `new_payees_1h`). In the generator, scam receiver accounts are reused for about 8 days, which makes known-mule seeds useful after the 24 h label delay.
- **Warn band is mostly friction.** The warn band alone has low precision, so the warn threshold is a trade-off between user friction and recall. Use the dashboard slider or `explore_thresholds.py` to tune it.
- **Translations need review.** Tamil and Hindi reason texts in `src/reasons.py` should be checked by native speakers before any real use.
- **Verification is simulated.** The `hold` flow's OTP / trusted-contact step is a simulation.
- **Windows encoding.** Any file write containing Tamil or Hindi must use `encoding="utf-8"` (the default `cp1252` crashes).
- **Stretch goals.** The `senior` flag exists in `users.parquet` and victims are weighted toward seniors, but a trusted-contact confirmation mode for senior citizens and adaptive model retraining are not implemented. Screen-share and scam-call detection are simulated through the `call_active` / `screen_share` flags.
