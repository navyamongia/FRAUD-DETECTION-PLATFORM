# UPI Fraud Alert System: Frontend Integration Guide

For the frontend developer. Everything the UI needs is behind one backend at
`http://localhost:8000`. You never touch the model or the data; you call these endpoints
and listen to one WebSocket. The backend (FastAPI) will follow this contract exactly.
Until the server is ready, build against the example payloads below.

- All bodies are JSON. CORS is enabled for `http://localhost:5173` (Vite) and `http://localhost:3000`.
- Money is INR (number). Times are ISO 8601 strings in simulated time (the replay runs in fast-forward).
- Languages: `"en"`, `"ta"` (Tamil), `"hi"` (Hindi). Reason texts always arrive in all three, so the language toggle is instant and needs no new request.
- Priority tags: **P0** = needed for the demo, **P1** = strongly wanted, **P2** = stretch.

## 1. The three views and what feeds them

| View | What it shows | Endpoints |
|---|---|---|
| **Payment simulator** (phone-app screen) | User picks a scenario or types a payment, gets allow / warn / hold, sees the warning in their language, taps Proceed / Cancel / Verify | `GET /api/scenarios`, `POST /api/score`, `POST /api/payments/{txn_id}/respond` |
| **Live risk dashboard** | Streaming alerts, why each fired, decisions, latency, plus the **threshold slider** with live precision / recall / friction | WebSocket `/ws/stream`, `GET /api/alerts`, `GET /api/trace/{txn_id}`, `GET /api/stats`, `GET /api/simulate`, `GET /api/curve`, `POST /api/config` |
| **Investigation workspace** | A small account network around an alert, and a case the analyst updates | `GET /api/graph/{account_id}`, `GET/POST/PATCH /api/cases...` |
| (bonus) **Evaluation** tab | Ablation, per-scenario recall, value prevented (for judges) | `GET /api/evaluation` |

The headline feature for judges is the **Decision Trace** (`GET /api/trace/{txn_id}`):
a left-to-right picture of what happened inside the backend for one transaction
(features → graph → model + SHAP → thresholds → action, with milliseconds per stage).

## 2. WebSocket: live stream  (P0)

`ws://localhost:8000/ws/stream`. Every message is `{ "type": ..., "data": ... }`.

| type | when | data |
|---|---|---|
| `txn_scored` | every scored transaction (can be hundreds per second at high replay speed; **batch your React state updates**) | `ScoreCompact` |
| `alert` | when action is `warn` or `hold` | `Alert` |
| `stats` | once per second | `Stats` |
| `config_changed` | thresholds changed by anyone | `Config` |
| `case_update` | a case is created or changed | `Case` |
| `replay_state` | replay started / paused / speed changed | `ReplayState` |

```json
// txn_scored (kept small on purpose)
{ "type": "txn_scored", "data": {
  "txn_id": 412233, "ts": "2026-10-12T14:03:22", "sender": "U00412", "receiver": "Z001977",
  "amount": 18500, "score": 0.9712, "action": "hold", "latency_ms": 4.8 } }
```

## 3. REST endpoints

### 3.1 Payment simulator

**`GET /api/scenarios`  (P0)** - preset payments for the demo buttons.
```json
[ { "id": "normal_grocery", "title": "Normal grocery payment", "expected": "allow",
    "payment": { "sender": "U00412", "receiver": "M0031", "amount": 420, "channel": "qr" } },
  { "id": "rent_new_landlord", "title": "Rent to a new landlord", "expected": "allow" },
  { "id": "vishing_call", "title": "Large transfer while on a call", "expected": "hold" },
  { "id": "fake_collect", "title": "Approve a collect request", "expected": "warn" },
  { "id": "qr_swap", "title": "Shop QR now pays a new account", "expected": "hold" } ]
```

