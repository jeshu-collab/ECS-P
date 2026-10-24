#!/usr/bin/env python3
"""Headless scenario tests: drives the simulator + engine with a manual clock (fast, deterministic).
Usage: python3 tests/run_scenarios.py [scenario ...] [-v]"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from aquamesh.config import Config
from aquamesh.engine import Engine
from aquamesh.evidence import Evidence
from aquamesh.sim import Sim, SCENARIOS
from aquamesh.util import Clock


def run(name, dur=600, verbose=False, ablate=(), terrain=None):
    d = tempfile.mkdtemp()
    cfg = Config(d)
    if terrain:
        cfg.d["terrain"]["elev"].update(terrain)
    clock = Clock()
    clock.manual = 1_000_000.0
    ev = Evidence(os.path.join(d, "ev.sqlite"), clock)
    sim = Sim(None, clock)
    eng = Engine(cfg, clock, ev, vision_get=sim.vision_state, gw_status=sim.gateway_status, mode="SIM")
    sim.ingest = eng.ingest_line
    from aquamesh import kb
    sim.edges_fn = lambda: kb.edges_open(cfg["terrain"])
    for a in ablate:
        eng.set_ablation(a, True)
    sim.set_scenario(name, clock.manual)
    first = {}
    for i in range(dur):
        clock.manual += 1.0
        sim.step(clock.manual)
        eng.tick()
        s = eng.snapshot()
        for z, zd in s["zones"].items():
            for lv in ("WATCH", "WARNING", "CRITICAL", "UNKNOWN"):
                if zd["level"] == lv and (z, lv) not in first:
                    first[(z, lv)] = i + 1
        for f in s["facts"]:
            if f["origin"] == "derived" and f["name"] not in first:
                first[f["name"]] = i + 1
        for a in s["alerts"]:
            if a["key"] not in first:
                first[a["key"]] = i + 1
        if verbose and i % 60 == 59:
            z = s["zones"]
            print("t=%3d  river=%-8s drain=%-8s land=%-8s  P(surge)=%.2f P(block)=%.2f P(inund)=%.2f" % (
                i + 1, z["river"]["level"], z["drain"]["level"], z["land"]["level"],
                s["hyp"][2]["p"], s["hyp"][3]["p"], s["hyp"][4]["p"]))
    s = eng.snapshot()
    return eng, s, first, ev


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    v = "-v" in sys.argv
    for name in args or list(SCENARIOS):
        eng, s, first, ev = run(name, verbose=v)
        print("\n=== %s: %s" % (name, SCENARIOS[name]["title"]))
        print("expect:", SCENARIOS[name]["expect"])
        print("first seen (virtual s):", {str(k): t for k, t in sorted(first.items(), key=lambda kv: kv[1])})
        print("final zones:", {z: (d["level"], d["status"], d["confidence"]) for z, d in s["zones"].items()})
        print("verify:", ev.verify())
