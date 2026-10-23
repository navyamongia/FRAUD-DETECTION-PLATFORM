"""
server.py - FastAPI web layer. All logic lives in engine.py / store.py; see docs/API_CONTRACT.md.

Run (from the project folder, venv active):
    python src\\server.py                                   -> http://127.0.0.1:8000  (interactive docs at /docs)
or  uvicorn server:app --app-dir src --port 8000

Startup takes about 15-40 seconds (it replays the history to warm up the feature store).
"""
import asyncio
import json
import os
import queue
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine import ROOT, Engine, clean
from fastapi.responses import FileResponse
from demo import DemoHub, build_router
from store import STATUSES, Store

S = SimpleNamespace(eng=None, store=None, clients=set(), q=queue.Queue(), pump=None, demo=None)


# --------------------------------------------------------------- request bodies
class ScoreRequest(BaseModel):
    scenario_id: Optional[str] = None
    sender: Optional[str] = None
    receiver: Optional[str] = None
    amount: Optional[float] = None
    channel: Optional[str] = "p2p"
    qr_merchant: Optional[str] = None
    call_active: bool = False
    screen_share: bool = False
    device_id: Optional[str] = None
    city: Optional[str] = None
    lang: Optional[str] = None


class ConfigRequest(BaseModel):
    warn_t: Optional[float] = None
    hold_t: Optional[float] = None
    warn_pct: Optional[float] = None
    hold_pct: Optional[float] = None
    overrides_enabled: Optional[bool] = None


class ReplayRequest(BaseModel):
    action: str
    speed: Optional[float] = None


class RespondRequest(BaseModel):
    response: str


class CaseCreate(BaseModel):
    alert_ids: List[int] = []
    title: str
    assignee: Optional[str] = None


