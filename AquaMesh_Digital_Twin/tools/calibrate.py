#!/usr/bin/env python3
"""Summarise recorded RAW values so you can fill config/calibration.json with real numbers.
   1) Record ~1 min with the sensor DRY, then ~1 min fully WET (soil in water / rain board wet) while the gateway runs.
   2) python3 tools/calibrate.py logs/readings.csv   -> percentiles per sensor; pick dry_raw / wet_raw; set verified:true."""
import csv, sys, statistics as st
rows = list(csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "logs/readings.csv")))
for col, label in (("rain_raw", "RAIN (FLOOD01)"), ("soil_raw", "SOIL (GAS01)"), ("mq_raw", "MQ gas (GAS01)"), ("hx_raw", "HX711 (FLOOD01)")):
    v = sorted(float(r[col]) for r in rows if r.get(col) not in (None, ""))
    if not v: continue
    q = lambda p: v[min(len(v) - 1, int(p * len(v)))]
    zeros = sum(1 for x in v if x == 0)
    print(f"{label:16} n={len(v):5} min={v[0]:.0f} p10={q(.1):.0f} median={q(.5):.0f} p90={q(.9):.0f} max={v[-1]:.0f} zeros={zeros} ({100*zeros/len(v):.0f}%)")
print("\nHints: a probe that reads 0 most of the time is shorted/disconnected or reads LOW when wet - decide direction by wetting it on purpose.\n"
      "soil.direction = 'lower_is_wetter' or 'higher_is_wetter'; dry_raw = median in air/dry soil; wet_raw = median in water.")
