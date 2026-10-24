"""Sensor-health monitor: freshness, plausibility, stuck/glitch/rail faults, reliability weights."""
from collections import deque

BASE_REL = {"rain": 0.80, "soil": 0.80, "hx": 0.50, "dist": 0.50, "mq": 0.40,
            "cam_river": 0.85, "cam_drain": 0.85, "cam_land": 0.85}
CHANNELS = {"rain": ("flood", "RAIN"), "soil": ("flood", "SOIL"), "hx": ("flood", "HX"),
            "mq": ("gas", "MQ"), "dist": ("gas", "DIST")}
ABLATABLE = {"camera": ["cam_river", "cam_drain", "cam_land"], "rain": ["rain"], "soil": ["soil"],
             "hx": ["hx"], "mpu": ["dist"], "gas": ["mq"]}


class Chan:
    def __init__(self):
        self.last_t = None
        self.last = None
        self.hist = deque(maxlen=3)
        self.same = 0
        self.rail = 0
        self.ok_streak = 0
        self.fault = None
        self.glitches = 0


class Health:
    def __init__(self, period=5):
        self.period = period
        self.ch = {k: Chan() for k in CHANNELS}
        self.node_t = {}         # role -> last accepted rx time
        self.node_flags = {}     # role -> dict(mpu_ok, warmup)
        self.reset_t = None

    def reset(self):
        self.__init__(self.period)

    def on_packet(self, t, role, fields):
        """Returns {channel: value_for_features or None}."""
        self.node_t[role] = t
        self.node_flags[role] = dict(mpu_ok=fields.get("MPU", "OK") == "OK",
                                     warmup="WARMUP" in str(fields.get("FLAGS", "")))
        out = {}
        for name, (r, key) in CHANNELS.items():
            if r != role or key not in fields:
                continue
            c = self.ch[name]
            v = float(fields[key])
            c.last_t = t
            fault = None
            if name in ("rain", "soil", "mq"):
                c.rail = c.rail + 1 if v >= 4090 else 0
                if c.rail >= 2:
                    fault = "reading pinned at ADC rail (short / saturation)"
                if name == "soil" and v <= 5:
                    fault = "soil reading ~0 (sensor floating / disconnected)"
                frac = v / 4095.0
                c.same = c.same + 1 if (c.last == v and 0.05 < frac < 0.95) else 0
                if c.same >= 30:
                    fault = "reading stuck (30 identical samples)"
                c.hist.append(v)
                med = sorted(c.hist)[len(c.hist) // 2]
                if abs(v - med) > 2500:
                    c.glitches += 1
                v = med
            if name == "dist" and not self.node_flags[role]["mpu_ok"]:
                fault = "MPU6050 read error (MPU=ERR)"
            if name == "mq" and self.node_flags[role]["warmup"]:
                fault = "MQ sensor warming up"
            c.last = float(fields[key])
            c.fault = fault
            out[name] = None if fault else v
        return out

    def _node_state(self, t, role):
        lt = self.node_t.get(role)
        if lt is None:
            return "NO_DATA", None
        age = t - lt
        p = self.period
        return ("FRESH" if age <= 2.5 * p else "STALE" if age <= 6 * p else "OFFLINE"), age

    def status(self, t, links, vis, gw, ablated, prev_events, nodes_cfg):
        out = {}
        gw_ok = gw.get("connected", True)
        for role, label in (("flood", "FLOOD01"), ("gas", "GAS01")):
            st, age = self._node_state(t, role)
            reasons = []
            if not gw_ok:
                st = "OFFLINE" if st != "NO_DATA" else st
                reasons.append("gateway (TTGO) not connected")
            if st in ("STALE", "OFFLINE"):
                reasons.append("no packet for %.0fs" % age)
            out[role + "_node"] = dict(state=st, label=label, age=age, reasons=reasons,
                                       rel=0.0 if st in ("OFFLINE", "NO_DATA") else (1.0 if st == "FRESH" else 0.5))
        out["gateway"] = dict(state="OK" if gw_ok else "DOWN", reasons=[gw.get("error", "")] if not gw_ok and gw.get("error") else [],
                              rel=1.0 if gw_ok else 0.0)
        # channels
        ev = prev_events or {}
        uncorr = bool(ev.get("rain_high", {}).get("active")) and self._uncorroborated(t, ev)
        for name, (role, key) in CHANNELS.items():
            c, nd = self.ch[name], out[role + "_node"]
            reasons = list(nd["reasons"])
            rel = BASE_REL[name] * nd["rel"]
            state = nd["state"]
            if c.last_t is None and nd["state"] in ("FRESH", "STALE"):
                reasons.append("field %s not present in packets" % key)
                rel, state = 0.0, "MISSING"
            elif c.last_t is not None and nd["state"] == "FRESH" and t - c.last_t > 2.5 * self.period:
                reasons.append("%s omitted (sensor not ready)" % key)
                rel, state = 0.0, "MISSING"
            if c.fault:
                reasons.append(c.fault)
                rel, state = 0.0, ("WARMUP" if "warming" in c.fault else "FAULT")
            if name == "rain" and uncorr and rel > 0:
                rel *= 0.5
                reasons.append("rain high but soil/river/drain show no response (uncorroborated)")
                state = "SUSPECT"
            for grp, members in ABLATABLE.items():
                if name in members and grp in ablated:
                    rel, state = 0.0, "DISABLED"
                    reasons.append("disabled (ablation)")
            out[name] = dict(state=state, rel=round(rel, 2), reasons=reasons, glitches=c.glitches,
                             age=None if c.last_t is None else t - c.last_t, last=c.last)
        out["rain_uncorroborated"] = uncorr
        # camera
        cs, reasons, rel = "OK", [], BASE_REL["cam_river"]
        if not vis:
            cs, rel = "NO_DATA", 0.0
            reasons.append("no vision source")
        else:
            stat = vis.get("status", "unknown")
            age = t - vis.get("t", 0)
            if age > 10:
                cs, rel = "STALE", 0.0
                reasons.append("no frame for %.0fs" % age)
            elif stat == "paused":
                cs, rel = "PAUSED", 0.0
                reasons.append(vis.get("warning", "global change (hand/light)"))
            elif stat != "running":
                cs, rel = stat.upper(), 0.0
                reasons.append("vision status: %s%s" % (stat, " - " + vis["error"] if vis.get("error") else ""))
            elif vis.get("warning"):
                rel *= 0.5
                reasons.append(vis["warning"])
        for zone in ("river", "drain", "land"):
            zr, zs, zre = rel, cs, list(reasons)
            if vis and cs == "OK" and vis.get(zone, {}).get("state") == "UNLABELED":
                zr, zs = 0.0, "UNLABELED"
                zre.append("%s region not drawn" % zone)
            if "camera" in ablated:
                zr, zs = 0.0, "DISABLED"
                zre.append("disabled (ablation)")
            out["cam_" + zone] = dict(state=zs, rel=round(zr, 2), reasons=zre)
        return out

    def _uncorroborated(self, t, ev):
        e = ev.get("rain_high", {})
        since = e.get("since")
        if since is None or t - since < 150:
            return False
        others = ("soil_rising", "soil_saturated", "river_cover_rising", "drain_level_rising", "hx_rising",
                  "river_flow_surging")
        known = [o for o in others if ev.get(o, {}).get("known")]
        if len(known) < 2:
            return False
        return not any(ev[o]["active"] for o in known)
