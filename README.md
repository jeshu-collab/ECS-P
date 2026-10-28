# AquaMesh v2 — evidence-driven flood digital twin

AquaMesh used to be a live sensor dashboard with a risk formula. It is now a **reasoning system**: it separates *what the hardware measured*, *what the software infers*, *what it predicts* and *what is simulated*; it can say **why** it reached every decision, and it never lets missing or bad data look like "safe".

```
ESP32 FLOOD01 ─┐ LoRa 433 MHz                                   ┌─ buzzer GPIO17 (local, no Internet needed)
ESP32 GAS01  ──┴─► TTGO receiver ─USB serial─► aquamesh_pi.py ───┤
                                                  │ engine/      └─ http://<pi>:8000  dashboard + JSON API
                                         validate → evidence → forward chaining → hysteresis → explain → SQLite
```

## 1. Run it

```bash
cd AquaMesh_Digital_Twin
pip install -r requirements.txt            # pyserial, gpiozero (Pi only)
python3 aquamesh_pi.py                     # real hardware (auto-detects USB serial ports)
python3 aquamesh_pi.py --sim storm         # DEMO: simulated nodes, every value tagged SIMULATED
python3 tests/test_engine.py               # 11 tests, no hardware needed
python3 tools/replay_csv.py logs/readings.csv   # replay your recorded real data through the engine
```
Open `http://localhost:8000` for the monitoring overview. The dashboard reads the Python gateway's `/api/twin/state` contract; for a clearly labelled demo, start it with `python3 aquamesh_pi.py --sim storm`. Change simulator scenarios from the Reasoning view (**9**) while it is running. Other views are **2 Twin**, **3 Nodes**, **4 Inputs**, **5 Trend**, **6 Alerts**, **7 Infer**, and **8 Sky**.

Dashboard data contract, source modes, and integration limits: [`docs/DASHBOARD.md`](AquaMesh_Digital_Twin/docs/DASHBOARD.md).

Only the Python gateway exists now. The Node server was removed from the deployment path (see §8).

## 2. What the system concludes and how (the logic)

### 2.1 Four truth values (the key idea)
| value | meaning | example |
|---|---|---|
| **T** true | evidence holds | `rain_wet` |
| **F** false | evidence says no | dry rain board |
| **U** unknown *now* | node stale, warming up, or sensor needs calibration | uncalibrated soil probe |
| **N** not installed | the sensor does not exist in this deployment | drain, river |

A classic rule engine treats "no data" as false; a dead river sensor would then read "river fine". Here `U` and `N` propagate through every rule (Kleene logic, `engine/logic.py`): one definite `F` refutes a rule, otherwise any `U` keeps the conclusion `U`, and a rule that needs an `N` premise is *inapplicable* (ignored, but listed under "cannot be assessed"). Result: **bad data never looks like NORMAL.** Missing coverage is a separate output.

### 2.2 Forward chaining (data-driven) — "what follows from everything we know?"
Every packet/second: sensors → **evidence facts** (`engine/features.py`) → repeatedly fire the 39 rules in `engine/knowledge.py` until nothing changes (fixpoint, 2–5 rounds) → every derived fact plus a **firing trace**. Rules are plain data, e.g.
`R16 flood_warning ← rain_sustained ∧ ground_saturated`, `R22 flood_critical ← river_backwater ∧ rain_sustained`, `R24 flood_normal ← flood_assessable ∧ ¬flood_watch ∧ ¬flood_warning ∧ ¬flood_critical`. Full table: `docs/RULES.md`. Layers: evidence → interpretation (`rain_sustained`) → hydrology (`drainage_stressed`) → severity ladder → state.

### 2.3 Backward chaining (goal-driven) — "why is this true / not true?"
`engine/logic.py: prove()/explain()`. Click any node in the network or any alert's **WHY?** and the engine builds the AND/OR proof tree for that goal: the winning rule, the evidence under it, which premise **refuted** each blocked rule, and the exact **missing evidence** that stops a goal being decided (e.g. "WARNING cannot be ruled out: ground saturation UNKNOWN — soil calibration required"). Same rule base, opposite direction.

### 2.3b Counterfactual replay — "what if…?"
`counterfactual()` re-runs forward chaining on the **same recorded facts** with overrides (no drain blockage / rain stops / river low / rain node offline) and diffs the conclusions. It is a model experiment, not proof that a factor caused a real event.

### 2.4 Abductive diagnosis — "what best explains the evidence?"
`engine/hypothesis.py`: 7 hypotheses (dry, splash, sustained heavy rain, rain-sensor fault, node outage, blocked drain, gas hazard) scored with prior × likelihood ratios per fact (naive-Bayes style). `U`/`N` facts add **no** evidence. Shown as *relative support*, **not** calibrated probability.

### 2.5 Pattern matching (Stage B of your plan)
`engine/patterns.py`: last 3 min of rain wetness resampled to 12 bins, compared with template shapes (burst-decay, ramp-up, sustained, intermittent, stuck). Returns similarity or **rejects** ("no known pattern" below 55%). Similarity ≠ probability; templates are synthetic until you record labelled storms.

### 2.6 Hysteresis and the state model
`engine/statemachine.py`. Two **separate** outputs:

| Flood severity | rule |
|---|---|
| NORMAL | assessable + no watch/warning/critical rule holds |
| WATCH / WARNING | must persist 10 s to rise, 60 s to fall (no flicker) |
| CRITICAL | **immediate** (verified overflow contact, river backwater, or heavy+saturated+stressed) |
| UNASSESSED | rain node unusable → cannot say |

| System health | rule |
|---|---|
| OK / PARTIAL_COVERAGE (drain/river not installed, by design) | |
| DEGRADED | node silent, packet loss ≥30 %, UART missing |
| SENSOR_FAULT | node talks but reading is stuck/implausible |

Gas has its own state (`gas_hazard`, `gas_watch`) and never implies flooding. Risk number = "LIVE HEURISTIC SCORE (not a probability)"; the old logistic "30-second flood probability" is gone, shown as **UNAVAILABLE** until a validated predictor exists (your Stage C).

### 2.7 Data provenance
Every value carries a tag: **MEASURED** (raw comparison), **ESTIMATED** (needs calibration/statistics), **PREDICTED**, **SIMULATED**, **UNAVAILABLE** (with reason). Raw ADC stays raw until `config/calibration.json` says `verified: true`. mm/h and soil % appear only then.

## 3. What I found in your real data (logs/readings.csv, 2,259 rows)
* **Soil probe reads 0 in 78 % of packets** (spikes to 787), but the old calibration (`dry 4095 → wet 1500`) turned raw 0 into **"100 % saturated"**. That would have inflated risk constantly. Soil is now `CALIBRATION_REQUIRED` → `U`, so it cannot influence decisions until you calibrate (§6).
* Rain raw is 0 when dry and spikes up to ~1900 with decay — a *contact/wetness* signal, not a rate. Treated as wetness; mm/h hidden until calibrated.
* HX711 gave 0 in 3 packets (not ready) — flagged `NOT_READY`; the new firmware omits it instead.
* Replaying the file: NORMAL → WATCH (t=450 s) → WARNING (644 s) → WATCH → NORMAL (1511 s); gas stayed NORMAL except one short WATCH; gaps in the log appear as DEGRADED/UNASSESSED, not NORMAL.

## 4. Folder map
```
AquaMesh_Live/
├─ README.md                  this file
├─ package.json               npm start/demo/test → Python (no Node backend)
└─ AquaMesh_Digital_Twin/
   ├─ aquamesh_pi.py          I/O only: serial, local buzzer, HTTP API, --sim. No reasoning here.
   ├─ engine/                 ALL reasoning (pure Python, no hardware needed)
   │   ├─ packet.py           parse + validate (node id, ranges, counter, duplicates, optional CRC-8)
   │   ├─ calibration.py      versioned calibration; raw vs physical units; SIM profile
   │   ├─ features.py         raw windows → evidence facts T/F/U/N with provenance (stage 1-3)
   │   ├─ logic.py            4-valued logic, forward_chain, prove/explain (backward), counterfactual
   │   ├─ knowledge.py        THE rule base (edit rules here)
   │   ├─ hypothesis.py       abductive diagnosis
   │   ├─ patterns.py         template pattern matching w/ rejection
   │   ├─ statemachine.py     hysteresis
   │   ├─ pipeline.py         Twin: orchestrates stages, alerts, evidence, snapshot for the API
   │   ├─ db.py               SQLite schema + queries
   │   └─ simulator.py        7 scenarios incl. drain/river/overflow (SIMULATED only)
   ├─ config/                 calibration.json (edit), topology.json (verified facts, nulls explicit)
   ├─ public/                 ONLY folder the web server exposes
   │   ├─ index.html app.js styles.css   original isometric dashboard (design kept)
   │   ├─ reasoning.js reasoning.css     view 8 + provenance-aware tiles/alerts/infer
   │   └─ assets/ data/                  map image; synthetic sample storms (Sky card only)
   ├─ firmware/               improved ESP32 + TTGO sketches (see §7)
   ├─ tools/                  replay_csv.py, calibrate.py, dump_rules.py
   ├─ tests/test_engine.py    11 tests
   ├─ docs/RULES.md           generated rule table
   ├─ logs/                   readings.csv (export format, kept), aquamesh.db (SQLite, auto-created)
   ├─ integration/            example-state.json = real shape of /api/twin/state
   └─ legacy/                 old Node server, deprecated
```

