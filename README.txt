AQUAMESH MVP - README
=====================
Web platform that runs on the Raspberry Pi: reads your LoRa packets via the TTGO, runs your camera
vision, reasons over everything (forward + backward chaining, temporal reasoning, Bayesian fusion),
and shows panels for collected data, trends, alerts and explained inferences.
Design and science: see APPROACH.txt.

QUICK START (2 minutes, no hardware - the demo)
-----------------------------------------------
  cd aquamesh-mvp
  python3 run.py --sim --speed 6  # simulated sensors + live camera (if available)
  open http://localhost:8000   (from another machine: http://<pi-ip>:8000)
Pick scenarios in the "Experiments" panel. The header shows SIMULATED so nobody mistakes it for live.

LIVE MODE ON THE PI
-------------------
  sudo apt install -y python3-opencv python3-picamera2 python3-numpy python3-serial
  # TTGO plugged in by USB; it must print each received packet line to serial at 115200
  # (firmware/ttgo_receiver.ino is a reference sketch; yours is fine if it prints FROM=...,C=XX)
  python3 run.py                    # auto-detects /dev/ttyACM* or /dev/ttyUSB*
  python3 run.py --serial /dev/ttyUSB0
  python3 run.py --no-camera        # sensors only
  # SSH without a screen:  ssh -L 8000:localhost:8000 pi@<pi-ip>   then http://localhost:8000

First-time vision setup (same as your waterwatch flow; the camera tab in the dashboard is MJPEG):
  1. Open "Camera" in Model view. Draw regions via the API or reuse your regions.json
     (POST /api/regions, /api/suggest, /api/learn exist; copy regions.json and baseline.npz
     from waterwatch into data/ to reuse them - same format).
  2. POST /api/learn with the model DRY and hands out of view (~10 s).
  A polygon region editor is NOT in this dashboard. Use your original waterwatch GUI once to draw
  regions and learn the baseline (kept as tools/waterwatch_original.py; stop AquaMesh first because
  both need the camera, and run it from inside data/ so regions.json and baseline.npz land there):
      cd data && python3 ../tools/waterwatch_original.py     # open :8000, edit regions, learn baseline
  Then Ctrl-C it and start run.py. AquaMesh reads the same two files.

CALIBRATE (important, 1 minute)
-------------------------------
  Dashboard -> "Terrain / causal graph & calibration". With the rain sensor dry press "rain = dry",
  wet it, press "rain = wet". Same for soil. Default assumption: higher raw value = wetter.
  Enter your model's real elevations (cm) for river / drain / land in the same panel.

RUN AS A SERVICE
----------------
  sudo cp systemd/aquamesh.service /etc/systemd/system/   (edit User and paths first)
  sudo systemctl enable --now aquamesh
  journalctl -u aquamesh -f
  Optional env: AQUAMESH_TOKEN (protects all control/POST calls; open the page as /?token=...),
  PUSH_URL (cloud sync of alerts; buffered if offline), TTGO_PORT, PORT.

REPLAY / ABLATION ON A RECORDED RUN
-----------------------------------
  python3 run.py --replay data/evidence.sqlite --speed 10 --disable mpu
  (--disable accepts camera,rain,soil,hx,mpu,gas; replay writes to a separate log)

TESTS
-----
  python3 tests/test_expectations.py     30 assertions over all experiments (should print "0 failed")
  python3 tests/run_scenarios.py -v      readable timeline of every scenario
  python3 tools/verify_log.py data/evidence.sqlite

WHAT IS WHERE
-------------
  run.py                    entry point
  aquamesh/protocol.py      packet parse, CRC-8, session tracking (loss / dup / replay / reboot)
  aquamesh/gateway.py       TTGO serial reader (auto-reconnect)
  aquamesh/vision.py        your waterwatch logic, extended (river cover, drain flow), as a module
  aquamesh/health.py        sensor health and reliability weights
  aquamesh/features.py      trend tests, persistence, baselines -> events
  aquamesh/kb.py            rules, forward chaining, backward chaining, terrain graph
  aquamesh/bn.py            Bayesian network + abduction
  aquamesh/engine.py        orchestration, risk, confidence, alerts, timeline
  aquamesh/evidence.py      hash-chained log, audits, outbox
  aquamesh/sim.py           physically-coupled simulator + scenarios
  aquamesh/server.py        HTTP API;  aquamesh/static/index.html  dashboard
  aquamesh/config.py        thresholds, calibration, terrain (saved to data/config.json)
  data/                     created at run time (logs, config, baselines, audit snapshots)

API (JSON; all GET unless noted)
--------------------------------
  /api/snapshot  current state (zones, facts, firings, hypotheses, health, alerts, timeline)
  /api/history?n=900   1 Hz series        /api/export.csv   all series as CSV
  /api/why?goal=land_warning   backward-chaining proof tree + narrative
  /api/evidence?n=40&kind=alert,decision   /api/verify   /api/audit   /api/tamper_demo
  POST /api/calibrate {sensor,point}  /api/terrain {elev,connected}  /api/ablate {source,off}
  POST /api/scenario {name,speed} (sim)  /api/note {text}  /api/regions /api/learn /api/suggest
  /stream.mjpg  /snapshot.jpg  (camera)

TROUBLESHOOTING
---------------
  "no serial port found"      check USB cable / TTGO_PORT; user must be in group dialout
  Nodes show NO_DATA          open Evidence panel: are packet lines arriving? CRC rejects mean the
                              line format differs (the packet text must be intact up to ",C=XX")
  Everything DEGRADED         a source is missing; the zone cards list which one and why
  Camera "needs_baseline"     learn the dry baseline (POST /api/learn)
  Slow vision                 lower PROC_W/PROC_H in aquamesh/vision.py (then relearn baseline)
