"""
demo.py - payment-terminal demo layer.

  Raspberry Pi  -> /terminal      shows a payment QR, then success / failed
  Phone         -> /p/<session>   opened by scanning the QR; the customer pays here
  Laptop        -> dashboard      presenter picks a scenario, watches the decision live

All fraud logic stays in engine.py. This file only runs the demo "sessions" (one QR = one session)
and turns what the phone does into what the Pi shows and what the dashboard shows.
"""
import os
import secrets
import socket
import threading
import time
from collections import OrderedDict
from typing import Optional

RESULT_SECONDS = float(os.environ.get("RESULT_SECONDS", "10"))   # how long the Pi keeps the result on screen
MULE_ACCOUNT = os.environ.get("MULE_ACCOUNT", "Z022921")          # receiver used by the "mule" scenario
PRESETS = [
    {"id": "normal", "title": "Normal payment", "scenario": "normal_grocery", "merchant": "Fresh Mart",
     "expected": "allow",
     "story": "Customer pays at a shop. No warning. Pi: Payment successful."},
    {"id": "risky", "title": "Risky payment (alert)", "scenario": "__warn__", "merchant": "Quick Transfer",
     "expected": "warn",
     "story": "Phone shows an alert. Continue: Pi says successful, dashboard flags it. Cancel: Pi says failed."},
    {"id": "mule", "title": "Mule account (hold)", "scenario": "__mule__", "merchant": "Fresh Mart",
     "expected": "hold", "receiver": MULE_ACCOUNT,
     "story": "The shop QR was swapped to a mule account. Payment is held; dashboard shows the mule network."},
]
FINAL = {"success", "failed"}
VOICE_STATES = {"played", "muted", "blocked", "unavailable"}
LANG_NAMES = {"en": "English", "hi": "Hindi", "ta": "Tamil"}


