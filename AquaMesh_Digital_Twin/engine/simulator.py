"""
Scenario simulator.  Everything it produces is tagged SIMULATED and is only used when aquamesh_pi.py runs with --sim.
It adds the nodes that do not exist in hardware (drain, river, overflow switch) so every rule can be demonstrated,
including the counterfactual "same rain, but without the drain blockage".
Virtual time advances 5 s per tick (one LoRa transmit period) so a storm plays in about a minute of wall-clock time.
"""
import random, threading, time

SCENARIOS = {
    "calm":         "Dry weather, everything normal",
    "storm":        "Rain builds to a heavy storm; soil saturates; drain fills",
    "blockage":     "Same storm, but the drain is 75% blocked",
    "river_surge":  "Storm plus river rising: backwater onto the drain",
    "sensor_fault": "Rain sensor sticks at a high value",
    "node_loss":    "FLOOD01 stops transmitting mid-storm",
    "gas_leak":     "Gas level climbs past the alarm threshold",
}


class World:
    """Tiny hydrology: rain -> drain (limited capacity) -> low area -> river."""
    def __init__(self):
        self.reset("calm")

    def reset(self, scenario="calm"):
        self.scenario, self.t, self.drain, self.soil, self.river, self.area_depth, self.gas = scenario, 0, 1.0, 0.15, 6.0, 0.0, 800.0
        self.blockage = 0.75 if scenario == "blockage" else 0.5 if scenario == "river_surge" else 0.05
        self.stuck_value = None

    def intensity(self):  # 0..1
        t, s = self.t, self.scenario
        if s in ("calm", "gas_leak"):
            return 0.0
        ramp = min(1.0, max(0.0, (t - 30) / 150))
        return ramp ** 1.3 if t < 330 else max(0.0, 1.0 - (t - 330) / 120)

    def step(self, dt=5.0):
        self.t += dt
        i = self.intensity()
        inflow = i * 2.4
        outflow = (1 - self.blockage) * 0.5 * self.drain
        self.drain = max(0.5, min(14.0, self.drain + (inflow - outflow) * dt / 5))
        self.soil = max(0.1, min(1.0, self.soil + (i * 0.04 - 0.004) * dt / 5))
        rb = 0.3 if self.scenario == "river_surge" and self.t > 120 else 0.0
        self.river = max(4.0, min(40.0, self.river + (0.05 * max(0, self.drain - 6) + rb - 0.04) * dt / 5))
        self.area_depth = max(0.0, self.area_depth + (0.5 if self.drain >= 13.5 else -0.2) * dt / 5)
        if self.scenario == "gas_leak" and self.t > 40:
            self.gas = min(3000, self.gas + 90)
        if self.scenario == "sensor_fault" and self.t > 60 and self.stuck_value is None:
            self.stuck_value = 3300

    def packets(self, rng, pkt):
        i = self.intensity()
        rain = int(max(0, i * 1900 + rng.uniform(-120, 120)) if i > 0.02 else 0)
        if rng.random() < 0.18 * (1 - i) and i > 0.02:
            rain = 0                                   # intermittent contact, like the real rain board
        if self.stuck_value is not None:
            rain = self.stuck_value
        soil = int(3800 - self.soil * 2600 + rng.uniform(-40, 40))
        out = []
        if not (self.scenario == "node_loss" and self.t > 150):
            out.append(f"FROM=FLOOD01,PKT={pkt},STATUS={'ALERT' if rain > 500 else 'NORMAL'},RAIN={rain},HX={192000 + rng.randint(-300, 300)}")
        out.append(f"FROM=GAS01,PKT={pkt},STATUS={'ALERT' if self.gas > 1500 else 'NORMAL'},MQ={int(self.gas + rng.uniform(-15, 15))},SOIL={soil},OVF={1 if self.area_depth > 0.3 else 0}")
        out.append(f"FROM=DRAIN01,PKT={pkt},STATUS=NORMAL,LVL={round(self.drain)},FLOW={max(0, int((1 - self.blockage) * 40 * (1 if self.drain > 1.2 else 0.1)))}")
        out.append(f"FROM=RIVER01,PKT={pkt},STATUS=NORMAL,LVL={round(self.river)}")
        return out


class SimRunner(threading.Thread):
    def __init__(self, twin, vclock, speed=0.8, seed=7):
        super().__init__(daemon=True)
        self.twin, self.vclock, self.speed, self.rng = twin, vclock, speed, random.Random(seed)
        self.world, self.pkt, self.paused = World(), 0, False

    def control(self, scenario=None, blockage=None):
        if scenario in SCENARIOS:
            with self.twin.lock:
                self.world.reset(scenario)
                self.twin.nodes.clear(); self.twin.flood_sm.cur = self.twin.gas_sm.cur = "UNASSESSED"
                self.twin.flood_sm.pending = self.twin.gas_sm.pending = None
                self.twin.started = self.twin.clock()
                self.twin._last_sig = None
        if blockage is not None:
            self.world.blockage = max(0.0, min(1.0, float(blockage)))
        return {"scenario": self.world.scenario, "blockage": self.world.blockage, "t": self.world.t}

    def tick(self):
        from .packet import parse_line
        self.world.step(5.0)
        self.vclock.advance(5.0)
        self.pkt += 1
        for line in self.world.packets(self.rng, self.pkt):
            d, text = parse_line(line)
            self.twin.ingest(d, text, line)
        self.twin.assess()

    def run(self):
        while True:
            if not self.paused:
                self.tick()
            time.sleep(self.speed)


class VClock:
    def __init__(self, start=None):
        self.t = start if start is not None else time.time()
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt
