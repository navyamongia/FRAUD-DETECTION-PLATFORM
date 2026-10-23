# Real-Time UPI Fraud Alert System: Project Status & Handoff

Hackathon Track 4.3 (FinTech & GovTech). 36-hour build. Paste this file at the start of a new session so work can continue without re-explaining.

## 0. How to work with me (preferences)
- Assume **nothing is installed**. Give code and file names step by step.
- For every command or output, say **which lines of the output matter** (the user usually pastes everything).
- The **frontend is built by a teammate (React)**. Melwin owns backend, model and data. The API contract is the hand-off.
- Priority: show judges, visually, what happens in the backend (decision making), **separately from the <200 ms scoring path**.

## 1. Environment
- Windows, VS Code, PowerShell. Python 3.12.7 in `.venv`. Project folder: `C:\dev\upi-fraud` (moved out of OneDrive to avoid file-sync stalls).
- Activate: `.venv\Scripts\activate`. Libraries in `requirements.txt`: numpy, pandas, pyarrow, scikit-learn, lightgbm, shap, networkx, fastapi, uvicorn[standard], matplotlib.
- Node.js is only needed by the frontend teammate (Vite + React).

## 2. The brief in one paragraph
Score every UPI transaction in real time (<200 ms), using transaction details, user behaviour and account-network patterns. Decide allow / warn / hold with configurable thresholds. Show the user a simple warning in English and a regional language. Provide an analyst dashboard (live alerts, explanations, account network, case management). Evaluation criteria: realism of data and fraud scenarios 15%, detection with low false positives 30%, network-based mule detection 20%, clarity of user warnings and analyst tools 20%, documentation and pitch 15%. Deliverables: working prototype, source + README, evaluation report (precision, recall, FPR, value prevented, latency), 5-7 min pitch.

## 3. Architecture (decided)
```
Replay simulator (test-period txns, adjustable speed)
   -> FastAPI  POST /api/score   [HOT PATH, <200 ms]
        FeatureEngine (in-memory feature store + rolling account graph)
        LightGBM score + top-3 SHAP reasons (pred_contrib)
        Decision layer: warn_t / hold_t + override rules
   -> returns decision + reasons + stage latencies
   -> [EXPLAIN PATH, async, off the hot path]
        full Decision Trace, graph neighbourhood, SQLite writes
        pushed over WebSocket /ws/stream to the React app
React app (teammate): Payment simulator | Live risk dashboard | Investigation workspace
```
- **Backend:** Python FastAPI + uvicorn, WebSocket for live push.
- **DB:** SQLite (`data/app.db`) for decisions, alerts, cases, config. Written by a background writer thread so scoring never waits.
- **Graph rebuild** runs in a background thread. It caused a 221 ms spike when run inline.
- Not used (mention on the "production architecture" slide only): Kafka, Redis, Neo4j, Postgres.

## 4. Files: what exists (all delivered and tested)

| File | Status | Purpose |
|---|---|---|
| `src/generate.py` | Done (v2 with stealth fraud) | Synthetic data: 20k users, 82 days, ~650k txns (503k in the 60-day window), 7 fraud scenarios, 6 hard-genuine types. `python src\generate.py` (`--users N`, `--stealth 0.35`, `--seed 42`) |
| `src/check_data.py` | Done | Sanity and leakage checks on the generated data |
| `src/features.py` | Done | `FeatureEngine`: ONE causal feature engine for training and live scoring. `python src\features.py` writes `data/features.parquet` |
| `src/reasons.py` | Done | SHAP contributions to grouped reasons with en / ta / hi texts. **Tamil and Hindi need native-speaker review** |
| `src/train.py` | Done, **re-run pending** (UTF-8 fix) | LightGBM, time split, 3-tier ablation, per-scenario recall, hard-genuine FPs, decision layer, value prevented, SHAP importance, example explanations, model latency, saves models and scores |
| `src/explore_thresholds.py` | Done, **not yet run on real scores** | Grid of warn/hold settings, to choose defaults |
| `docs/API_CONTRACT.md` | Done, **needs one update** (see 6) | Endpoint + WebSocket contract for the frontend teammate |
| `src/server.py` | **NOT STARTED** | See section 6 |
| `README.md` | **NOT STARTED** | See section 6 |

### Data timeline (generator)
- Day 0 = 2026-08-01. Days 0-21 warm-up (history only). **Train days 22-61, validation 62-71, test 72-81** (split by time, never random).
- Festivals: Ganesh Chaturthi Sep 14-24 (in train), Navratri/Dussehra Oct 11-20 (mostly in test, on purpose).
- Raw fields only are stored (new payee, new device, distance etc. are computed in `features.py`) so offline and live code are identical.

