"""
store.py - SQLite storage for alerts and cases (data/app.db).

Alert inserts go through a queue and a background writer thread, so scoring never waits for the disk.
The database is recreated on every server start (set KEEP_DB=1 to keep it).
"""
import json
import queue
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
  alert_id INTEGER PRIMARY KEY, txn_id INTEGER, ts TEXT, sender TEXT, receiver TEXT, amount REAL,
  score REAL, risk REAL, action TEXT, override TEXT, reasons TEXT, truth TEXT, source TEXT, case_id INTEGER);
CREATE INDEX IF NOT EXISTS idx_alerts_action ON alerts(action);
CREATE TABLE IF NOT EXISTS cases (
  case_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, status TEXT, assignee TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS case_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, ts TEXT, by TEXT, note TEXT, status TEXT);
"""
STATUSES = {"open", "investigating", "confirmed_fraud", "false_positive", "closed"}


def _now():
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path, reset=True):
        path = Path(path)
        path.parent.mkdir(exist_ok=True)
        if reset:
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(str(path) + suffix).unlink()
                except (FileNotFoundError, PermissionError):
                    pass
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self._stop = False
        self.thread = threading.Thread(target=self._writer, daemon=True, name="db-writer")
        self.thread.start()

    # ------------------------------------------------------------ alerts
    def add_alert(self, a):
        self.q.put(a)

    def _drain(self):
        items = []
        try:
            while True:
                items.append(self.q.get_nowait())
        except queue.Empty:
            pass
        if items:
            rows = [(a["alert_id"], a["txn_id"], a["ts"], a["sender"], a["receiver"], a["amount"], a["score"],
                     a["risk"], a["action"], a.get("override"), json.dumps(a["reasons"], ensure_ascii=False),
                     json.dumps(a.get("truth")), a.get("source", "replay"), None) for a in items]
            with self.lock:
                self.db.executemany("INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                self.db.commit()

    def _writer(self):
        import time
        while not self._stop:
            time.sleep(0.1)
            try:
                self._drain()
            except Exception as e:                      # never let the writer die
                print("db writer error:", e)

    def flush(self):
        self._drain()

    @staticmethod
    def _alert(r):
        d = dict(r)
        d["reasons"] = json.loads(d["reasons"]) if d["reasons"] else []
        d["truth"] = json.loads(d["truth"]) if d["truth"] else None
        return d

    def list_alerts(self, limit=50, action=None, status=None):
        sql, args = "SELECT * FROM alerts WHERE 1=1", []
        if action in ("warn", "hold"):
            sql += " AND action=?"; args.append(action)
        if status == "open":
            sql += " AND case_id IS NULL"
        elif status == "in_case":
            sql += " AND case_id IS NOT NULL"
        sql += " ORDER BY alert_id DESC LIMIT ?"; args.append(int(limit))
        with self.lock:
            return [self._alert(r) for r in self.db.execute(sql, args).fetchall()]

    def get_alert(self, alert_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
        return self._alert(r) if r else None

    # ------------------------------------------------------------- cases
    def _case(self, case_id):
        c = self.db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if not c:
            return None
        d = dict(c)
        d["alert_ids"] = [r[0] for r in self.db.execute(
            "SELECT alert_id FROM alerts WHERE case_id=? ORDER BY alert_id", (case_id,))]
        d["events"] = [dict(r) for r in self.db.execute(
            "SELECT ts, by, note, status FROM case_events WHERE case_id=? ORDER BY id", (case_id,))]
        return d

    def create_case(self, alert_ids, title, assignee):
        self.flush()
        with self.lock:
            cur = self.db.execute("INSERT INTO cases (title,status,assignee,created_at,updated_at) VALUES (?,?,?,?,?)",
                                  (title, "open", assignee, _now(), _now()))
            cid = cur.lastrowid
            for a in alert_ids or []:
                self.db.execute("UPDATE alerts SET case_id=? WHERE alert_id=?", (cid, a))
            self.db.execute("INSERT INTO case_events (case_id,ts,by,note,status) VALUES (?,?,?,?,?)",
                            (cid, _now(), assignee or "analyst", "Case opened", "open"))
            self.db.commit()
            return self._case(cid)

    def list_cases(self, status=None):
        with self.lock:
            ids = [r[0] for r in self.db.execute(
                "SELECT case_id FROM cases" + (" WHERE status=?" if status else "") + " ORDER BY case_id DESC",
                (status,) if status else ())]
            return [self._case(i) for i in ids]

    def get_case(self, case_id):
        with self.lock:
            return self._case(case_id)

    def patch_case(self, case_id, status=None, assignee=None, note=None, alert_ids=None):
        with self.lock:
            if not self.db.execute("SELECT 1 FROM cases WHERE case_id=?", (case_id,)).fetchone():
                return None
            if status:
                self.db.execute("UPDATE cases SET status=? WHERE case_id=?", (status, case_id))
            if assignee:
                self.db.execute("UPDATE cases SET assignee=? WHERE case_id=?", (assignee, case_id))
            for a in alert_ids or []:
                self.db.execute("UPDATE alerts SET case_id=? WHERE alert_id=?", (case_id, a))
            self.db.execute("UPDATE cases SET updated_at=? WHERE case_id=?", (_now(), case_id))
            by = assignee or self.db.execute("SELECT assignee FROM cases WHERE case_id=?", (case_id,)).fetchone()[0]
            if note or status:
                self.db.execute("INSERT INTO case_events (case_id,ts,by,note,status) VALUES (?,?,?,?,?)",
                                (case_id, _now(), by or "analyst", note or "", status))
            self.db.commit()
            return self._case(case_id)

    def close(self):
        self._stop = True
        try:
            self._drain()
            self.db.close()
        except Exception:
            pass
