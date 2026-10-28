import io, os, sys, unittest, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from engine import calibration as C
from engine.logic import T, F, U, N, k_and, k_or
from engine.packet import parse_line, validate, crc8
from engine.pipeline import Twin
from engine.simulator import SimRunner, VClock, SCENARIOS
from engine.statemachine import Hysteresis


def live_twin(clock):
    t = Twin(":memory:", C.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "calibration.json")), clock=clock)
    t.uart_ok = True
    return t


def feed(t, line):
    d, tx = parse_line(line); return t.ingest(d, tx, line)


def sim(sc, steps, blockage=None):
    vc = VClock(1000.0)
    t = Twin(":memory:", C.SIM, clock=vc, mode="SIMULATED", allow_future_nodes=True, installed={"FLOOD01": 1, "GAS01": 1, "DRAIN01": 1, "RIVER01": 1, "OVERFLOW": 1})
    t.uart_ok = True
    r = SimRunner(t, vc); r.control(sc, blockage)
    seen = []
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(steps):
            r.tick(); a = t.last["a"]; seen.append((a["flood"], a["gas"], a["health"]))
    return t, seen


class Logic(unittest.TestCase):
    def test_four_valued(self):
        self.assertEqual(k_and([T, U]), U); self.assertEqual(k_and([F, U]), F); self.assertEqual(k_and([T, N]), N)
        self.assertEqual(k_or([F, U]), U); self.assertEqual(k_or([T, U]), T); self.assertEqual(k_or([N, N]), N); self.assertEqual(k_or([N, F]), F)


class Packets(unittest.TestCase):
    def test_crc_and_range(self):
        body = "FROM=FLOOD01,PKT=5,STATUS=NORMAL,RAIN=12"
        ok, e, _ = validate(*parse_line(body + ",C=%02X" % crc8(body.encode())))[0:3]
        self.assertIsNotNone(ok)
        self.assertIsNone(validate(*parse_line(body + ",C=00"))[0] if crc8(body.encode()) else None)
        self.assertIsNone(validate(*parse_line("FROM=FLOOD01,PKT=5,RAIN=99999"))[0])
        self.assertIsNone(validate(*parse_line("FROM=EVIL99,PKT=1,RAIN=5"))[0])
        self.assertIsNone(validate(*parse_line("FROM=DRAIN01,PKT=1,LVL=5"))[0])      # future nodes refused on real hardware

    def test_old_format_still_accepted(self):
        self.assertIsNotNone(validate(*parse_line("FROM=GAS01,PKT=3,STATUS=NORMAL,MQ=860,SOIL=0"))[0])


class Reasoning(unittest.TestCase):
    def test_dry_live_is_normal_and_missing_hardware_is_reported_not_zero(self):
        vc = VClock(0.0); t = live_twin(vc)
        with contextlib.redirect_stdout(io.StringIO()):
            for i in range(14):
                vc.t = i * 5.0
                feed(t, f"FROM=FLOOD01,PKT={i},STATUS=NORMAL,RAIN=0,HX=1000"); feed(t, f"FROM=GAS01,PKT={i},STATUS=NORMAL,MQ=860,SOIL=0"); t.assess()
        a = t.assessment_public(t.last)
        self.assertEqual(a["flood_state"], "NORMAL")
        self.assertIn("River backwater detection", a["not_assessable"]); self.assertEqual(t.last["wm"]["river_backwater"], N)
        soil = [x for x in a["indicators"] if x["label"] == "Soil moisture"][0]
        self.assertIsNone(soil["value"]); self.assertEqual(soil["type"], "UNAVAILABLE")      # uncalibrated soil is never shown as a %
        self.assertTrue(any(x["label"].startswith("30-second") and x["type"] == "UNAVAILABLE" for x in a["indicators"]))

    def test_node_loss_is_unassessed_not_normal(self):
        _, seen = sim("node_loss", 60)
        self.assertEqual(seen[-1][0], "UNASSESSED"); self.assertEqual(seen[-1][2], "DEGRADED")

    def test_stuck_sensor_is_fault_not_safe(self):
        _, seen = sim("sensor_fault", 40)
        self.assertEqual(seen[-1][2], "SENSOR_FAULT"); self.assertNotEqual(seen[-1][0], "NORMAL")

    def test_gas_is_separate_from_flood(self):
        _, seen = sim("gas_leak", 30)
        self.assertEqual(seen[-1][1], "WARNING"); self.assertEqual(seen[-1][0], "NORMAL")

    def test_storm_escalates_and_blockage_is_worse(self):
        _, a = sim("storm", 45); _, b = sim("blockage", 45)
        rank = {"UNASSESSED": -1, "NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
        self.assertGreaterEqual(rank[a[-1][0]], 1); self.assertGreaterEqual(rank[b[-1][0]], rank[a[-1][0]])
        self.assertEqual(b[-1][0], "CRITICAL")

    def test_counterfactual_and_backward_proof(self):
        t, _ = sim("blockage", 28)
        why = t.why("flood_warning"); self.assertEqual(why["value"], T); self.assertTrue(why["text"])
        cf = t.counterfactual({"rain_wet": F})
        self.assertTrue(any(c["fact"] == "flood_warning" and c["after"] == F for c in cf["changed"]))
        self.assertEqual(t.why("rain_wet")["value"], T)                                  # real state untouched by the experiment

    def test_every_transition_has_stored_evidence(self):
        t, _ = sim("storm", 40)
        ev = [e for e in t.db.events(100) if e["goal"] and e["event_type"].startswith("FLOOD_RISK_W")]
        self.assertTrue(ev)
        full = t.db.event_row(ev[0]["id"]); self.assertIn("reason_codes", full["evidence_json"]); self.assertIn("calibration", full["evidence_json"])
        self.assertIsNotNone(t.replay(ev[0]["assessment_id"]))


class Hyst(unittest.TestCase):
    def test_no_flicker_and_instant_critical(self):
        h = Hysteresis("NORMAL"); s, tr = h.step("WATCH", 0); self.assertEqual(s, "NORMAL")
        s, tr = h.step("NORMAL", 3); s, tr = h.step("WATCH", 4); s, tr = h.step("WATCH", 15); self.assertEqual(s, "WATCH")
        s, tr = h.step("CRITICAL", 16); self.assertEqual(s, "CRITICAL")
        s, tr = h.step("WARNING", 17); s, tr = h.step("WARNING", 70); self.assertEqual(s, "CRITICAL")   # needs 60 s to step down
        s, tr = h.step("WARNING", 78); self.assertEqual(s, "WARNING")


if __name__ == "__main__":
    unittest.main(verbosity=2)
