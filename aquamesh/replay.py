"""Replay a recorded evidence log through the engine (e.g. for ablation: same event, fewer sources)."""
import json
import sqlite3
import threading
import time


class Replay(threading.Thread):
    def __init__(self, db_path, engine, clock, speed=10.0):
        super().__init__(daemon=True)
        self.engine, self.clock, self.speed = engine, clock, speed
        db = sqlite3.connect(db_path)
        self.rows = [(ts, kind, json.loads(p)) for ts, kind, p in
                     db.execute("SELECT ts,kind,payload FROM rec WHERE kind IN ('packet','feat') ORDER BY id")]
        db.close()
        self.vis = None
        self.done = False
        self.t_first = self.rows[0][0] if self.rows else 0
        clock.set_time(self.t_first)
        clock.set_speed(speed)

    def vision_state(self):
        return self.vis

    def gateway_status(self):
        return dict(connected=True, port="REPLAY", error="")

    def run(self):
        for ts, kind, d in self.rows:
            while self.clock.now() < ts:
                time.sleep(0.02)
            if kind == "packet":
                self.engine.ingest_line(d["line"], ts)
            else:
                v = d.get("vals", {})
                if "river_cover" in v:
                    self.vis = dict(t=ts, status="running", fps=6.0,
                                    river=dict(state="FLOWING", cover=v.get("river_cover"), flow=v.get("river_flow")),
                                    drain=dict(state="FLOWING", level=v.get("drain_level"), flow=v.get("drain_flow")),
                                    land=dict(state="DRY", still=v.get("land_still")))
        self.done = True
