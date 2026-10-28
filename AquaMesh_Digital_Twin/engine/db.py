"""SQLite persistence: every telemetry packet, assessment, state transition and alert is stored with its evidence."""
import json, sqlite3, threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry(id INTEGER PRIMARY KEY, ts REAL, hw TEXT, pkt INTEGER, status TEXT,
  raw_json TEXT, rssi REAL, snr REAL, flags TEXT, calib TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS rejected(id INTEGER PRIMARY KEY, ts REAL, line TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY, ts REAL, mode TEXT, flood_state TEXT, gas_state TEXT,
  health TEXT, risk_index INTEGER, kb_version TEXT, calib TEXT, leaves_json TEXT, metrics_json TEXT, top_hypothesis TEXT);
CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY, ts REAL, domain TEXT, from_state TEXT, to_state TEXT,
  reasons_json TEXT, assessment_id INTEGER);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, event_type TEXT, severity TEXT, title TEXT, detail TEXT,
  goal TEXT, evidence_json TEXT, assessment_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_tel_ts ON telemetry(ts); CREATE INDEX IF NOT EXISTS ix_as_ts ON assessments(ts);
"""


class DB:
    def __init__(self, path):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.c.execute("PRAGMA journal_mode=WAL")
            self.c.executescript(SCHEMA)
            self.c.commit()

    def _x(self, sql, args=()):
        with self.lock:
            cur = self.c.execute(sql, args)
            self.c.commit()
            return cur.lastrowid

    def _q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def telemetry(self, ts, p, calib, mode):
        self._x("INSERT INTO telemetry(ts,hw,pkt,status,raw_json,rssi,snr,flags,calib,mode) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ts, p["hw"], p["pkt"], p["status"], json.dumps(p["values"]), p.get("rssi"), p.get("snr"), ",".join(p["flags"]), calib, mode))

    def rejected(self, ts, line, reason):
        self._x("INSERT INTO rejected(ts,line,reason) VALUES(?,?,?)", (ts, line[:300], reason))

    def assessment(self, ts, mode, a, calib, kb_version, leaves, metrics, top):
        return self._x("INSERT INTO assessments(ts,mode,flood_state,gas_state,health,risk_index,kb_version,calib,leaves_json,metrics_json,top_hypothesis)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (ts, mode, a["flood"], a["gas"], a["health"], a["risk_index"], kb_version, calib,
                        json.dumps({k: list(v) for k, v in leaves.items()}), json.dumps(metrics), top))

    def transition(self, ts, domain, f, t, reasons, aid):
        return self._x("INSERT INTO transitions(ts,domain,from_state,to_state,reasons_json,assessment_id) VALUES(?,?,?,?,?,?)",
                       (ts, domain, f, t, json.dumps(reasons), aid))

    def event(self, ts, etype, sev, title, detail, goal, evidence, aid):
        return self._x("INSERT INTO events(ts,event_type,severity,title,detail,goal,evidence_json,assessment_id) VALUES(?,?,?,?,?,?,?,?)",
                       (ts, etype, sev, title, detail, goal, json.dumps(evidence), aid))

    # ---- reads ----
    def events(self, limit=50):
        return self._q("SELECT id,ts,event_type,severity,title,detail,goal,assessment_id FROM events ORDER BY id DESC LIMIT ?", (limit,))

    def event_row(self, eid):
        r = self._q("SELECT * FROM events WHERE id=?", (eid,))
        return r[0] if r else None

    def assessment_row(self, aid):
        r = self._q("SELECT * FROM assessments WHERE id=?", (aid,))
        return r[0] if r else None

    def timeline(self, since, limit=600):
        return self._q("SELECT id,ts,flood_state,gas_state,health,risk_index,top_hypothesis FROM assessments WHERE ts>=? ORDER BY ts LIMIT ?", (since, limit))

    def transitions(self, limit=40):
        return self._q("SELECT * FROM transitions ORDER BY id DESC LIMIT ?", (limit,))

    def telemetry_window(self, since, until):
        return self._q("SELECT ts,hw,pkt,status,raw_json FROM telemetry WHERE ts BETWEEN ? AND ? ORDER BY ts", (since, until))

    def counts(self):
        return {t: self._q(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in ("telemetry", "rejected", "assessments", "transitions", "events")}
