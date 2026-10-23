# UPI Fraud Alert System: Frontend Integration Guide (v2)

For the frontend developer. Everything the UI needs is behind one backend at `http://localhost:8000`.
You never touch the model or the data: call these endpoints and listen to one WebSocket.
The backend is built and follows this contract. Open **http://localhost:8000/docs** for an interactive page where
every endpoint can be tried without any UI.

- All bodies are JSON. CORS is enabled for `http://localhost:5173` (Vite) and `http://localhost:3000`.
- Money is INR. Times are ISO 8601 strings in **simulated time** (the replay runs in fast-forward), except case `created_at`/`updated_at`/event `ts`, which are real clock time.
- Languages: `"en"`, `"ta"` (Tamil), `"hi"` (Hindi). Reason texts always arrive in all three, so the language toggle needs no new request.
- Any numeric field can be `null`; handle it.
- Priority tags: **P0** = needed for the demo, **P1** = strongly wanted.

## 0. Key ideas

- **Two paths.** Scoring (`POST /api/score`, and every replayed transaction) is the fast path: it returns in a few ms and shows a latency number. The **Decision Trace** (`GET /api/trace/{txn_id}`) is the "look inside the backend" view and is computed separately, on request.
- **Risk score 0-100.** Raw model scores are tiny, uncalibrated numbers (like 0.0003). Never show them to users. Use `risk` (0-100, higher = riskier) and `flag_pct` ("only X% of genuine payments score this high"). Colour by `action`, not by `risk`.
- **The threshold slider works in `warn_pct` / `hold_pct`** = "% of genuine payments that get a warning / get held". Typical range 0.01 to 5. `hold_pct` must be <= `warn_pct`. Lower numbers = fewer interruptions.
- **Replay vs simulator.** The *replay* streams historical test-period transactions at a chosen speed (the live dashboard). The *payment simulator* scores a single typed payment. Simulator payments never change the history, so clicking the same scenario twice gives the same answer, and they also appear on the dashboard with `source: "simulator"`.
- **Demo-only truth.** Replayed items carry `truth: { fraud, type, hard }`, the real label from the simulator. Use it only to colour true/false alarms for judges (a "demo mode" toggle). A real system would not have it.

## 1. The views and what feeds them

| View | Shows | Endpoints |
|---|---|---|
| **Payment simulator** (phone screen) | Pick a scenario, get allow / warn / hold, warning in the user's language, Proceed / Cancel / Verify | `GET /api/scenarios`, `POST /api/score`, `POST /api/payments/{txn_id}/respond` |
| **Live risk dashboard** | Streaming alerts, why each fired, decisions, latency, threshold slider with precision / recall / friction curve, replay controls | WS `/ws/stream`, `GET /api/alerts`, `GET /api/trace/{txn_id}`, `GET /api/stats`, `GET/POST /api/replay`, `GET /api/simulate`, `GET /api/curve`, `GET/POST /api/config` |
| **Investigation workspace** | Account network around an alert, case management | `GET /api/graph/{account_id}`, `/api/cases...` |
| **Evaluation** tab (for judges) | Ablation, per-scenario recall, false positives, value prevented | `GET /api/evaluation` |

## 2. WebSocket: live stream  (P0)

`ws://localhost:8000/ws/stream`. Messages are `{ "type": ..., "data": ... }`. On connect you immediately receive
`config_changed`, `replay_state` and `stats`.

| type | when | data |
|---|---|---|
| `txn_scored` | every scored transaction. Routine `allow` events are throttled to about 600/s; alerts are never dropped. **Batch your React state updates (flush every ~250 ms).** | `TxnScored` |
| `alert` | action is `warn` or `hold` | `Alert` |
| `stats` | once per second | `Stats` |
| `config_changed` | thresholds or override switch changed | `Config` |
| `replay_state` | replay started / paused / speed changed | `ReplayState` |
| `case_update` | a case was changed | `Case` |

```json
{ "type": "txn_scored", "data": {
  "txn_id": 412233, "ts": "2026-10-12T14:03:22", "sender": "U00412", "receiver": "Z001977",
  "amount": 18500, "score": 0.0712, "risk": 91.5, "action": "hold", "source": "replay",
  "latency_ms": 0.9, "truth": { "fraud": true, "type": "vishing", "hard": "" } } }
```

## 3. REST endpoints

### 3.1 Payment simulator

