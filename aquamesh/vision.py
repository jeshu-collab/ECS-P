"""Vision processor (adapted from waterwatch.py): per-region water detection on the Pi camera.

Methods: LAB colour deviation from a learned dry baseline (K-sigma), Farneback dense optical flow for
surface motion, connected components for puddles, PCA axis binning for drain fill level.
Outputs the schema consumed by the reasoning engine: river{state,flow,cover}, drain{state,level,flow}, land{state,still}.
"""
import cv2, time, json, os, csv, threading, numpy as np
from collections import deque

# ---------------- config ----------------
PROC_W, PROC_H = 320, 240             # processing size (try 416x312 if your Pi keeps up)
ROT = None                            # cv2.ROTATE_90_CLOCKWISE if camera is mounted sideways
DATA = os.environ.get("AQUAMESH_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
os.makedirs(DATA, exist_ok=True)
K_SIGMA, MIN_STD, LEARN_N = 3.5, 2.5, 40
DRAIN_FRAC = (0.51, 0.34, 0.79, 0.84)       # Auto-suggest only: drain box inside board+river bbox
WATERLOG_FRAC, WATERLOG_SECS, NB = 0.03, 5, 20
CFG, BASE = os.path.join(DATA, "regions.json"), os.path.join(DATA, "baseline.npz")
K3 = np.ones((3, 3), np.uint8)
COL = {"river": (255, 128, 0), "drain": (0, 165, 255), "land": (0, 200, 0)}
KEYS = ("river", "drain", "land")

# ---------------- camera ----------------
def open_camera():
    try:
        from picamera2 import Picamera2
        c = Picamera2()
        c.configure(c.create_preview_configuration(main={"size": (640, 480), "format": "RGB888"}))
        c.start(); time.sleep(2)
        c.set_controls({"AeEnable": False, "AwbEnable": False})
        return c.capture_array
    except ImportError:
        cap = cv2.VideoCapture(0); cap.set(3, 640); cap.set(4, 480)
        return lambda: cap.read()[1]

_grab_raw = None
def grab():
    global _grab_raw
    if _grab_raw is None: _grab_raw = open_camera()
    f = _grab_raw()
    if f is None: raise RuntimeError("camera returned no frame")
    return cv2.rotate(f, ROT) if ROT is not None else f

# ---------------- shared state ----------------
lock = threading.Lock(); cond = threading.Condition()
latest = {"raw": None, "over": None, "seq": 0}
STATE = {"status": "starting"}
HIST = {k: deque(maxlen=600) for k in ("t", "river", "drain", "land")}

# ---------------- helpers ----------------
def flow_mag(prev, gray):
    return np.linalg.norm(cv2.calcOpticalFlowFarneback(prev, gray, None, 0.5, 2, 15, 2, 5, 1.1, 0), axis=2)

def white_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    low = hsv[..., 1] < 70; v = hsv[..., 2]
    thr, _ = cv2.threshold(v[low].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return (low & (v > thr)).astype(np.uint8)

def blue_mask(bgr):
    return cv2.inRange(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV), (95, 90, 25), (135, 255, 255))

def biggest(m, k=9):
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    n, lbl, st, _ = cv2.connectedComponentsWithStats(m)
    if n < 2: return None
    return (lbl == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)

def axis_bins(mask):
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], 1).astype(np.float32); c = pts.mean(0)
    _, v = np.linalg.eigh(np.cov((pts - c).T)); u = v[:, 1]
    p = (pts - c) @ u; p = (p - p.min()) / max(p.max() - p.min(), 1e-6)
    idx = np.full(mask.shape, -1, np.int16)
    idx[ys, xs] = np.minimum((p * NB).astype(int), NB - 1)
    return idx

def clean_regions(r):
    return {k: [[[min(max(float(x), 0), 1), min(max(float(y), 0), 1)] for x, y in poly]
                for poly in r.get(k, []) if len(poly) >= 3] for k in KEYS}

