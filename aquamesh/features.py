"""Temporal feature extraction: time series -> persistent, hysteretic EVENTS.

Trend test: ordinary-least-squares slope over a sliding window, accepted only when the slope is
statistically distinguishable from noise (z = slope / standard-error).  Events must persist for
`on_s` seconds before being asserted and stay false for `off_s` before being retracted.
"""
import math
from collections import deque


class Series:
    def __init__(self, keep_s=1500):
        self.d = deque()
        self.keep = keep_s

    def add(self, t, v):
        if v is None:
            return
        self.d.append((t, float(v)))
        while self.d and t - self.d[0][0] > self.keep:
            self.d.popleft()

    def last(self):
        return self.d[-1] if self.d else None

    def win(self, t, w):
        return [(a, b) for a, b in self.d if t - a <= w]

    def trend(self, t, w, min_pts=6):
        pts = self.win(t, w)
        n = len(pts)
        if n < min_pts or pts[-1][0] - pts[0][0] < 0.5 * w:
            return None
        t0 = pts[0][0]
        xs = [(a - t0) / 60.0 for a, _ in pts]
        ys = [b for _, b in pts]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx < 1e-9:
            return None
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        syy = sum((y - my) ** 2 for y in ys)
        slope = sxy / sxx
        res = max(syy - slope * sxy, 0.0)
        se = math.sqrt(res / (n - 2) / sxx) if n > 2 else 1e9
        z = slope / se if se > 1e-12 else (99.0 if slope > 1e-9 else -99.0 if slope < -1e-9 else 0.0)
        return dict(slope=slope, z=z, mean=my, n=n, last=ys[-1])


class Latch:
    def __init__(self, on_s, off_s):
        self.on_s, self.off_s = on_s, off_s
        self.reset()

    def reset(self):
        self.active = False
        self.t_true = self.t_false = self.since = None

    def update(self, t, cond):
        if cond:
            self.t_false = None
            if self.t_true is None:
                self.t_true = t
            if not self.active and t - self.t_true >= self.on_s:
                self.active, self.since = True, self.t_true
        else:
            self.t_true = None
            if self.active:
                if self.t_false is None:
                    self.t_false = t
                if t - self.t_false >= self.off_s:
                    self.reset()
        return self.active


class Baseline:
    """Slowly-adapting calm baseline (EWMA mean/variance), frozen while an event is active."""

    def __init__(self, alpha=0.02, warm=20):
        self.alpha, self.warm, self.n, self.mu, self.var = alpha, warm, 0, 0.0, 0.0

    @property
    def ready(self):
        return self.n >= self.warm

    def update(self, v, calm):
        if v is None:
            return
        if self.n == 0:
            self.mu = v
        elif self.n < self.warm or calm:
            a = max(self.alpha, 1.0 / (self.n + 1)) if self.n < self.warm else self.alpha
            d = v - self.mu
            self.mu += a * d
            self.var = (1 - a) * (self.var + a * d * d)
        self.n += 1

    def z(self, v, floor):
        return (v - self.mu) / max(math.sqrt(self.var), floor)


EVENTS = ["rain_high", "soil_rising", "soil_saturated", "hx_rising", "river_cover_rising",
          "river_flow_surging", "river_motion_up", "drain_level_rising", "drain_level_high",
          "drain_flow_falling", "drain_flow_rising", "land_wet_expanding", "land_wet_present"]