def guess_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))          # no packet is sent; this only picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class DemoHub:
    def __init__(self, eng, emit, public_url: Optional[str] = None, log=print):
        self.eng, self.emit, self.log = eng, emit, log
        self.lock = threading.RLock()
        self.sessions = OrderedDict()
        self.current_id = None
        self.public_url = (public_url or os.environ.get("PUBLIC_URL") or f"http://{guess_lan_ip()}:8000").rstrip("/")
        self.warn_payment = None
        self.hold_payment = None
        self.calibrate()

    # ------------------------------------------------------------ scenarios
    def calibrate(self):
        """Find the payments for the two scenarios the built-in presets do not cover, so the demo is repeatable:
        'risky' = a payment the model scores as 'warn';  'mule' = a payment to MULE_ACCOUNT that is held."""
        self.warn_payment = self._find_warn()
        self.hold_payment = self._find_hold()

    def _find_warn(self):
        """Amount scan on a first-time transfer until the model says 'warn'."""
        import numpy as np
        eng = self.eng
        base = next((s["payment"] for s in eng.scenarios if s["id"] == "first_time_relative"), None)
        if base is None:
            return None
        senders = list(dict.fromkeys(s["payment"]["sender"] for s in eng.scenarios))
        for extra in ({}, {"call_active": True}):
            for sender in [base["sender"]] + [x for x in senders if x != base["sender"]]:
                med = max(float(eng.users_med.get(sender, 400)), 100.0)
                for k in np.geomspace(1.0, 60.0, 60):
                    p = {**base, **extra, "sender": sender, "amount": float(max(300.0, round(med * k, -1)))}
                    if eng.adhoc(p, emit=False)["action"] == "warn":
                        self.log(f"[demo] warn scenario: {p['sender']} -> {p['receiver']} INR {p['amount']:,.0f}")
                        return p
        self.log("[demo] WARNING: no payment scores as 'warn' with the current thresholds. "
                 "Raise the warn sensitivity on the dashboard, then press Recalibrate.")
        return None

    def _find_hold(self):
        """The shop-QR-swap payment, but paid to MULE_ACCOUNT. Plain payment first; if the model does not hold it,
        add risk signals (phone call, bigger amount) until it does."""
        eng = self.eng
        sc = next((s for s in eng.scenarios if s["id"] == "qr_swap"), None)
        if sc is None:
            return None
        base = {**sc["payment"], "receiver": MULE_ACCOUNT}
        amt = float(base["amount"])
        for extra in ({}, {"call_active": True}, {"amount": amt * 4}, {"call_active": True, "amount": amt * 4},
                      {"call_active": True, "screen_share": True, "amount": amt * 8}):
            p = {**base, **extra}
            if eng.adhoc(p, emit=False)["action"] == "hold":
                self.log(f"[demo] mule scenario: {p['sender']} -> {p['receiver']} INR {p['amount']:,.0f}"
                         + (f"  (needed extra signals: {list(extra)})" if extra else ""))
                return p
        self.log(f"[demo] WARNING: payments to {MULE_ACCOUNT} are not held by the model. "
                 "Check the account id exists in the data, or lower the hold threshold.")
        return base

    def _payment(self, preset):
        if preset["scenario"] == "__warn__":
            return self.warn_payment
        if preset["scenario"] == "__mule__":
            return self.hold_payment
        sc = next((s for s in self.eng.scenarios if s["id"] == preset["scenario"]), None)
        return sc["payment"] if sc else None

    def presets(self):
        out = []
        for p in PRESETS:
            pay = self._payment(p)
            observed = self.eng.adhoc(pay, emit=False)["action"] if pay else None
            graph_ok = None                                     # does the Investigation page have a graph for it?
            if pay and p["expected"] == "hold":
                graph_ok = self.eng.neighbourhood(pay["receiver"], 1) is not None
            out.append({**p, "available": pay is not None, "observed": observed,
                        "amount": pay["amount"] if pay else None, "graph_ok": graph_ok,
                        "ok": observed == p["expected"]})
        return out
    # ------------------------------------------------------------- sessions
    def create(self, preset_id):
        preset = next((p for p in PRESETS if p["id"] == preset_id), None)
        if preset is None:
            raise KeyError("unknown preset")
        pay = self._payment(preset)
        if pay is None:
            raise ValueError("this scenario is not available with the current thresholds (see /api/demo/presets)")
        with self.lock:
            if self.current_id:
                old = self.sessions.get(self.current_id)
                if old and old["status"] not in FINAL:
                    self._set(old, status="expired")
            sid = secrets.token_hex(3)
            s = {"id": sid, "preset": preset["id"], "title": preset["title"], "merchant": preset["merchant"],
                 "amount": pay["amount"], "sender": pay["sender"], "receiver": pay["receiver"],
                 "channel": pay.get("channel", "p2p"), "payment": pay,
                 "pay_url": f"{self.public_url}/p/{sid}", "status": "waiting_scan",
                 "action": None, "txn_id": None, "alert_id": None, "risk": None, "override": None,
                 "reasons": [], "choice": None, "outcome": None, "voice": None,
                 "dashboard": {"level": "pending", "label": "QR shown – waiting for the customer", "graph_account": None},
                 "created": time.time(), "updated": time.time(), "finished": None, "events": []}
            self.sessions[sid] = s
            while len(self.sessions) > 200:
                self.sessions.popitem(last=False)
            self.current_id = sid
            self._event(s, "QR shown on the terminal")
            self._push(s)
            return self.view(s)

    def get(self, sid):
        s = self.sessions.get(sid)
        if s is None:
            raise KeyError("unknown session")
        return s

    def pay(self, sid, lang="en"):
        """The phone pressed Pay: score it with the real engine."""
        with self.lock:
            s = self.get(sid)
            if s["status"] == "expired":
                raise ValueError("This QR code is no longer active.")
            if s["status"] != "waiting_scan":                       # double tap / page reload: give the same answer
                return self.phone_view(s)
            self._set(s, status="processing")
        res = self.eng.adhoc(dict(s["payment"], lang=lang))
        with self.lock:
            s.update(action=res["action"], txn_id=res["txn_id"], alert_id=res.get("alert_id"), risk=res["risk"],
                     override=res.get("override"), reasons=res.get("reasons", []))
            self._event(s, f"Scored: {res['action']} (risk {res['risk']})")
            if res["action"] == "allow":
                self._finish(s, self.eng.respond(res["txn_id"], "proceed"), "proceed")
            elif res["action"] == "warn":
                self._set(s, status="awaiting_customer", dashboard={
                    "level": "alert", "label": "Alert shown to the customer – waiting for their choice",
                    "graph_account": None})
            else:
                self._set(s, status="awaiting_customer", dashboard={
                    "level": "held", "label": "Payment HELD – receiver looks like a mule account",
                    "graph_account": s["receiver"]})
            return self.phone_view(s)

    def voice(self, sid, status, lang="en", chosen=None, fallback=False):
        """The phone reports what happened to the spoken warning (played / muted / blocked / unavailable)."""
        if status not in VOICE_STATES:
            raise ValueError("status must be one of " + ", ".join(sorted(VOICE_STATES)))
        lang = lang if lang in LANG_NAMES else "en"
        chosen = chosen if chosen in LANG_NAMES else lang
        with self.lock:
            s = self.get(sid)
            plays = (s.get("voice") or {}).get("plays", 0) + (1 if status == "played" else 0)
            if status == "played":
                if fallback:
                    text = f"Warning spoken aloud in English (no {LANG_NAMES[chosen]} voice on the phone)"
                elif plays > 1:
                    text = f"Customer replayed the spoken warning ({LANG_NAMES[lang]})"
                else:
                    text = f"Warning spoken aloud in {LANG_NAMES[lang]}"
            else:
                text = {"muted": "Voice is muted on the phone",
                        "blocked": "The phone blocked the voice; the customer must tap Listen again",
                        "unavailable": "This phone cannot speak"}[status]
            self._event(s, text)
            self._set(s, voice={"status": status, "lang": lang, "chosen": chosen, "fallback": bool(fallback),
                                "plays": plays, "at": time.time()})
            return {"ok": True}    

    def respond(self, sid, response):
        with self.lock:
            s = self.get(sid)
            if s["status"] != "awaiting_customer":
                raise ValueError("nothing is waiting for a response")
            out = self.eng.respond(s["txn_id"], response)       # raises ValueError for illegal moves
            self._finish(s, out, response)
            return self.phone_view(s)

    def _finish(self, s, out, choice):
        outcome = out["status"]                                  # settled | released | cancelled | rejected
        ok = outcome in ("settled", "released")
        a = s["action"]
        if a == "allow":
            dash = ("clear", "Clean payment – no action needed", None)
        elif a == "warn" and ok:
            dash = ("alert", "ALERT – customer paid despite the warning. Needs review", None)
        elif a == "warn":
            dash = ("alert", "Alert – customer cancelled after the warning", None)
        elif ok:
            dash = ("held", "Hold released after verification – review the mule network", s["receiver"])
        else:
            dash = ("blocked", "Payment BLOCKED – mule account stopped", s["receiver"])
        s["choice"], s["outcome"], s["finished"] = choice, outcome, time.time()
        self._event(s, f"Customer chose '{choice}' -> {outcome}")
        self._set(s, status="success" if ok else "failed", message=out["message"],
                  dashboard={"level": dash[0], "label": dash[1], "graph_account": dash[2]})

    def reset(self):
        with self.lock:
            self.current_id = None
            self.sessions.clear()
        self.emit("demo_reset", {})

    # ---------------------------------------------------------------- views
    def _event(self, s, text):
        s["events"].append({"t": time.time(), "text": text})

    def _set(self, s, **kw):
        s.update(kw)
        s["updated"] = time.time()
        self._push(s)

    def _push(self, s):
        self.emit("demo_session", self.view(s))

    def view(self, s):
        """Everything the dashboard and the Pi need (the Pi only reads status/merchant/amount/pay_url)."""
        d = {k: v for k, v in s.items() if k not in ("payment",)}
        d["current"] = s["id"] == self.current_id
        d["scenario_title"] = s["title"]
        return d

    def phone_view(self, s):
        return {"id": s["id"], "status": s["status"], "action": s["action"], "txn_id": s["txn_id"],
                "merchant": s["merchant"], "amount": s["amount"], "receiver": s["receiver"],
                "reasons": s["reasons"], "outcome": s["outcome"], "message": s.get("message")}

    def current(self):
        """What the Pi should show right now (None = idle screen)."""
        with self.lock:
            s = self.sessions.get(self.current_id) if self.current_id else None
            if s is None or s["status"] == "expired":
                return None
            if s["status"] in FINAL and s["finished"] and time.time() - s["finished"] > RESULT_SECONDS:
                return None
            return self.view(s)

    def log_rows(self, limit=50):
        with self.lock:
            return [self.view(s) for s in list(self.sessions.values())[-limit:]][::-1]


