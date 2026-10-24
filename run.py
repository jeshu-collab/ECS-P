#!/usr/bin/env python3
"""AquaMesh entry point.

  python3 run.py                      live: TTGO serial + Pi camera
  python3 run.py --sim                demo/simulator (no hardware needed)
  python3 run.py --sim --scenario compound --speed 5
  python3 run.py --no-camera          live sensors only
  python3 run.py --replay data/evidence.sqlite --speed 10 --disable mpu     ablation on a recorded run
"""
import argparse
import json
import os
import threading
import time
import urllib.request

from aquamesh.config import Config, ROOT
from aquamesh.engine import Engine
from aquamesh.evidence import Evidence
from aquamesh.server import Ctx, serve
from aquamesh.util import Clock


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--scenario", default="normal")
    ap.add_argument("--speed", type=float, default=4.0, help="simulation / replay speed-up")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--serial", default=os.environ.get("TTGO_PORT"))
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--data", default=os.environ.get("AQUAMESH_DATA", os.path.join(ROOT, "data")))
    ap.add_argument("--replay")
    ap.add_argument("--disable", default="", help="comma list of sources to ablate: camera,rain,soil,hx,mpu,gas")
    a = ap.parse_args()
    os.environ["AQUAMESH_DATA"] = a.data

    cfg = Config(a.data)
    ctx = Ctx()
    ctx.cfg = cfg
    mode = "SIMULATED" if a.sim else "REPLAY" if a.replay else "LIVE"
    ctx.clock = Clock(a.speed if (a.sim or a.replay) else 1.0)
    dbname = "evidence_sim.sqlite" if a.sim else "evidence_replay.sqlite" if a.replay else "evidence.sqlite"
    if a.replay and os.path.exists(os.path.join(a.data, dbname)):
        os.remove(os.path.join(a.data, dbname))
    ctx.ev = Evidence(os.path.join(a.data, dbname), ctx.clock)

    if a.sim:
        from aquamesh.sim import Sim, SimRunner
        ctx.sim = Sim(None, ctx.clock)
        # In simulation mode, keep sensor/decision values simulated but still
        # start the physical camera unless --no-camera was explicitly supplied.
        # This ensures /stream.mjpg exists when the dashboard is run with --sim.
        if not a.no_camera:
            try:
                from aquamesh.vision import Vision
                ctx.vision = Vision()
                print("Camera vision thread started in simulation mode.", flush=True)
            except Exception as ex:
                ctx.vision = None
                print("Camera unavailable in simulation mode: %s" % (ex,), flush=True)
        eng = Engine(cfg, ctx.clock, ctx.ev, vision_get=ctx.sim.vision_state, gw_status=ctx.sim.gateway_status, mode=mode)
        ctx.sim.ingest = eng.ingest_line
        from aquamesh import kb
        ctx.sim.edges_fn = lambda: kb.edges_open(cfg["terrain"])
        ctx.sim.set_scenario(a.scenario)
        SimRunner(ctx.sim, ctx.clock).start()
    elif a.replay:
        from aquamesh.replay import Replay
        eng = Engine(cfg, ctx.clock, ctx.ev, mode=mode)
        ctx.replay = Replay(a.replay, eng, ctx.clock, a.speed)
        eng.vision_get, eng.gw_status = ctx.replay.vision_state, ctx.replay.gateway_status
        ctx.replay.start()
    else:
        from aquamesh.gateway import SerialGateway
        ctx.gateway = SerialGateway(None, a.serial, a.baud, ctx.clock)
        vget = None
        if not a.no_camera:
            from aquamesh.vision import Vision
            ctx.vision = Vision()
            vget = ctx.vision.state
        eng = Engine(cfg, ctx.clock, ctx.ev, vision_get=vget, gw_status=ctx.gateway.status,
                     vision_jpeg=ctx.vision.jpeg if ctx.vision else None, mode=mode)
        ctx.gateway.on_line = eng.ingest_line
        ctx.gateway.start()
    ctx.engine = eng
    for s in filter(None, a.disable.split(",")):
        eng.set_ablation(s.strip(), True)
    ctx.ev.log("system", dict(action="start", mode=mode, args=vars(a)))

    def ticker():
        last = 0
        while True:
            now = ctx.clock.now()
            if now - last >= 1.0:
                last = now
                try:
                    eng.tick()
                except Exception as e:           # never die
                    print("tick error:", repr(e), flush=True)
            time.sleep(0.05)
    threading.Thread(target=ticker, daemon=True).start()

    push = os.environ.get("PUSH_URL")
    if push:                                     # outbox -> cloud, survives internet loss
        def pusher():
            while True:
                time.sleep(10)
                rows = ctx.ev.outbox_take()
                if not rows:
                    continue
                try:
                    req = urllib.request.Request(push, json.dumps([json.loads(p) for _, p in rows]).encode(),
                                                 {"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=5).close()
                    ctx.ev.outbox_done([i for i, _ in rows])
                except Exception:
                    pass                          # keep buffered, retry later
        threading.Thread(target=pusher, daemon=True).start()

    srv = serve(ctx, a.port)
    print("AquaMesh [%s] dashboard on http://0.0.0.0:%d" % (mode, a.port), flush=True)
    try:
        srv.serve_forever()
    finally:
        ctx.ev.flush()


if __name__ == "__main__":
    main()