class Features:
    def __init__(self, th):
        self.th = th
        self.reset()

    def reset(self):
        th = self.th
        self.S = {k: Series() for k in ("rain_n", "soil_n", "hx", "dist", "mq", "river_cover", "river_flow",
                                         "drain_level", "drain_flow", "land_still")}
        self.L = {e: Latch(th["on_s"], th["off_s"]) for e in EVENTS}
        self.B = {"river_flow": Baseline(), "dist": Baseline()}
        self.values = {}

    def feed(self, t, name, v):
        if name in self.S and v is not None:
            self.S[name].add(t, v)
            self.values[name] = v

    def _recent(self, name, t, maxage=30):
        l = self.S[name].last()
        return l if l and t - l[0] <= maxage else None

    def evaluate(self, t, avail):
        th, S, E = self.th, self.S, {}
        zmin = th["z_min"]

        def put(name, known, cond=False, value=""):
            L = self.L[name]
            if not known:
                L.reset()
                E[name] = dict(known=False, active=False, since=None, value="")
            else:
                L.update(t, bool(cond))
                E[name] = dict(known=True, active=L.active, since=L.since, value=value)

        # ---- rain / soil / load cell (FLOOD01)
        r = self._recent("rain_n", t) if "rain" in avail else None
        put("rain_high", bool(r), r and r[1] >= th["rain_high_n"], r and "rain=%.2f" % r[1])
        s = self._recent("soil_n", t) if "soil" in avail else None
        tr = S["soil_n"].trend(t, 90, 5) if s else None
        put("soil_rising", bool(tr), tr and tr["slope"] > th["soil_rise_slope"] and tr["z"] > zmin,
            tr and "%+.0f%%/min z=%.1f" % (tr["slope"] * 100, tr["z"]))
        put("soil_saturated", bool(s), s and s[1] >= th["soil_sat_n"], s and "soil=%.2f" % s[1])
        h = self._recent("hx", t, 40) if "hx" in avail else None
        tr = S["hx"].trend(t, 90, 5) if h else None
        put("hx_rising", bool(tr), tr and tr["slope"] > th["hx_slope_min"] and tr["z"] > 3,
            tr and "%+.0f cnt/min z=%.1f" % (tr["slope"], tr["z"]))

        # ---- river (camera + MPU)
        tr = S["river_cover"].trend(t, 60) if "cam_river" in avail else None
        put("river_cover_rising", bool(tr), tr and tr["slope"] > th["river_cover_slope"] and tr["z"] > zmin,
            tr and "cover %+.0f%%/min z=%.1f" % (tr["slope"] * 100, tr["z"]))
        f = self._recent("river_flow", t, 10) if "cam_river" in avail else None
        B = self.B["river_flow"]
        known = bool(f) and B.ready
        z = B.z(f[1], 0.03) if known else 0
        cond = known and z > th["river_flow_z"] and f[1] > 1.4 * B.mu
        put("river_flow_surging", known, cond, known and "flow %.2f vs calm %.2f (z=%.1f)" % (f[1], B.mu, z))
        if f:
            B.update(f[1], z < 2)
        d = self._recent("dist", t, 15) if "dist" in avail else None
        B = self.B["dist"]
        known = bool(d) and B.ready
        z = B.z(d[1], 0.5) if known else 0
        cond = known and z > th["dist_z"]
        put("river_motion_up", known, cond, known and "DIST %.1f vs calm %.1f (z=%.1f)" % (d[1], B.mu, z))
        if d:
            B.update(d[1], z < 2)

        # ---- drain (camera)
        cam = "cam_drain" in avail
        tr = S["drain_level"].trend(t, 60) if cam else None
        put("drain_level_rising", bool(tr), tr and tr["slope"] > th["drain_level_slope"] and tr["z"] > zmin,
            tr and "level %+.0f%%/min z=%.1f" % (tr["slope"] * 100, tr["z"]))
        lv = self._recent("drain_level", t, 10) if cam else None
        put("drain_level_high", bool(lv), lv and lv[1] >= th["drain_level_high"], lv and "level=%.0f%%" % (lv[1] * 100))
        trf = S["drain_flow"].trend(t, 60) if cam else None
        fl = self._recent("drain_flow", t, 10) if cam else None
        fl_floor = th["drain_flow_floor"]
        if trf and fl:
            rel = trf["slope"] / max(trf["mean"], fl_floor)
            peak = max(v for _, v in S["drain_flow"].win(t, 120))
            stagn = peak >= 2 * fl_floor and fl[1] <= 1.5 * fl_floor and lv and lv[1] >= 0.15
            peak10 = max(v for _, v in S["drain_flow"].win(t, 600))
            deficit = peak10 >= 2 * fl_floor and fl[1] <= 0.5 * peak10 and lv and lv[1] >= 0.5
            falling = (rel < -th["drain_flow_rel_slope"] and trf["z"] < -2.0) or bool(stagn) or bool(deficit)
            rising = rel > th["drain_flow_rel_slope"] and trf["z"] > zmin and not (stagn or deficit)
            val = ("flow %.2f stalled vs recent peak %.2f" % (fl[1], peak10)) if (stagn or deficit) else \
                  "flow %.2f (%+.0f%%/min)" % (fl[1], rel * 100)
        else:
            falling = rising = False
            val = ""
        put("drain_flow_falling", bool(trf and fl), falling, val)
        put("drain_flow_rising", bool(trf and fl), rising, val)

        # ---- land (camera)
        tr = S["land_still"].trend(t, 90, 6) if "cam_land" in avail else None
        put("land_wet_expanding", bool(tr), tr and tr["slope"] > th["land_still_slope"] and tr["z"] > zmin,
            tr and "wet area %+.1f%%/min z=%.1f" % (tr["slope"] * 100, tr["z"]))
        ls = self._recent("land_still", t, 10) if "cam_land" in avail else None
        put("land_wet_present", bool(ls), ls and ls[1] >= th["land_wet_present"],
            ls and "wet area=%.1f%%" % (ls[1] * 100))
        return E
