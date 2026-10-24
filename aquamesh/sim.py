"""Physically-coupled simulator of the miniature flood model (DEMO / TEST ONLY - clearly labelled).

Hidden state (rain R, soil S, river Q, drain level D, land wet L, blockage b) evolves by simple
ODEs; the simulator then emits FLOOD01 / GAS01 packets (with real CRC-8) and a vision-state dict in
exactly the format of the real hardware, so the reasoning engine cannot tell the difference.
"""
import math
import random
import threading
import time

from .protocol import make_line


def ramp(t, a, b, y0, y1):
    if t <= a:
        return y0
    if t >= b:
        return y1
    return y0 + (y1 - y0) * (t - a) / (b - a)


SCENARIOS = {
    "normal": dict(title="Normal / dry", expect="All zones NORMAL, high confidence.",
                   prog=lambda t: dict(R=0.0)),
    "heavy_rain": dict(title="Heavy rainfall", expect="Rain -> soil -> river -> drain; risk rises in the physical order, rule R01/R03/R07 fire.",
                       prog=lambda t: dict(R=ramp(t, 20, 50, 0, .9) if t < 400 else ramp(t, 400, 470, .9, 0))),
    "drain_blockage": dict(title="Drain blockage (light rain)", expect="Level rising + flow falling -> obstruction suspected (R05); abduction prefers obstruction.",
                           prog=lambda t: dict(R=ramp(t, 10, 30, 0, .4), block=ramp(t, 100, 115, 0, 1.0))),
    "compound": dict(title="Compound: heavy rain + blockage + river surge", expect="Several hypotheses propagate to LAND inundation (R10/R11), compound_event fires.",
                     prog=lambda t: dict(R=ramp(t, 10, 40, 0, .9), block=ramp(t, 110, 125, 0, 1.0))),
    "sensor_fault": dict(title="Sensor fault: rain sensor pinned at ADC rail", expect="Fault flagged, rain evidence excluded, confidence drops, no false flood.",
                         prog=lambda t: dict(R=0.0, fault_rain=(t >= 40))),
    "node_failure": dict(title="Node failure: FLOOD01 loses power during rain", expect="Node OFFLINE, zones DEGRADED (never silently safe), confidence drops.",
                         prog=lambda t: dict(R=ramp(t, 10, 40, 0, .7), silent={"FLOOD01"} if t >= 120 else set())),
    "replay_spoof": dict(title="Replay + spoofed rain readings", expect="Replay/CRC rejected; valid-CRC spoofed rain flagged as uncorroborated and down-weighted.",
                         prog=lambda t: dict(R=ramp(t, 10, 40, 0, .5) if t < 110 else 0.0, replay=(t >= 90), spoof=(t >= 130))),
    "gateway_loss": dict(title="Gateway loss (TTGO unplugged)", expect="Explicit network-degraded state; stale data not treated as live.",
                         prog=lambda t: dict(R=ramp(t, 10, 40, 0, .6), gw_down=(t >= 120))),
    "camera_loss": dict(title="Camera loss", expect="Vision evidence excluded; sensors alone keep a lower-confidence assessment.",
                        prog=lambda t: dict(R=ramp(t, 10, 40, 0, .8), cam_down=(t >= 100))),
}


