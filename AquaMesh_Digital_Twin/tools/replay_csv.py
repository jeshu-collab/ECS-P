#!/usr/bin/env python3
"""Replay a recorded readings.csv through the reasoning engine (offline, faster than real time) and print every decision.
   python3 tools/replay_csv.py logs/readings_sample_2026-10-19.csv [--db out.db]"""
import argparse, csv, os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from engine import calibration as C
from engine.packet import parse_line
from engine.pipeline import Twin
from engine.simulator import VClock

ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--db", default=":memory:"); a = ap.parse_args()
rows = list(csv.DictReader(open(a.csv)))
vc = VClock(0.0)
t = Twin(a.db, C.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "calibration.json")), clock=vc)
t.uart_ok = True
t0 = datetime.fromisoformat(rows[0]["time_iso"]).timestamp()
for r in rows:
    vc.t = datetime.fromisoformat(r["time_iso"]).timestamp()
    parts = [f"FROM={r['from']}", f"PKT={r['pkt']}", f"STATUS={r['status']}"] + [f"{k}={r[c]}" for k, c in (("RAIN", "rain_raw"), ("HX", "hx_raw"), ("MQ", "mq_raw"), ("SOIL", "soil_raw")) if r.get(c) not in (None, "")]
    line = ",".join(parts); d, tx = parse_line(line); t.ingest(d, tx, line); t.assess()
print("\n--- state transitions (seconds from start) ---")
for tr in reversed(t.db.transitions(500)):
    print(f"{int(tr['ts'] - t0):>6}s  {tr['domain']:5} {tr['from_state']:>10} -> {tr['to_state']:<10} {tr['reasons_json']}")
print("\nrows stored:", t.db.counts())