**`POST /api/score`  (P0)** - score one payment. Everything except `sender`, `receiver`, `amount` is optional.
The backend fills device, location and history from the sender's profile.
```json
// request
{ "scenario_id": "vishing_call" }
// or
{ "sender": "U00412", "receiver": "Z001977", "amount": 18500, "channel": "p2p",
  "call_active": true, "screen_share": false, "lang": "ta" }
```
`channel` is `"p2p" | "qr" | "collect"`. Response (`ScoreResult`):
```json
{ "txn_id": 900001, "ts": "2026-10-12T14:03:22", "sender": "U00412", "receiver": "Z001977",
  "amount": 18500, "channel": "p2p",
  "score": 0.9712, "action": "hold",
  "thresholds": { "warn_t": 0.0016, "hold_t": 0.0178 },
  "override": null,
  "reasons": [
    { "id": "AMOUNT", "weight": 2.31,
      "text": { "en": "The amount is much higher than you usually pay.", "ta": "...", "hi": "..." } },
    { "id": "NEW_PAYEE", "weight": 1.2, "text": { "en": "...", "ta": "...", "hi": "..." } },
    { "id": "CALL", "weight": 0.9, "text": { "en": "...", "ta": "...", "hi": "..." } } ],
  "latency_ms": { "features": 0.1, "model": 0.6, "decision": 0.01, "total": 2.4 },
  "next_step": "verify" }
```
- `action`: `"allow" | "warn" | "hold"`.
- `override`: `null` or a rule name, e.g. `"collect_to_flagged_mule"` (shown as a badge in the trace).
- `reasons`: up to 3, strongest first. For `allow` it is an empty list.
- `next_step` tells the phone screen what to render: `"settle"` (allow), `"confirm_or_cancel"` (warn), `"verify"` (hold: OTP or trusted-contact step).
- `latency_ms.total` is the number to display as "scored in X ms". It is the scoring path only.

**`POST /api/payments/{txn_id}/respond`  (P0)** - what the user did after the warning.
```json
{ "response": "proceed" }          // "proceed" | "cancel" | "verified" | "failed_verification"
// -> { "status": "settled" | "cancelled" | "released" | "rejected", "message": "Payment of INR 18,500 cancelled." }
```

### 3.2 Live risk dashboard

**`GET /api/alerts?limit=50&action=hold&status=open`  (P0)** - newest first. `action` and `status` optional.
```json
[ { "alert_id": 311, "txn_id": 412233, "ts": "2026-10-12T14:03:22", "sender": "U00412",
    "receiver": "Z001977", "amount": 18500, "score": 0.9712, "action": "hold",
    "reasons": [ { "id": "AMOUNT", "weight": 2.31, "text": { "en": "...", "ta": "...", "hi": "..." } } ],
    "case_id": null } ]
```

**`GET /api/trace/{txn_id}`  (P0)** - the Decision Trace. This is computed off the scoring path, so it may take a few hundred ms.
```json
{ "txn": { "txn_id": 412233, "ts": "...", "sender": "U00412", "receiver": "Z001977", "amount": 18500, "channel": "p2p",
           "device_id": "D412a", "city": "Chennai", "call_active": true, "screen_share": false },
  "features": [
    { "name": "amt_over_median", "group": "behavioural", "value": 21.4, "normal": 1.1 },
    { "name": "is_new_payee", "group": "behavioural", "value": 1, "normal": 0 },
    { "name": "recv_age_days", "group": "network", "value": 2.1, "normal": 1500 } ],
  "shap": { "bias": -5.1, "contributions": [ { "feature": "amt_over_median", "value": 21.4, "shap": 2.31 } ],
            "raw_logodds": 3.2, "score": 0.9712 },
  "graph": { "pagerank_rel": 4.2, "component_size": 14, "hops_to_known_mule": 2 },
  "decision": { "score": 0.9712, "warn_t": 0.0016, "hold_t": 0.0178, "override": null, "action": "hold" },
  "stages_ms": [ { "stage": "features", "ms": 0.1 }, { "stage": "model", "ms": 0.6 },
                 { "stage": "explain", "ms": 0.8 }, { "stage": "decision", "ms": 0.01 } ] }
```
`group` is `"behavioural" | "context" | "network"` (colour code them). `normal` is a typical value for genuine payments, for showing "this is 20x normal". `shap.contributions` are sorted by absolute value; draw them as a waterfall.

**`GET /api/stats`  (P0)** - also pushed every second over the WebSocket as `stats`.
```json
{ "replay_ts": "2026-10-12T14:03:22", "txns_scored": 18234, "txns_per_sec": 220.4,
  "actions": { "allow": 17890, "warn": 210, "hold": 134 },
  "latency_ms": { "p50": 1.9, "p95": 3.1, "p99": 6.4, "budget": 200 },
  "known_outcomes": { "fraud_caught": 120, "fraud_missed": 4, "false_alarms": 224 } }
```

**`GET /api/simulate?warn_t=0.002&hold_t=0.02`  (P0)** - the **threshold slider**. Recomputes the trade-off on the test period
in a few milliseconds, so call it on slider change (debounce ~100 ms).
```json
{ "warn_t": 0.002, "hold_t": 0.02,
  "precision": 0.44, "recall": 0.976, "fpr": 0.0135,
  "friction": { "warned_pct": 1.12, "held_pct": 0.23, "genuine_per_1000_flagged": 13.5 },
  "fraud_prevented_inr": 1514231, "fraud_total_inr": 1695694,
  "assumptions": { "warn_heeded": 0.5, "hold_effective": 0.9 } }
```