def polys_to_masks(reg):
    ms = {}
    for k in KEYS:
        m = np.zeros((PROC_H, PROC_W), np.uint8)
        for poly in reg.get(k, []):
            cv2.fillPoly(m, [(np.array(poly) * [PROC_W, PROC_H]).astype(np.int32)], 1)
        ms[k] = m.astype(bool)
    ms["drain"] &= ~ms["river"]; ms["land"] &= ~(ms["river"] | ms["drain"])   # river > drain > land
    return ms

def suggest(img):
    reg = {k: [] for k in KEYS}
    norm = lambda a: (np.asarray(a).reshape(-1, 2) / [PROC_W, PROC_H]).round(4).tolist()
    wb, bl = biggest(white_mask(img), 7), biggest(blue_mask(img), 5)
    union = np.zeros((PROC_H, PROC_W), np.uint8)
    if bl is not None:
        c = cv2.convexHull(cv2.findNonZero(bl))
        reg["river"] = [norm(cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True))]; union |= bl
    if wb is not None:
        cs, _ = cv2.findContours(wb, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)
        reg["land"] = [norm(cv2.approxPolyDP(c, 0.01 * cv2.arcLength(c, True), True))]; union |= wb
    if union.any():
        x, y, w, h = cv2.boundingRect(cv2.findNonZero(union)); a, b, c2, d = DRAIN_FRAC
        reg["drain"] = [norm([[x + a * w, y + b * h], [x + c2 * w, y + b * h],
                              [x + c2 * w, y + d * h], [x + a * w, y + d * h]])]
    return reg

class Series:                 # 1-second bins -> mean, volatility (CV), slope per minute
    def __init__(s): s.b = deque(maxlen=300); s.sec = None; s.acc = []
    def add(s, now, v):
        sec = int(now)
        if s.sec is None: s.sec = sec
        if sec != s.sec:
            if s.acc: s.b.append((s.sec, float(np.mean(s.acc))))
            s.acc, s.sec = [], sec
        s.acc.append(v)
    def _a(s, n): return np.array(list(s.b)[-n:])
    def mean(s, n=60):
        a = s._a(n); return float(a[:, 1].mean()) if len(a) else 0.0
    def cv(s, n=20):
        a = s._a(n)
        if len(a) < 8: return 0.0
        m = a[:, 1].mean(); return float(a[:, 1].std() / m) if m > 1e-6 else 0.0
    def slope(s, n=60):
        a = s._a(n)
        if len(a) < 15: return 0.0
        return float(np.polyfit(a[:, 0] - a[0, 0], a[:, 1], 1)[0] * 60)

class Deb:                    # a state must persist `secs` before it switches
    def __init__(s, init, secs=2.0): s.cur = s.cand = init; s.t = 0; s.secs = secs
    def __call__(s, new, now):
        if new == s.cur: s.cand = new; return s.cur
        if new != s.cand: s.cand, s.t = new, now
        elif now - s.t >= s.secs: s.cur = new
        return s.cur

def compose(frame, masks, still, moving, lines):
    o = frame.copy(); fh, fw = o.shape[:2]
    for m, col in ((still, (255, 0, 255)), (moving, (0, 255, 255))):
        if m is None: continue
        mm = cv2.resize(m.astype(np.uint8), (fw, fh), interpolation=cv2.INTER_NEAREST).astype(bool)
        o[mm] = (0.45 * o[mm] + 0.55 * np.array(col)).astype(np.uint8)
    sx, sy = fw / PROC_W, fh / PROC_H
    for k, m in masks.items():
        cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cs: cv2.polylines(o, [(c * [sx, sy]).astype(np.int32)], True, COL[k], 2)
    for i, t in enumerate(lines):
        cv2.putText(o, t, (8, 20 + 18 * i), 0, 0.55, (0, 0, 0), 3)
        cv2.putText(o, t, (8, 20 + 18 * i), 0, 0.55, (255, 255, 255), 1)
    return o