## 5. Dashboard guide
1 Overview (gateway snapshot, nodes, health, event feed; camera/Gemini explicitly unavailable until backend support exists) · 2 Twin (map; rain animation intensity is visual-only, from 30 s raw wetness) · 3 Nodes (raw first, data-quality row) · 4 Inputs · 5 Trend · 6 Alerts (reason codes + **WHY?**) · 7 Infer (Observed / Inferred / Predicted columns; validated probability = UNAVAILABLE) · 8 Sky (synthetic samples, labelled illustrative) · **9 Reason**:
* top strip: flood / gas / health states with hysteresis countdown, evidence coverage, calibration + rule versions, "cannot be assessed", data quality chips, simulator controls
* **inference network**: columns = layers, animated green edge = rule fired, dashed amber = unknown, grey dashed = not installed; click a node → proof panel
* **forward-chaining trace** · **best explanation** · **decision history** (timeline strips from SQLite; click a moment or transition → *replay* that exact decision with stored facts, or open its stored alert evidence) · **what-if** buttons

## 6. Calibration (do this before trusting soil/mm/h)
1. Run the gateway; record ~1 min with the probe dry, ~1 min wet on purpose. 2. `python3 tools/calibrate.py logs/readings.csv`. 3. Edit `config/calibration.json`: `soil.direction` (`lower_is_wetter`/`higher_is_wetter`), `dry_raw`, `wet_raw`, `verified:true`; bump `version` (stored with every reading/decision). Rain mm/h needs a real gauge: set `mm_per_hour_at_4095` only if you have one.

## 7. Hardware (firmware/) — read the caveat
Your original `.ino` files were not in the zip, so these were written from the packet format your parser accepts. **I could not compile or flash them.** Check pin numbers, then test. Fully backward-compatible with the old parser.
* `node1_flood`, `node2_gas_soil`: median-of-5 sampling; alert **hysteresis** (2 up / 3 down); HX711 omitted when not ready; `UP=` uptime, `FW=`; **CRC-8** (`C=`, C and Python versions cross-checked identical); hardware **watchdog**; TX jitter; optional `VBAT=`; MQ **warm-up** flag (cold MQ reads wrong); optional **`OVF=`** float switch (turns on the real "surface overflow" rule); soil sent raw.
* `gateway_ttgo_receiver`: appends `,RSSI=,SNR=` so link quality shows in the dashboard.
Real sensors worth adding next (each unlocks rules that already exist): water-level sensor in the drain (`DRAIN01 LVL=,FLOW=`) and river (`RIVER01 LVL=`) — enabled by setting `installed: true` for that node in `config/topology.json`; a float switch on GAS01.

## 8. Security / robustness changes
Server serves only `public/` (source, config, DB, logs → 404); telemetry can only enter by serial (no POST endpoint); 8 KB body cap; unknown node IDs/out-of-range/CRC-failed packets rejected and logged to `rejected`; Node server removed. Buzzer logic is local to the Pi: node `STATUS=ALERT` pulses as before, and CRITICAL/gas hazard repeats every 10 s, independent of browser or Internet.

## 9. API
`GET /api/twin/state` (back-compatible + `assessment`) · `/assessment` · `/why?goal=flood_warning` · `/events` · `/event?id=` · `/timeline?minutes=` · `/replay?id=` · `/api/sim/scenarios` · `/api/db/counts` · `POST /api/twin/counterfactual {overrides, assessment_id}` · `POST /api/sim/control {scenario, blockage}` (sim only).
SQLite tables: `telemetry` (raw + calibration version), `rejected`, `assessments` (leaf facts, metrics), `transitions`, `events` (evidence JSON with reason codes, provenance, data quality, calibration, rule version, node health).

## 10. Honest limits
* Thresholds (wet 15 % of 60 s, persistent 35 % of 3 min, heavy raw 1000…) are engineering defaults, **not fitted to labelled floods**. No ML predictor yet (Stage C needs labelled data); the hypothesis weights and pattern templates are hand-set.
* Verified here: 11 unit tests, all 7 scenarios, replay of your real CSV, dashboard in headless Chromium (sim + no-hardware live), C/Python CRC match. **Not verified:** real hardware end-to-end, firmware compile, buzzer GPIO.
* Drain/river/overflow exist only in the simulator; the rain→drain→area→river chain on the map animates only for links the model reports ACTIVE.

## 11. Demo script for your professor (5 min)
1. `--sim storm`, view 8: show NORMAL → WATCH → WARNING as the network lights up left to right. 2. Click `flood_warning`: backward-chaining proof. 3. Switch to `blockage`, then drag the slider to 0%: CRITICAL vs WARNING. 4. Click a past transition in Decision history → replay; press **No drain blockage** → counterfactual. 5. `sensor_fault`, `node_loss`: state becomes SENSOR_FAULT / UNASSESSED, never NORMAL. 6. Restart without `--sim`: real data, soil `CALIBRATION_REQUIRED`, drain/river "cannot be assessed".