**`GET /api/curve`  (P0)** - precomputed points for drawing the precision / recall / friction curve; mark the current slider position on it.
```json
{ "points": [ { "threshold": 0.001, "precision": 0.31, "recall": 0.99, "fpr": 0.02 }, ... ] }
```

**`GET /api/config` / `POST /api/config`  (P0)** - read and change the live thresholds. Changing them affects all decisions from then on.
```json
{ "warn_t": 0.0016, "hold_t": 0.0178, "languages": ["en","ta","hi"] }
```

**Replay control `GET /api/replay` and `POST /api/replay`  (P0)** - drives the live stream.
```json
{ "action": "start", "speed": 50 }     // "start" | "pause" | "stop"; speed = simulated seconds per real second
// -> ReplayState: { "running": true, "speed": 50, "replay_ts": "...", "progress_pct": 12.3 }
```

### 3.3 Investigation workspace

**`GET /api/graph/{account_id}?hops=2`  (P0)** - small network around an account (cap 60 nodes). Works with react-force-graph-2d directly.
```json
{ "center": "Z001977",
  "nodes": [ { "id": "Z001977", "kind": "mule", "age_days": 2.1, "in_deg": 11, "out_deg": 3,
               "is_known_mule": false, "risk": 0.93 },
             { "id": "U00412", "kind": "user", "age_days": 1450, "in_deg": 4, "out_deg": 20,
               "is_known_mule": false, "risk": 0.05 } ],
  "edges": [ { "source": "U00412", "target": "Z001977", "amount": 18500, "ts": "...", "count": 1 } ] }
```
`kind`: `"user" | "merchant" | "mule" | "landlord" | "organizer"`. Colour by `risk` (0 to 1); outline `is_known_mule`.
(In the demo the backend may reveal `kind` for realism. Treat it as a display hint.)

**Cases (P0)**
- `POST /api/cases` body `{ "alert_ids": [311], "title": "Possible mule chain", "assignee": "analyst1" }` -> `Case`
- `GET /api/cases?status=open` -> `Case[]`
- `GET /api/cases/{case_id}` -> `Case`
- `PATCH /api/cases/{case_id}` body `{ "status": "investigating", "assignee": "analyst1", "note": "linked 3 receivers" }` -> `Case`
- `status`: `"open" | "investigating" | "confirmed_fraud" | "false_positive" | "closed"`
```json
{ "case_id": 7, "title": "Possible mule chain", "status": "investigating", "assignee": "analyst1",
  "created_at": "...", "alert_ids": [311, 312],
  "events": [ { "ts": "...", "by": "analyst1", "note": "linked 3 receivers" } ] }
```

### 3.4 Evaluation (P1)

**`GET /api/evaluation`** returns the evaluation results as JSON (ablation table, per-scenario recall, hard-genuine false-positive
rates, value prevented, latency). Render them as simple tables and bar charts; the backend sends the final numbers.
Keys: `ablation[]`, `per_scenario[]`, `hard_genuine[]`, `decision_layer`, `latency`.

## 4. The phone-app screen (what to build)

| State | Show |
|---|---|
| Payment form | Payee, amount, a "Pay" button. Language toggle: English / தமிழ் / हिन्दी. A scenario picker (from `/api/scenarios`) is fine for the demo. |
| `allow` | Brief "Processing..." then success tick. |
| `warn` | Amber card. Headline in the chosen language, then the `reasons[].text[lang]` list, then two buttons: **Cancel payment** (primary) and **Proceed anyway** (secondary). |
| `hold` | Red card: "We paused this payment to protect you." Same reasons list. Buttons: **Verify** (simulated OTP or trusted-contact approval) and **Cancel**. |
| Result | The `message` from `/respond`. |

Keep text large and simple: the audience includes first-time digital users and senior citizens.

## 5. Dashboard UI tips

- At high replay speed do not render every message: buffer `txn_scored` and flush to state about every 250 ms.
- Show the latency budget as a line at 200 ms on the latency chart.
- Clicking an alert opens the Decision Trace panel (`/api/trace/{txn_id}`), then an "Investigate" button opens the graph (`/api/graph/{receiver}`) and a "Create case" button.
- Suggested libraries: React + Vite, `recharts` (charts), `react-force-graph-2d` (network), `react-router-dom` (3 views).

## 6. Running it together

1. Backend (separate terminal): `uvicorn server:app --app-dir src --port 8000` (the server will be delivered after the model step).
2. Frontend: `npm run dev` -> http://localhost:5173.
3. Open `http://localhost:8000/docs` for an auto-generated interactive page where every endpoint can be tried without any UI.
4. For the final demo the built frontend (`npm run build`, output `frontend/dist`) will be served by the backend, so only one command is needed.