# ---------------- detector thread ----------------
class Detector(threading.Thread):
    def __init__(s):
        super().__init__(daemon=True)
        s.mean = s.std = s.thr = None; s.learn = None; s.learn_req = False
        s.shift, s.t_cs = 0.0, 0
        if os.path.exists(BASE):
            d = np.load(BASE)
            if d["mean"].shape[:2] == (PROC_H, PROC_W):
                s.mean, s.std, s.thr = d["mean"].copy(), d["std"], d["thr"]
        s.set_regions(json.load(open(CFG)) if os.path.exists(CFG) else {}, save=False)

    def set_regions(s, reg, save=True):
        reg = clean_regions(reg)
        if save: json.dump(reg, open(CFG, "w"))
        masks = polys_to_masks(reg); cnt = {k: int(m.sum()) for k, m in masks.items()}
        dbin = axis_bins(masks["drain"]) if cnt["drain"] >= 50 else None
        dcount = np.bincount(dbin[dbin >= 0], minlength=NB).clip(1) if dbin is not None else None
        pack = dict(reg=reg, masks=masks, cnt=cnt, dbin=dbin, dcount=dcount,
                    valid=masks["river"] | masks["drain"] | masks["land"],
                    S={k: Series() for k in ("rq", "dl", "ls")},
                    D={k: Deb(v) for k, v in (("river", "NO_WATER"), ("drain", "DRY"), ("land", "DRY"))},
                    wl=[None])
        with lock: s.p = pack

    def detect(s, now, lab, lab_s, me, wet_cnt, p, fps):
        masks, cnt, S, D = p["masks"], p["cnt"], p["S"], p["D"]
        dev = (np.abs(lab_s - s.mean) / s.std).max(axis=2) > K_SIGMA
        wet_cnt[:] = np.where(dev, np.minimum(wet_cnt, 100) + 1, 0)
        wet = cv2.morphologyEx((wet_cnt >= 3).astype(np.uint8), cv2.MORPH_OPEN, K3).astype(bool)
        moving = cv2.morphologyEx((me > s.thr).astype(np.uint8), cv2.MORPH_OPEN, K3).astype(bool)
        still = wet & ~cv2.dilate(moving.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        water = wet | moving
        valid = p["valid"]
        if valid.any() and (wet & valid).sum() > 0.5 * valid.sum():
            return {"status": "paused", "warning": "global change: hand in view or lights changed"}, still, moving
        dry = ~cv2.dilate(water.astype(np.uint8), K3).astype(bool)
        s.mean[dry] += 0.002 * (lab_s[dry] - s.mean[dry])          # follow slow light drift
        if now - s.t_cs > 5:                                       # camera moved?
            (dx, dy), resp = cv2.phaseCorrelate(
                np.ascontiguousarray(s.mean[..., 0]),
                np.ascontiguousarray(cv2.GaussianBlur(lab[..., 0], (0, 0), 1)))
            s.shift = float(np.hypot(dx, dy)) if resp > 0.1 else 0.0; s.t_cs = now
        out = {"status": "running"}

        if cnt["river"] >= 50:                                     # RIVER
            r = masks["river"]; n = cnt["river"]
            mr = (moving & r).sum() / n; sr = (still & r).sum() / n
            sp = float(me[moving & r].mean()) * fps if mr > 0.02 else 0.0
            q = mr * sp; S["rq"].add(now, q)
            qm, vol = S["rq"].mean(60), S["rq"].cv(20)
            tr = S["rq"].slope(60) / qm * 100 if qm > 1e-6 else 0.0
            rs = D["river"]("FLOWING" if mr > 0.02 else "STILL_WATER" if sr > 0.02 else "NO_WATER", now)
            fl = rs == "FLOWING"
            out["river"] = {"state": rs, "flow": round(q, 3), "speed_px_s": round(sp, 2),
                            "cover": round(float((water & r).sum() / n), 3),
                            "volatility": round(vol, 2),
                            "vol_label": ("STABLE" if vol < 0.15 else "FLUCTUATING" if vol < 0.4 else "ERRATIC") if fl else "-",
                            "trend": ("RISING" if tr > 10 else "FALLING" if tr < -10 else "STEADY") if fl else "-",
                            "trend_pct_min": round(tr, 1)}
        else: out["river"] = {"state": "UNLABELED"}

        if cnt["drain"] >= 50:                                     # DRAIN
            dm = masks["drain"]; n = cnt["drain"]
            dw = (water & dm).sum() / n; dmv = (moving & dm).sum() / n
            cov = np.bincount(p["dbin"][water & dm], minlength=NB) / p["dcount"]
            level = float((cov > 0.25).mean()); S["dl"].add(now, level)
            rise = S["dl"].slope(90) * 100
            ds = D["drain"]("DRY" if dw < 0.02 else "FLOWING" if dmv > 0.02 else "STILL_WATER", now)
            dsp = float(me[moving & dm].mean()) * fps if dmv > 0.02 else 0.0
            out["drain"] = {"state": ds, "flow": round(dmv * dsp, 3), "trend": "RISING" if rise > 1 else "FALLING" if rise < -1 else "HOLDING",
                            "level": round(level, 2), "rise_pct_min": round(rise, 2),
                            "eta_full_s": round((1 - level) * 100 / rise * 60) if rise > 0.5 and level < 1 else None}
        else: out["drain"] = {"state": "UNLABELED"}

        if cnt["land"] >= 50:                                      # LAND
            lm = masks["land"]; n = cnt["land"]; ls = still & lm
            sfrac = ls.sum() / n; lmv = (moving & lm).sum() / n
            S["ls"].add(now, sfrac); grow = S["ls"].slope(90) * 100
            _, _, stt, _ = cv2.connectedComponentsWithStats(ls.astype(np.uint8))
            areas = stt[1:, cv2.CC_STAT_AREA]; areas = areas[areas >= 20]
            wl = p["wl"]
            if sfrac > WATERLOG_FRAC: wl[0] = wl[0] or now
            else: wl[0] = None
            lst = ("WATERLOGGED" if wl[0] and now - wl[0] >= WATERLOG_SECS else
                   "PUDDLING" if sfrac > WATERLOG_FRAC else "RUNOFF" if lmv > 0.02 else "DRY")
            out["land"] = {"state": D["land"](lst, now), "still": round(float(sfrac), 3),
                           "patches": int(len(areas)),
                           "largest": round(float(areas.max() / n), 3) if len(areas) else 0.0,
                           "growth_pct_min": round(grow, 2)}
        else: out["land"] = {"state": "UNLABELED"}
        if s.shift > 3: out["warning"] = f"camera moved {s.shift:.1f}px - recalibrate"
        return out, still, moving

    def run(s):
        me = np.zeros((PROC_H, PROC_W), np.float32); wet_cnt = np.zeros((PROC_H, PROC_W), np.uint8)
        prev = lab_s = None; fps, last = 5.0, time.time(); errs = 0
        t_hist = t_log = t_over = 0; t_start = time.time()
        log = csv.writer(open(os.path.join(DATA, "water_log.csv"), "a", buffering=1))
        while True:
            try:
                frame = grab()
                small = cv2.resize(frame, (PROC_W, PROC_H), interpolation=cv2.INTER_AREA)
                bgr = cv2.GaussianBlur(small, (5, 5), 0)
                lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                latest["raw"] = frame
                now = time.time(); fps += 0.1 * (1 / max(now - last, 1e-3) - fps); last = now
                lab_s = lab if lab_s is None else lab_s + 0.5 * (lab - lab_s)
                if prev is None: prev = gray; continue
                me += 0.4 * (flow_mag(prev, gray) - me); prev = gray
                p = s.p; still = moving = None
                st = {"t": round(now, 1), "fps": round(fps, 1), "uptime_s": int(now - t_start)}

                if s.learn_req:
                    s.learn_req = False
                    s.learn = dict(frames=0, n=0, t=0.0, s=np.zeros((PROC_H, PROC_W, 3), np.float32),
                                   s2=np.zeros((PROC_H, PROC_W, 3), np.float32),
                                   peak=np.zeros((PROC_H, PROC_W), np.float32))
                if s.learn is not None:                            # learn dry baseline, stream keeps running
                    L = s.learn; L["frames"] += 1
                    if L["frames"] > 6:
                        L["peak"] = np.maximum(L["peak"], me)
                        if now - L["t"] >= 0.25:
                            L["t"] = now; L["n"] += 1; L["s"] += lab_s; L["s2"] += lab_s * lab_s
                    if L["n"] >= LEARN_N:
                        n = L["n"]; mean = L["s"] / n
                        std = np.maximum(np.sqrt(np.maximum(L["s2"] / n - mean ** 2, 0)), MIN_STD)
                        thr = cv2.blur(np.maximum(0.15, 1.5 * L["peak"] + 0.1), (7, 7))
                        np.savez(BASE, mean=mean, std=std, thr=thr)
                        s.mean, s.std, s.thr = mean, std, thr; s.learn = None; wet_cnt[:] = 0
                    st.update(status="learning", progress=round(L["n"] / LEARN_N, 2))
                elif s.mean is None:
                    st["status"] = "needs_baseline"
                else:
                    out, still, moving = s.detect(now, lab, lab_s, me, wet_cnt, p, fps); st.update(out)

                with lock: STATE.clear(); STATE.update(st)
                if now - t_hist >= 1:
                    t_hist = now; g = lambda k, f: st.get(k, {}).get(f, 0) or 0
                    with lock:
                        for k, v in (("t", round(now, 1)), ("river", g("river", "flow")),
                                     ("drain", g("drain", "level")), ("land", g("land", "still"))):
                            HIST[k].append(v)
                if now - t_log >= 5 and st["status"] == "running":
                    t_log = now
                    log.writerow([st["t"], g("river", "flow"), g("river", "volatility"),
                                  g("drain", "level"), g("drain", "rise_pct_min"),
                                  g("land", "still"), g("land", "growth_pct_min")])
                if now - t_over >= 0.25:
                    t_over = now
                    lines = [f"{k} {st[k]['state']}" for k in KEYS if k in st] or [st["status"]]
                    o = compose(frame, p["masks"], still, moving, lines)
                    jpg = cv2.imencode(".jpg", o, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
                    with cond: latest["over"] = jpg; latest["seq"] += 1; cond.notify_all()
                errs = 0; time.sleep(0.03)
            except Exception as e:                                 # never die: report and retry
                errs += 1
                with lock: STATE.clear(); STATE.update({"status": "error", "error": str(e)[:120]})
                time.sleep(min(errs, 5))


# ---------------- facade used by the server / engine ----------------
class Vision:
    def __init__(self):
        self.det = Detector()
        self.det.start()

    def state(self):
        with lock:
            return dict(STATE)

    def jpeg(self):
        f = latest["raw"]
        return None if f is None else cv2.imencode(".jpg", f)[1].tobytes()

    def overlay(self):
        with cond:
            return latest["over"], latest["seq"]

    def wait_frame(self, timeout=2):
        with cond:
            cond.wait(timeout)

    def regions(self):
        return self.det.p["reg"]

    def set_regions(self, reg):
        self.det.set_regions(reg)
        return self.det.p["reg"]

    def learn(self):
        self.det.learn_req = True

    def suggest(self):
        f = latest["raw"]
        if f is None:
            return None
        return suggest(cv2.resize(f, (PROC_W, PROC_H), interpolation=cv2.INTER_AREA))
