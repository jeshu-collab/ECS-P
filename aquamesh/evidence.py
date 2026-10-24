"""Tamper-evident evidence log: SQLite, each record hash-chained to the previous one,
periodic external checkpoints, random audits that recompute features from raw readings,
and an outbox for cloud sync that survives internet loss."""
import hashlib
import json
import os
import random
import shutil
import sqlite3
import tempfile
import threading
import time

from .protocol import crc8, parse_line

GENESIS = "0" * 64
_rng = random.SystemRandom()


def _h(prev, i, ts, kind, payload):
    return hashlib.sha256(("%s|%d|%.3f|%s|%s" % (prev, i, ts, kind, payload)).encode()).hexdigest()


def norm(v, dry, wet):
    if wet == dry:
        return 0.0
    return max(0.0, min(1.0, (v - dry) / float(wet - dry)))


class Evidence:
    CHECK_EVERY = 100

    def __init__(self, path, clock):
        self.path, self.clock = path, clock
        self.lock = threading.RLock()
        self.cp_path = path + ".checkpoints"
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS rec(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT, prev TEXT, hash TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, payload TEXT)")
        self.db.commit()
        row = self.db.execute("SELECT id,hash FROM rec ORDER BY id DESC LIMIT 1").fetchone()
        self.next_id, self.head = (row[0] + 1, row[1]) if row else (1, GENESIS)
        self.pending = []
        self.audits = []
        self.next_audit = None
        self.audit_stats = dict(runs=0, failed=0, last=None)

    # ---- writing
    def log(self, kind, payload, ts=None):
        ts = self.clock.now() if ts is None else ts
        p = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        with self.lock:
            i = self.next_id
            h = _h(self.head, i, ts, kind, p)
            self.pending.append((i, ts, kind, p, self.head, h))
            self.next_id, self.head = i + 1, h
            if i % self.CHECK_EVERY == 0:
                self.flush()
                with open(self.cp_path, "a") as f:
                    f.write("%d %s %.3f\n" % (i, h, ts))
                    f.flush()
                    os.fsync(f.fileno())
            return i

    def flush(self):
        with self.lock:
            if self.pending:
                self.db.executemany("INSERT INTO rec VALUES(?,?,?,?,?,?)", self.pending)
                self.db.commit()
                self.pending = []

    def outbox_add(self, payload):
        with self.lock:
            self.db.execute("INSERT INTO outbox(ts,payload) VALUES(?,?)", (self.clock.now(), json.dumps(payload, default=str)))
            self.db.commit()

    def outbox_take(self, n=20):
        with self.lock:
            return self.db.execute("SELECT id,payload FROM outbox ORDER BY id LIMIT ?", (n,)).fetchall()

    def outbox_done(self, ids):
        with self.lock:
            self.db.executemany("DELETE FROM outbox WHERE id=?", [(i,) for i in ids])
            self.db.commit()

    def outbox_len(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    # ---- reading
    def recent(self, n=50, kinds=None):
        with self.lock:
            self.flush()
            q = "SELECT id,ts,kind,payload,hash FROM rec"
            args = []
            if kinds:
                q += " WHERE kind IN (%s)" % ",".join("?" * len(kinds))
                args = list(kinds)
            rows = self.db.execute(q + " ORDER BY id DESC LIMIT ?", args + [n]).fetchall()
        return [dict(id=r[0], ts=r[1], kind=r[2], payload=json.loads(r[3]), hash=r[4][:12]) for r in rows]

    def stats(self):
        with self.lock:
            self.flush()
            n = self.db.execute("SELECT COUNT(*) FROM rec").fetchone()[0]
        return dict(records=n, head=self.head[:16], outbox=self.outbox_len(), audits=self.audit_stats)

    # ---- verification
    def verify(self, path=None, cp_path=None):
        with self.lock:
            self.flush()
        db = self.db if path is None else sqlite3.connect(path)
        cp_path = cp_path or self.cp_path
        prev, n, bad = GENESIS, 0, None
        hashes = {}
        for i, ts, kind, p, pv, h in db.execute("SELECT id,ts,kind,payload,prev,hash FROM rec ORDER BY id"):
            n += 1
            if pv != prev or _h(prev, i, ts, kind, p) != h:
                bad = i
                break
            hashes[i] = h
            prev = h
        cps_ok, cps = True, 0
        if os.path.exists(cp_path):
            for line in open(cp_path):
                try:
                    i, h, _ = line.split()
                except ValueError:
                    continue
                cps += 1
                if int(i) in hashes and hashes[int(i)] != h:
                    cps_ok = False
                elif bad is not None and int(i) >= bad and cps_ok:
                    pass
        return dict(ok=bad is None and cps_ok, records=n, first_bad_id=bad, checkpoints=cps, checkpoints_ok=cps_ok)

    def tamper_demo(self):
        """Edit one record in a COPY of the log and show that verification catches it."""
        with self.lock:
            self.flush()
            tmp = tempfile.mkdtemp()
            cp = os.path.join(tmp, "copy.sqlite")
            src = sqlite3.connect(self.path)
            dst = sqlite3.connect(cp)
            src.backup(dst)
            src.close()
            n = dst.execute("SELECT COUNT(*) FROM rec").fetchone()[0]
            before = self.verify(cp, self.cp_path)
            if n < 3:
                dst.close()
                shutil.rmtree(tmp, ignore_errors=True)
                return dict(error="log too short for demo", before=before)
            tid = dst.execute("SELECT id FROM rec WHERE kind='packet' ORDER BY id LIMIT 1 OFFSET 1").fetchone()
            tid = tid[0] if tid else n // 2
            row = dst.execute("SELECT payload FROM rec WHERE id=?", (tid,)).fetchone()
            edited = row[0].replace("RAIN=", "RAIN=9") if "RAIN=" in row[0] else row[0][:-1] + ' ,"x":1}'
            dst.execute("UPDATE rec SET payload=? WHERE id=?", (edited, tid))
            dst.commit()
            after = self.verify(cp, self.cp_path)
            dst.close()
            shutil.rmtree(tmp, ignore_errors=True)
        return dict(edited_record=tid, before=before, after=after)

    # ---- random audit
    def schedule_audit(self, now):
        self.next_audit = now + _rng.uniform(60, 180)

    def maybe_audit(self, now, snapshot_fn=None, cal=None):
        if self.next_audit is None:
            self.schedule_audit(now)
            return None
        if now < self.next_audit:
            return None
        self.schedule_audit(now)
        return self.audit(now, snapshot_fn)

    def audit(self, now, snapshot_fn=None, window=60):
        """Pick an unpredictable window, recompute calibrated features and CRCs from the stored raw
        packets and compare with what the decision engine used."""
        recs = self.recent(400, ["packet", "feat"])
        if not recs:
            return None
        recs.reverse()
        t0 = _rng.uniform(recs[0]["ts"], max(recs[0]["ts"], recs[-1]["ts"] - window))
        win = [r for r in recs if t0 <= r["ts"] <= t0 + window]
        pk = [r for r in win if r["kind"] == "packet"]
        ft = [r for r in win if r["kind"] == "feat"]
        mism, crc_fail = 0, 0
        for r in pk:
            p = parse_line(r["payload"].get("line", ""))
            if not p or not p["crc_ok"]:
                crc_fail += 1
        for r in ft:
            d = r["payload"]
            cal = d.get("cal", {})
            for ch in ("rain", "soil"):
                if d.get(ch + "_raw") is not None and d.get(ch + "_n") is not None and cal.get(ch):
                    exp = norm(d[ch + "_raw"], cal[ch]["dry"], cal[ch]["wet"])
                    if abs(exp - d[ch + "_n"]) > 0.01:
                        mism += 1
        snap = None
        if snapshot_fn:
            try:
                jpg = snapshot_fn()
                if jpg:
                    snap = hashlib.sha256(jpg).hexdigest()[:16]
                    d = os.path.join(os.path.dirname(self.path), "audit_snapshots")
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, "audit_%d.jpg" % int(now)), "wb") as f:
                        f.write(jpg)
                    for old in sorted(os.listdir(d))[:-30]:
                        os.remove(os.path.join(d, old))
            except Exception:
                pass
        res = dict(window=[round(t0), round(t0 + window)], packets=len(pk), feature_records=len(ft),
                   crc_failures=crc_fail, recompute_mismatches=mism, snapshot=snap,
                   ok=(mism == 0 and crc_fail == 0))
        self.audit_stats["runs"] += 1
        self.audit_stats["failed"] += 0 if res["ok"] else 1
        self.audit_stats["last"] = res
        self.log("audit", res, now)
        return res
