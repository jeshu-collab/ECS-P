#!/usr/bin/env python3
"""Assertion tests: each experiment must reach the right conclusion for the right reasons.
Run:  python3 tests/test_expectations.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from run_scenarios import run
from aquamesh.protocol import make_line, parse_line, NodeLink

fails = []
def check(name, cond, info=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  " + info) if info and not cond else ""))
    if not cond:
        fails.append(name)

def lv(s, z): return s["zones"][z]["level"]
def derived(s): return {f["name"] for f in s["facts"] if f["origin"] == "derived"}
def alerts(s): return {a["key"] for a in s["alerts"] if a["active"]}

_, s, f, ev = run("normal", 400)
check("normal: all zones NORMAL/OK", all(lv(s, z) == "NORMAL" and s["zones"][z]["status"] == "OK" for z in s["zones"]))
check("normal: no active alerts", not alerts(s))
check("normal: evidence chain verifies", ev.verify()["ok"])

_, s, f, ev = run("heavy_rain", 200)
check("heavy rain: river surge suspected", "river_surge_suspected" in derived(s))
check("heavy rain: NO drain blockage inferred", "drain_blockage_suspected" not in derived(s))
check("heavy rain: temporal order rain -> drain recorded", "drain_rise_follows_rain" in derived(s) or f.get("drain_rise_follows_rain"))

_, s, f, ev = run("drain_blockage", 300)
check("blockage: obstruction suspected via R05/R05b", "drain_blockage_suspected" in derived(s))
check("blockage: drain at least WARNING", s["zones"]["drain"]["lvl"] >= 2)

_, s, f, ev = run("compound", 320)
check("compound: compound_event fires", "compound_event" in derived(s))
check("compound: land inundation risk propagates", "land_inundation_risk_rising" in derived(s))
check("compound: land >= WARNING", s["zones"]["land"]["lvl"] >= 2)
check("compound: P(inundation) high", s["hyp"][4]["p"] > 0.8)

_, s, f, ev = run("sensor_fault", 300)
check("sensor fault: flagged", "chan:rain" in alerts(s))
check("sensor fault: no false flood", all(lv(s, z) == "NORMAL" for z in s["zones"]))
check("sensor fault: confidence reduced", s["zones"]["land"]["confidence"] < 1.0)

_, s, f, ev = run("node_failure", 300)
check("node failure: offline alert", "node:flood" in alerts(s))
check("node failure: never silently safe (DEGRADED)", s["zones"]["land"]["status"] in ("DEGRADED", "UNKNOWN"))

_, s, f, ev = run("replay_spoof", 400)
check("replay: rejected & alerted", any(k.startswith("replay:") for k in f if isinstance(k, str)))
check("replay: bad-CRC packet rejected", any(k.startswith("crc:") for k in f if isinstance(k, str)))
check("spoof: rain sensor flagged uncorroborated", "chan:rain" in f)
check("spoof: no false flood from spoofed rain", all(lv(s, z) == "NORMAL" for z in s["zones"]))

_, s, f, ev = run("gateway_loss", 300)
check("gateway loss: explicit alert", "gateway" in alerts(s))
check("gateway loss: degraded, not silently safe", s["zones"]["land"]["status"] != "OK")

_, s, f, ev = run("camera_loss", 300)
check("camera loss: UNKNOWN drain (not 'safe')", s["zones"]["drain"]["level"] == "UNKNOWN")

_, s, f, ev = run("compound", 320, ablate=["camera"])
check("ablation: camera off lowers confidence & coverage", s["zones"]["land"]["confidence"] < 0.6)
_, s, f, ev = run("compound", 320, terrain={"land": 8.0})
check("elevation: raising land closes paths, land stays below WARNING", s["zones"]["land"]["lvl"] < 2 and not s["terrain"]["open"]["edge_drain_land_open"])

# integrity: tamper detection
_, s, f, ev = run("compound", 300)
t = ev.tamper_demo()
check("evidence log: tampering detected", t["before"]["ok"] and not t["after"]["ok"])
# protocol
l = make_line("FROM=FLOOD01,PKT=5,STATUS=NORMAL,RAIN=1,SOIL=2,UP=25,FW=2.1")
check("protocol: CRC ok", parse_line(l)["crc_ok"])
check("protocol: altered value fails CRC", not parse_line(l.replace("RAIN=1", "RAIN=9"))["crc_ok"])
nl = NodeLink("FLOOD01"); p = parse_line(l)
check("session: first accepted, exact duplicate rejected", nl.accept(p, 100.0) == "ok" and nl.accept(p, 101.0) == "duplicate")
print("\n%d failed" % len(fails)); sys.exit(1 if fails else 0)