class CasePatch(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None
    note: Optional[str] = None
    alert_ids: Optional[List[int]] = None


# -------------------------------------------------------------- live broadcast
def _emit(kind, data):
    S.q.put((kind, data))


async def _pump():
    """Every 100 ms: take the events produced by the replay thread and send them to all WebSocket clients."""
    last_stats = 0.0
    while True:
        await asyncio.sleep(0.1)
        msgs, allow_budget = [], 60                     # keep every alert, but cap routine 'allow' events per tick
        try:
            while len(msgs) < 3000:
                kind, data = S.q.get_nowait()
                if kind == "txn_scored" and data["action"] == "allow":
                    if allow_budget <= 0:
                        continue
                    allow_budget -= 1
                msgs.append((kind, data))
        except queue.Empty:
            pass
        now = time.time()
        if now - last_stats >= 1.0 and S.eng is not None:
            msgs.append(("stats", S.eng.get_stats()))
            last_stats = now
        if msgs and S.clients:
            payloads = [json.dumps(clean({"type": k, "data": d}), ensure_ascii=False) for k, d in msgs]
            for ws in list(S.clients):
                try:
                    for p in payloads:
                        await ws.send_text(p)
                except Exception:
                    S.clients.discard(ws)


@asynccontextmanager
async def lifespan(app):
    loop = asyncio.get_running_loop()
    S.store = Store(ROOT / "data" / "app.db", reset=os.environ.get("KEEP_DB") != "1")
    S.eng = await loop.run_in_executor(None, lambda: Engine(store=S.store, log=print))
    S.eng.emit = _emit
    S.eng.start_threads()
    S.demo = DemoHub(S.eng, _emit)
    print(f"phone/Pi address: {S.demo.public_url}   (Pi screen: {S.demo.public_url}/terminal)")
    if os.environ.get("AUTOSTART") == "1":
        S.eng.replay_control("start", speed=float(os.environ.get("SPEED", "600")))
    S.pump = asyncio.create_task(_pump())
    print("server ready: http://127.0.0.1:8000  (API docs: /docs)")
    yield
    S.pump.cancel()
    S.eng.close()
    S.store.close()


app = FastAPI(title="UPI Fraud Alert System", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(build_router(S))

_web = ROOT / "demo_web"


@app.get("/terminal")
def terminal_page():
    return FileResponse(_web / "terminal.html")


@app.get("/p/{sid}")
def phone_page(sid: str):
    return FileResponse(_web / "phone.html")

def J(obj):
    return clean(obj)


# ------------------------------------------------------------------- system
@app.get("/api/health")
def health():
    e = S.eng
    return J({"status": "ok", "model": "lightgbm", "n_features": len(e.names), "replay_txns": e.n_replay,
              "replay": e.replay_state()})


@app.get("/api/config")
def get_config():
    return J(S.eng.get_config())


@app.post("/api/config")
def set_config(body: ConfigRequest):
    try:
        return J(S.eng.set_config(**body.model_dump()))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/replay")
def get_replay():
    return J(S.eng.replay_state())


@app.post("/api/replay")
def post_replay(body: ReplayRequest):
    try:
        return J(S.eng.replay_control(body.action, body.speed))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/stats")
def stats():
    return J(S.eng.get_stats())


# ------------------------------------------------------- payment simulator
@app.get("/api/scenarios")
def scenarios():
    return J(S.eng.scenarios)


@app.post("/api/score")
def score(body: ScoreRequest):
    p = body.model_dump()
    if body.scenario_id:
        sc = next((s for s in S.eng.scenarios if s["id"] == body.scenario_id), None)
        if sc is None:
            raise HTTPException(404, "unknown scenario_id")
        p = {**p, **sc["payment"]}
    if not p.get("sender") or not p.get("receiver") or not p.get("amount"):
        raise HTTPException(400, "sender, receiver and amount are required (or give a scenario_id)")
    return J(S.eng.adhoc(p))


@app.post("/api/payments/{txn_id}/respond")
def respond(txn_id: int, body: RespondRequest):
    try:
        return J(S.eng.respond(txn_id, body.response))
    except KeyError:
        raise HTTPException(404, "unknown payment")
    except ValueError as ex:
        raise HTTPException(400, str(ex))


# --------------------------------------------------------- live dashboard
@app.get("/api/alerts")
def alerts(limit: int = 50, action: Optional[str] = None, status: Optional[str] = None):
    return J(S.store.list_alerts(limit, action, status))


@app.get("/api/trace/{txn_id}")
def trace(txn_id: int):
    out = S.eng.trace(txn_id)
    if out is None:
        raise HTTPException(404, "transaction not in the recent window (the last 20,000 scored are kept)")
    return J(out)


@app.get("/api/simulate")
def simulate(warn_t: Optional[float] = None, hold_t: Optional[float] = None,
             warn_pct: Optional[float] = Query(None, description="% of genuine payments that get at least a warning"),
             hold_pct: Optional[float] = Query(None, description="% of genuine payments that get held")):
    try:
        return J(S.eng.simulate(warn_t, hold_t, warn_pct, hold_pct))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.get("/api/curve")
def curve():
    return J(S.eng.curve)


@app.get("/api/evaluation")
def evaluation():
    return J(S.eng.evaluation())


# ------------------------------------------------------ investigation
@app.get("/api/graph/{account_id}")
def graph(account_id: str, hops: int = 2):
    out = S.eng.neighbourhood(account_id, hops)
    if out is None:
        raise HTTPException(404, "account has no transactions in the investigation window")
    return J(out)


@app.post("/api/cases")
def create_case(body: CaseCreate):
    return J(S.store.create_case(body.alert_ids, body.title, body.assignee))


@app.get("/api/cases")
def list_cases(status: Optional[str] = None):
    return J(S.store.list_cases(status))


@app.get("/api/cases/{case_id}")
def get_case(case_id: int):
    c = S.store.get_case(case_id)
    if c is None:
        raise HTTPException(404, "unknown case")
    return J(c)


@app.patch("/api/cases/{case_id}")
def patch_case(case_id: int, body: CasePatch):
    if body.status and body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(STATUSES)}")
    c = S.store.patch_case(case_id, body.status, body.assignee, body.note, body.alert_ids)
    if c is None:
        raise HTTPException(404, "unknown case")
    _emit("case_update", c)
    return J(c)


# ---------------------------------------------------------------- WebSocket
@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket):
    await ws.accept()
    S.clients.add(ws)
    try:
        for kind, data in (("config_changed", S.eng.get_config()), ("replay_state", S.eng.replay_state()),
                           ("stats", S.eng.get_stats())):
            await ws.send_text(json.dumps(clean({"type": kind, "data": data}), ensure_ascii=False))
        while True:
            await ws.receive_text()                    # we only push; incoming messages are ignored
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        S.clients.discard(ws)


# Built React app (npm run build -> frontend/dist) is served last, so /api and /ws take priority.
_dist = ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