### Fraud scenarios (7) and hard genuine cases (6)
1. fake_collect  2. vishing (call + urgency)  3. screen_share (burst of transfers)  4. account_takeover (new device + new location)  5. qr_swap (shop QR resolves to a mule account)  6. mule_chain (fan-in to fresh accounts, fast fan-out through 3-4 hops)  7. rapid_new_payee.
- 35% of incidents per scenario are **stealthy** (normal-looking amounts, aged mule accounts, slow pass-through, no call flag). Added because the first version was unrealistically easy.
- Hard genuine: festival_spike, travel, new_device, group_collect (society fund fan-in then payout), first_relative, rent_new_payee.
- Flags `call_active` / `screen_share` are noisy: they also occur on genuine payments (2.4% / 0.4%) and are missing on some fraud, so the model cannot just read them.

### Features (33 total, `features.py`)
- Behavioural (15): log_amount, amt_over_median, amt_over_max, hour, hour_dev, is_new_payee, payee_txn_count, is_new_device, dist_km, vel_1h, vel_24h, amt_sum_1h, new_payees_1h, sender_hist_n, sender_age_days.
- Context (5): is_collect, is_qr, call_active, screen_share, qr_mismatch.
- Network (18): receiver age, in/out counts (1h/24h), unique senders, in/out ratio, pass-through time, total inflow, PageRank, component size, hops to known mule seeds, is_seed, and the sender-side equivalents (money-in-then-out pass-through).
- **Causal:** `features(txn)` only uses earlier transactions; `commit(txn)` then adds it. Fraud labels reach the system **24 h late** (a receiver becomes a "known mule seed" only after 24 h). Rolling graph covers the last 6 h and is rebuilt every 600 s of data time (own PageRank, union-find components, BFS to seeds; no networkx or scipy needed).
- Feature latency measured: p50 0.04-0.11 ms, p99 3-9 ms (excluding graph rebuild).

## 5. Results so far (real LightGBM, Melwin's machine; test = days 72-81, thresholds picked on val)

**Ablation**
| Model | PR-AUC | recall @0.1% FPR | recall @0.5% FPR | recall @1% FPR |
|---|---|---|---|---|
| A behavioural only | 0.819 | 0.760 | 0.895 | 0.925 |
| B + graph features | 0.968 | 0.956 | 0.976 | 0.983 |
| C + known-mule seeds | 0.975 | 0.969 | 0.977 | 0.983 |

**Per-scenario recall @0.5% FPR (A / B / C):** account_takeover 1.00/1.00/1.00; fake_collect 0.91/1.00/1.00; mule_chain 0.92/1.00/1.00; qr_swap 0.98/1.00/1.00; **rapid_new_payee 0.73/0.87/0.88 (weakest)**; screen_share 0.93/1.00/1.00; vishing 0.91/1.00/1.00. Incident recall 1.0 for all.

**False positives by genuine type (flagged at 1% FPR):** travel 0.63%, ordinary 1.08%, new_device 3.02%, festival_spike 4.76%, first_relative 13.82%, **group_collect 35.76%**.

**Decision layer (default warn_t = 0.0003, hold_t = 0.0037):** 98.3% of fraud txns warned or held (97.0% held). Genuine friction 1.11% warned + 0.27% held. Victim loss in test INR 1,695,694; prevented INR 1,514,626 (89.3%) under the assumptions below.

**Top SHAP features:** recv_in_total 1.07, recv_age_days 0.69, recv_in_out_ratio 0.53, new_payees_1h 0.45, log_amount 0.35, recv_hops_seed 0.32.

### Value-prevented assumptions (state these in the report)
- A warn stops 50% of the fraud amount (users heed it). A hold stops 90% (verification can be defeated by social engineering).
- Only **victim-originated** fraud legs count as loss. Mule-to-mule hops are excluded so money is not double counted.

## 6. What still needs to be done (priority order)