def build_router(S):
    """FastAPI routes. S is server.py's state object; S.demo is set once the engine is ready."""
    from fastapi import APIRouter, HTTPException, Response
    from pydantic import BaseModel
    from engine import clean

    router = APIRouter(prefix="/api/demo")

    class NewSession(BaseModel):
        preset: str

    class PayBody(BaseModel):
        lang: Optional[str] = "en"

    class RespondBody(BaseModel):
        response: str

    class VoiceBody(BaseModel):
        status: str
        lang: Optional[str] = "en"
        chosen: Optional[str] = None
        fallback: Optional[bool] = False

    def hub():
        if S.demo is None:
            raise HTTPException(503, "demo is still starting")
        return S.demo

    def guard(fn, *a):
        try:
            return clean(fn(*a))
        except KeyError as ex:
            raise HTTPException(404, str(ex).strip("'\""))
        except ValueError as ex:
            raise HTTPException(400, str(ex))

    @router.get("/info")
    def info():
        h = hub()
        return {"public_url": h.public_url, "terminal_url": f"{h.public_url}/terminal",
                "result_seconds": RESULT_SECONDS}

    @router.get("/presets")
    def presets():
        return clean(hub().presets())

    @router.post("/calibrate")
    def calibrate():
        hub().calibrate()
        return clean(hub().presets())

    @router.post("/sessions")
    def new_session(body: NewSession):
        return guard(hub().create, body.preset)

    @router.get("/sessions")
    def sessions(limit: int = 50):
        return clean(hub().log_rows(limit))

    @router.get("/current")
    def current():
        return {"session": clean(hub().current())}

    @router.get("/sessions/{sid}")
    def one(sid: str):
        return guard(lambda: hub().phone_view(hub().get(sid)))

    @router.post("/sessions/{sid}/pay")
    def pay(sid: str, body: PayBody):
        return guard(hub().pay, sid, body.lang or "en")

    @router.post("/sessions/{sid}/respond")
    def respond(sid: str, body: RespondBody):
        return guard(hub().respond, sid, body.response)

    @router.post("/sessions/{sid}/voice")
    def voice(sid: str, body: VoiceBody):
        return guard(hub().voice, sid, body.status, body.lang or "en", body.chosen, bool(body.fallback))

    @router.post("/reset")
    def reset():
        hub().reset()
        return {"ok": True}

    @router.get("/qr/{sid}.svg")
    def qr(sid: str):
        import io
        import segno
        try:
            s = hub().get(sid)
        except KeyError:
            raise HTTPException(404, "unknown session")
        buf = io.BytesIO()
        segno.make(s["pay_url"], error="m").save(buf, kind="svg", scale=8, border=2, xmldecl=False)
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    @router.get("/qr/{sid}.json")
    def qr_matrix(sid: str):
        import segno
        try:
            s = hub().get(sid)
        except KeyError:
            raise HTTPException(404, "unknown session")
        q = segno.make(s["pay_url"], error="m")
        return {"size": len(q.matrix), "rows": ["".join("1" if c else "0" for c in row) for row in q.matrix]}
    
    return router