**`GET /api/scenarios`  (P0)** - demo buttons. `expected` is the intended outcome, `observed` is what the model actually decided at startup.
```json
[ { "id": "vishing_call", "title": "Large transfer to a new account while on a call", "expected": "hold",
    "observed": "hold", "risk": 100.0,
    "payment": { "sender": "U00412", "receiver": "Z001977", "amount": 8446, "channel": "p2p", "call_active": true } } ]
```
IDs: `normal_grocery`, `rent_new_landlord`, `first_time_relative`, `vishing_call`, `fake_collect`, `qr_swap`, `account_takeover`, `screen_share`.

**`POST /api/score`  (P0)** - score one payment.
```json
{ "scenario_id": "vishing_call" }
```
or a custom payment (`sender`, `receiver`, `amount` required; `channel` is `"p2p" | "qr" | "collect"`):
```json
{ "sender": "U00412", "receiver": "Z001977", "amount": 18500, "channel": "p2p", "call_active": true, "screen_share": false }
```
Response (`ScoreResult`):
```json
{ "txn_id": 9000001, "ts": "2026-10-12T14:03:22", "sender": "U00412", "receiver": "Z001977", "amount": 18500,
  "channel": "p2p", "score": 0.0712, "risk": 91.5, "flag_pct": 0.012, "action": "hold", "override": null,
  "thresholds": { "warn_t": 0.0016, "hold_t": 0.0178 },
  "reasons": [ { "id": "KNOWN_MULE", "weight": 1.9, "text": { "en": "...", "ta": "...", "hi": "..." } } ],
  "latency_ms": { "features": 0.12, "model": 0.66, "decision": 0.005, "explain": 0.12, "total": 0.95 },
  "next_step": "verify", "source": "simulator", "alert_id": 311 }
```
- `action`: `"allow" | "warn" | "hold"`. `reasons`: up to 3, strongest first, user-friendly; empty for `allow`.
- `override`: `null`, or the rule that forced a hold (`collect_to_flagged_mule`, `qr_swap_new_account`, `screen_share_new_payee_large`). Show a badge. Rules only ever escalate to hold.
- `next_step`: `"settle"` (allow), `"confirm_or_cancel"` (warn), `"verify"` (hold).
- `latency_ms.total` is the number to show as "scored in X ms" (budget 200 ms).
- `alert_id` is present only when flagged.
- Errors: 400 if required fields are missing, 404 for an unknown `scenario_id`.

**`POST /api/payments/{txn_id}/respond`  (P0)** - what the user did after the screen.
```json
{ "response": "proceed" }      // "proceed" (warn) | "cancel" | "verified" (hold) | "failed_verification" (hold)
// -> { "status": "settled" | "cancelled" | "released" | "rejected", "message": "Payment of INR 18,500 cancelled." }
```
`proceed` on a held payment returns 400 ("must be verified first").

### 3.2 Live dashboard

**Replay control  (P0)** `GET /api/replay`, `POST /api/replay`
```json
{ "action": "start", "speed": 600 }       // action: "start" | "pause" | "stop" | "speed"
// -> { "running": true, "speed": 600, "finished": false, "replay_ts": "2026-10-12T11:26:58", "progress_pct": 2.66 }
```
`speed` = simulated seconds per real second (default 600, range 1 to 20000; 600 is about 80 txns/s). `stop` is the same as `pause`. The replay cannot rewind: once `finished` is true the backend must be restarted for a clean run.

**`GET /api/alerts?limit=50&action=hold&status=open`  (P0)** - newest first; `action` (`warn|hold`) and `status` (`open` = not in a case, `in_case`) optional.
```json
[ { "alert_id": 311, "txn_id": 412233, "ts": "...", "sender": "U00412", "receiver": "Z001977", "amount": 18500,
    "score": 0.0712, "risk": 91.5, "action": "hold", "override": null, "case_id": null, "source": "replay",
    "reasons": [ { "id": "NEW_ACCOUNT", "weight": 1.1, "text": { "en": "...", "ta": "...", "hi": "..." } } ],
    "truth": { "fraud": true, "type": "vishing", "hard": "" } } ]
```
Analyst reasons may include `PASSTHROUGH` (English only): "sender received money moments ago and is forwarding it".