class Sim:
    def __init__(self, ingest, clock, seed=1):
        self.ingest, self.clock = ingest, clock
        self.rng = random.Random(seed)
        self.vision = None
        self.gw_connected = True
        self.lock = threading.RLock()
        self.edges_fn = None   # terrain graph: same edges the reasoner uses
        self.set_scenario("normal")

    # ---- control
    def set_scenario(self, name, now=None):
        with self.lock:
            if name not in SCENARIOS:
                return False
            self.name = name
            now = self.clock.now() if now is None else now
            self.t_start = self.t_last = now
            self.R = self.S = self.Q = self.D = self.L = 0.0
            self.Q = 0.1
            self.block = 0.0
            self.hx = 190000.0
            self.pkt = {"FLOOD01": 0, "GAS01": 0}
            self.next_tx = {"FLOOD01": now + 1.0, "GAS01": now + 3.5}
            self.sent = []
            self.replayed = self.tampered = False
            self.gw_connected = True
            return True

    def gateway_status(self):
        return dict(connected=self.gw_connected, port="SIMULATOR", error="" if self.gw_connected else "simulated gateway loss")

    def vision_state(self):
        return self.vision

    # ---- stepping
    def step(self, now):
        with self.lock:
            while self.t_last < now:
                dt = min(1.0, now - self.t_last)
                self.t_last += dt
                self._advance(self.t_last, dt)

    def _advance(self, t, dt):
        ts = t - self.t_start
        p = SCENARIOS[self.name]["prog"](ts)
        self.R = p.get("R", 0.0)
        self.block = p.get("block", 0.0)
        eo = self.edges_fn() if self.edges_fn else {}
        open_rd = eo.get("edge_river_drain_open", True)
        open_rl = eo.get("edge_river_land_open", True)
        open_dl = eo.get("edge_drain_land_open", True)
        S, Q, D, L = self.S, self.Q, self.D, self.L
        S += dt * (0.004 * self.R * (1 - S) - 0.0003 * S)
        runoff = self.R * (0.3 + 0.7 * S)
        Q += dt * 0.03 * (0.1 + 1.0 * runoff - Q)
        cap = 1.2 * (1 - self.block) + 0.03
        inflow = 0.5 * runoff + (0.3 * max(Q - 0.6, 0) if open_rd else 0)
        D = min(1.0, max(0.0, D + dt * 0.04 * (inflow - cap * D)))
        dL = (0.006 if (D > 0.9 and open_dl) else 0) + (0.004 if (Q > 0.85 and open_rl) else 0) - (0.002 * L if self.R < 0.05 else 0)
        L = min(1.0, max(0.0, L + dt * dL))
        self.S, self.Q, self.D, self.L = S, Q, D, L
        self.drain_flow = 2.0 * cap * D
        self.hx += self.R * dt * 40
        g = self.rng.gauss
        # vision state (same schema as vision.py)
        if p.get("cam_down"):
            self.vision = dict(t=t, status="error", error="camera not responding (simulated)")
        else:
            flow = 0.8 * Q + 0.05 + g(0, 0.01)
            self.vision = dict(
                t=t, fps=6.0, status="running", uptime_s=int(ts),
                river=dict(state="FLOWING" if Q > 0.12 else "STILL_WATER", flow=round(max(flow, 0), 3),
                           cover=round(min(1, max(0, 0.25 + 0.6 * Q + g(0, 0.004))), 3)),
                drain=dict(state="DRY" if D < 0.02 else ("FLOWING" if self.drain_flow > 0.05 else "STILL_WATER"),
                           level=round(min(1, max(0, D + g(0, 0.004))), 3), flow=round(max(0, self.drain_flow + g(0, 0.008)), 3)),
                land=dict(state="WATERLOGGED" if L > 0.3 else "PUDDLING" if L > 0.08 else "DRY",
                          still=round(max(0, 0.4 * L + g(0, 0.002)), 4), patches=int(L * 5)))
        self.gw_connected = not p.get("gw_down")
        # packets
        for node in ("FLOOD01", "GAS01"):
            while self.next_tx[node] <= t:
                tx = self.next_tx[node]
                self.next_tx[node] += 5.0 + self.rng.uniform(0, 0.4)
                if node in p.get("silent", set()) or not self.gw_connected:
                    continue
                self._emit(node, tx, ts, p)
        if p.get("replay") and not self.replayed and self.sent:
            self.replayed = True
            old = [l for (tt, l) in self.sent if tt >= 35][:1] or [self.sent[0][1]]
            self.ingest(old[0], t)                       # exact replay of an old packet
            self.ingest(old[0].replace("RAIN=", "RAIN=9", 1), t + 0.2)   # naive tamper, CRC not fixed
        if p.get("spoof"):
            pass

    def _emit(self, node, tx, ts, p):
        g = self.rng.gauss
        self.pkt[node] += 1
        up = int(tx - self.t_start) + 3
        if node == "FLOOD01":
            rain = int(max(0, 100 + 1400 * self.R + g(0, 12)))
            if p.get("fault_rain"):
                rain = 4095
            if p.get("spoof"):
                rain = 1500 + int(g(0, 10))
            soil = int(300 + 2200 * self.S + g(0, 8))
            body = "FROM=FLOOD01,PKT=%d,STATUS=%s,RAIN=%d,SOIL=%d,HX=%d,UP=%d,FW=2.1" % (
                self.pkt[node], "ALERT" if rain > 500 else "NORMAL", rain, soil, int(self.hx + g(0, 40)), up)
        else:
            dist = max(0.0, 4.0 + 28 * max(self.Q - 0.1, 0) + g(0, 0.4))
            mq = int(860 + g(0, 12))
            body = "FROM=GAS01,PKT=%d,STATUS=NORMAL,MQ=%d,DIST=%.2f,MPU=OK,UP=%d,FW=2.1" % (self.pkt[node], mq, dist, up)
            if up < 60:
                body += ",FLAGS=WARMUP"
        line = make_line(body)
        if node == "FLOOD01":
            self.sent.append((ts, line))
        self.ingest(line, tx)


class SimRunner(threading.Thread):
    def __init__(self, sim, clock):
        super().__init__(daemon=True)
        self.sim, self.clock = sim, clock

    def run(self):
        while True:
            self.sim.step(self.clock.now())
            time.sleep(0.05)