### P0: do first
1. **Re-run `train.py`** (the UTF-8 fix is in) and **`explore_thresholds.py`**. Check the last line says `saved: models/final_model.txt ...`.
2. **Pick default thresholds.** Finding: with warn at 1% FPR and hold at 0.2% FPR the warn band caught only 7 fraud vs 1,244 genuine (warn precision about 0.6%), so warn is nearly pure friction. Use the `warn_precision%`, `fraud_missed%`, `genuine_warned%` and `prevented%` columns of the explorer to choose, then update `WARN_FPR` / `HOLD_FPR` in `train.py`.
3. **Write `src/server.py`** (suggested: put logic in a testable `engine.py` and keep `server.py` thin):
   - Load `models/final_model.txt`, `model_meta.json`, `FeatureEngine`. Use `Booster.predict` with numpy rows and `pred_contrib=True` for SHAP.
   - Hot path `/api/score`: features, model, SHAP reasons (via `reasons.explain(..., user_facing=True)`), decision, per-stage latency. Target p99 well under 200 ms; **benchmark through the real API** and report p50/p95/p99.
   - Graph rebuild in a background thread (swap the dicts), never inline.
   - **Override rules** (e.g. collect request to a flagged mule goes straight to hold; QR mismatch to hold).
   - **Replay simulator** over test-period transactions with adjustable speed; reveal labels to the engine with the 24 h delay (`observe_label`).
   - WebSocket `/ws/stream`; SQLite tables for decisions, alerts, cases, case events, config; background writer thread.
   - `/api/simulate` and `/api/curve` computed from `data/scores.parquet` in memory (vectorised, milliseconds).
   - Scenario presets and sender personas for the payment simulator (`/api/scenarios`).
   - Graph endpoint: neighbourhood (cap 60 nodes) for `react-force-graph-2d`.
   - Serve the built React app from FastAPI for the final demo (one command).
4. **Update `docs/API_CONTRACT.md`**: the threshold slider must work in "% of genuine payments flagged" (score thresholds are tiny, around 0.0003, because of class weights). Add `warn_pct` / `hold_pct` to `/api/simulate`, `/api/config` and the curve points, plus a note that raw scores are not calibrated probabilities.
5. Give the teammate the API contract plus mock JSON (`reports/example_explanations.json` is a good source) so she can start before the server exists.

### P1
6. **Evaluation report** (all numbers from scripts): ablation, per-scenario recall, hard-genuine FPs, value prevented with assumptions, latency percentiles, threshold-drift note (festival in test), limitations.
7. **README**: setup from scratch, how to run, data generator and **documentation of each fraud scenario**, features, model, decision layer, assumptions, limitations.
8. **Pitch (5-7 min)** and a demo script: payment simulator (normal, rent to new landlord, vishing, fake collect, QR swap), then the live dashboard with the Decision Trace, then the threshold slider, then the investigation workspace and a case.
9. Native-speaker review of Tamil and Hindi in `reasons.py`.

### P2: stretch (from the brief)
10. Senior-citizen protection mode (trusted-contact confirmation for large payments). `users.parquet` already has a `senior` flag and victims are weighted toward seniors.
11. Adaptive model updating as new labels arrive (retrain or incremental on a schedule).
12. Screen-sharing / scam-call detection is already simulated via flags; present it as the stretch goal covered.

## 7. Known issues, risks and honest caveats (use in the pitch)
- Data is **synthetic**: absolute numbers are high. The relative gains (ablation) are the finding. Real traffic would score lower. Say so.
- **Threshold drift:** val-picked thresholds gave about 0.7% actual FPR on test (aimed for 0.5%) because the Dussehra festival is in the test period.
- `rapid_new_payee` is the weakest scenario. `group_collect` genuine payments look like mule fan-in (35.8% flagged).
- Receiver-side network features dominate. The ablation shows the graph features do the work, not the blacklist (seeds add only 0.968 to 0.975 PR-AUC).
- Scam receiver accounts are reused for about 8 days in the generator, which makes known-mule seeds useful after the 24 h delay.
- Latency: a 221 ms spike came from inline graph rebuilds (fixed by moving to a background thread). One 16 s outlier on Melwin's machine was a machine pause (the project was in OneDrive); re-measure from the final API.
- `train.py` crashed once on Windows (`cp1252` cannot write Tamil/Hindi). All JSON writes now use `encoding="utf-8"`. Apply the same to any new file writes that contain Tamil or Hindi.

## 8. Commands cheat sheet (from `C:\dev\upi-fraud`, venv active)
```
python src\generate.py              # ~2 min, writes data\transactions, users, accounts, meta.json
python src\check_data.py            # sanity checks
python src\features.py              # ~2 min, writes data\features, feature_latency.json
python src\train.py                 # ~1-3 min, writes models\*, reports\*, data\scores
python src\explore_thresholds.py    # warn/hold grid
# later:
uvicorn server:app --app-dir src --port 8000      # http://localhost:8000/docs
```
Order matters: generate, then features, then train. Re-run all three after any change to `generate.py`.

## 9. Suggested remaining 36-hour schedule
| Block | Work |
|---|---|
| Next | Re-run train, choose thresholds, write `server.py` core and scorer, test latency |
| Then | Replay + WebSocket + SQLite + cases + graph endpoint; update the API contract; teammate integrates |
| Then | Evaluation report, README, pitch and demo rehearsal |
| Last | Freeze: final latency benchmark on a quiet machine, two full demo run-throughs, buffer |