**`GET /api/trace/{txn_id}`  (P0)** - the Decision Trace. Only the most recent 20,000 scored transactions are kept; older ones return 404.
```json
{ "txn": { "txn_id": 412233, "ts": "...", "sender": "U00412", "receiver": "Z001977", "amount": 18500, "channel": "p2p",
           "device_id": "D412a", "lat": 13.08, "lon": 80.27, "qr_merchant": "", "call_active": true, "screen_share": false },
  "features": [ { "name": "amt_over_median", "group": "behavioural", "value": 21.4, "normal": 1.13 } ],
  "shap": { "bias": -5.1, "raw_logodds": 3.2, "score": 0.0712,
            "contributions": [ { "feature": "recv_age_days", "group": "network", "value": 2.1, "shap": 2.31 } ] },
  "graph": { "pagerank_rel": 4.2, "component_size": 14, "hops_to_known_mule": 2, "receiver_is_known_mule": false,
             "receiver_age_days": 2.1, "receiver_unique_senders_24h": 6 },
  "decision": { "score": 0.0712, "risk": 91.5, "flag_pct": 0.012, "warn_t": 0.0016, "hold_t": 0.0178,
                "override": null, "action": "hold" },
  "reasons": [ ],
  "stages_ms": [ { "stage": "features", "ms": 0.12 }, { "stage": "model", "ms": 0.66 }, { "stage": "decision", "ms": 0.005 },
                 { "stage": "reasons", "ms": 0.12 }, { "stage": "full_shap (trace)", "ms": 0.8 } ],
  "total_ms": 0.95, "source": "replay",
  "ground_truth": { "fraud": true, "type": "vishing", "hard": "" } }
```
- `features` lists all 33 features: `group` is `"behavioural" | "context" | "network"` (colour code them); `normal` is the typical value for genuine payments (for "this is 20x normal").
- `shap.contributions` are the top 15 by absolute value, positive = pushes toward fraud. Draw a waterfall from `bias`.
- `hops_to_known_mule`: 0 = known mule, 9 = no link. `stages_ms`: draw as a timeline; `full_shap (trace)` is computed for the trace itself and is not part of the scoring time.
- `ground_truth` exists only for replayed items.

**`GET /api/stats`  (P0)** (also pushed every second)
```json
{ "replay_ts": "...", "txns_scored": 18234, "txns_per_sec": 220.4, "actions": { "allow": 17890, "warn": 210, "hold": 134 },
  "latency_ms": { "p50": 0.76, "p95": 1.4, "p99": 2.5, "budget": 200 },
  "known_outcomes": { "fraud_caught": 120, "fraud_missed": 4, "false_alarms": 224 } }
```
Show the latency chart with a line at the 200 ms budget.

**`GET /api/simulate?warn_pct=0.5&hold_pct=0.1`  (P0)** - the **threshold slider**. Recomputes the trade-off on the held-out test period in a few ms (debounce ~100 ms). You can also pass `warn_t` / `hold_t` (raw scores) instead.
```json
{ "warn_pct": 0.5, "hold_pct": 0.1, "warn_t": 0.0016, "hold_t": 0.0178,
  "precision": 0.44, "recall": 0.976, "fpr": 0.0063, "warn_precision": 0.004, "hold_precision": 0.82,
  "friction": { "warned_pct": 0.53, "held_pct": 0.11, "genuine_per_1000_flagged": 6.3 },
  "fraud_caught": 560, "fraud_total": 574, "genuine_flagged": 720,
  "fraud_prevented_inr": 1512964, "fraud_total_inr": 1695694,
  "assumptions": { "warn_heeded": 0.5, "hold_effective": 0.9 },
  "note": "Score thresholds only; override rules are not included." }
```
Suggested slider display: two linked handles for `warn_pct` and `hold_pct`; live numbers for recall ("fraud caught"), `friction.warned_pct`/`held_pct` ("genuine users interrupted"), and `fraud_prevented_inr`. Errors: 400 if hold_pct > warn_pct.

**`GET /api/curve`  (P0)** - 60 points for the precision / recall / friction curve; mark the current slider position on it.
```json
{ "points": [ { "flag_pct": 0.01, "threshold": 0.31, "precision": 0.97, "recall": 0.93, "fpr": 0.0001 } ] }
```
`flag_pct` runs 0.01 to 5 (log-spaced); plot `flag_pct` on a log x-axis.

**`GET /api/config` / `POST /api/config`  (P0)** - read or change the live decision thresholds. A change applies to every decision from then on.
```json
// GET -> 
{ "warn_t": 0.0016, "hold_t": 0.0178, "warn_pct": 0.5, "hold_pct": 0.1, "overrides_enabled": true,
  "overrides": [ { "name": "collect_to_flagged_mule", "description": "Collect request to an account linked to known mules" } ],
  "languages": ["en", "ta", "hi"] }
// POST body (any subset): { "warn_pct": 0.5, "hold_pct": 0.1, "overrides_enabled": true }   (or warn_t / hold_t)
```
Add an "override rules" switch: turning it off shows judges what the rules add.

**`GET /api/evaluation`  (P1)** - results for a judges' tab. Keys: `ablation[]` (rows per model: A behavioural, B plus graph, C plus known mules, with PR_AUC, ROC_AUC, recall at fixed false-positive rates), `per_scenario[]`, `hard_genuine[]` (false-positive rate on tricky genuine payments), `decision_layer`, `latency`, `shap_top_by_scenario`.

### 3.3 Investigation workspace

**`GET /api/graph/{account_id}?hops=2`  (P0)** - small network (max 60 nodes) around an account. Works with react-force-graph-2d directly. 404 if the account has no recent activity.
```json
{ "center": "Z001977",
  "nodes": [ { "id": "Z001977", "kind": "user", "age_days": 2.1, "in_deg": 11, "out_deg": 3, "is_known_mule": false, "risk": 0.93 } ],
  "edges": [ { "source": "U00412", "target": "Z001977", "amount": 18500, "ts": "...", "count": 1 } ] }
```
`kind` is `"user" | "merchant"` only (the system does not know which accounts are mules). `risk` is 0-1 from the model's score on that account's incoming payments (colour scale); `is_known_mule` = already confirmed/reported (draw an outline); `age_days` and degrees suit tooltips or node size. Alert -> take `receiver` -> graph.

**Cases  (P0)**
- `POST /api/cases` body `{ "alert_ids": [311], "title": "Possible mule chain", "assignee": "analyst1" }` -> `Case`
- `GET /api/cases?status=open` -> `Case[]`; `GET /api/cases/{case_id}` -> `Case`
- `PATCH /api/cases/{case_id}` body (any subset) `{ "status": "investigating", "assignee": "analyst1", "note": "linked 3 receivers", "alert_ids": [312] }` -> `Case`
- `status`: `"open" | "investigating" | "confirmed_fraud" | "false_positive" | "closed"` (400 otherwise)
```json
{ "case_id": 7, "title": "Possible mule chain", "status": "investigating", "assignee": "analyst1",
  "created_at": "2026-10-23T10:15:02", "updated_at": "...", "alert_ids": [311, 312],
  "events": [ { "ts": "...", "by": "analyst1", "note": "Case opened", "status": "open" } ] }
```
An alert in a case gets `case_id` set (`status=in_case` filter).

## 4. The phone-app screen

| State | Show |
|---|---|
| Payment form | Payee, amount, Pay button. Language toggle English / தமிழ் / हिन्दी. A scenario picker from `/api/scenarios` is fine for the demo. |
| `allow` | "Processing..." then a success tick. |
| `warn` | Amber card. Headline in the chosen language, then `reasons[].text[lang]`, then **Cancel payment** (primary) and **Proceed anyway** (secondary). |
| `hold` | Red card: "We paused this payment to protect you." Same reasons. **Verify** (simulated OTP / trusted-contact approval) and **Cancel**. |
| Result | The `message` from `/respond`. |

Large simple text: the audience includes first-time digital users and senior citizens.

## 5. Dashboard tips

- At high replay speed, buffer `txn_scored` and flush to state about every 250 ms.
- Clicking an alert opens the Decision Trace panel, with buttons "Investigate" (graph of `receiver`) and "Create case".
- Reconnect the WebSocket automatically if it drops.
- Suggested libraries: React + Vite, `recharts`, `react-force-graph-2d`, `react-router-dom`.

## 6. Running it together

1. Backend, from `C:\dev\upi-fraud` with the venv active: `python src\server.py`. Wait for the line `server ready` (15-40 s warm-up).
   - To start the replay automatically: PowerShell `$env:AUTOSTART="1"; python src\server.py`.
   - Each start gives a fresh alerts/cases database (`$env:KEEP_DB="1"` keeps it).
2. Frontend: `npm run dev` -> http://localhost:5173.
3. Check the backend alone: open http://localhost:8000/docs and try `GET /api/scenarios`, then `POST /api/score`.
4. Final demo: `npm run build` puts the app in `frontend/dist`; the backend serves it at http://localhost:8000, so one command runs everything.
