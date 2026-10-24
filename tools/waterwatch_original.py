#!/usr/bin/env python3
import cv2, time, json, os, csv, threading, urllib.request, numpy as np
from collections import deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ---------------- config ----------------
PROC_W, PROC_H = 320, 240             # processing size (try 416x312 if your Pi keeps up)
ROT = None                            # cv2.ROTATE_90_CLOCKWISE if camera is mounted sideways
PORT = int(os.environ.get("PORT", "8000"))
PUSH_URL = os.environ.get("PUSH_URL", "")   # optional: POST state JSON here every PUSH_EVERY s
PUSH_EVERY = 5
K_SIGMA, MIN_STD, LEARN_N = 3.5, 2.5, 40
DRAIN_FRAC = (0.51, 0.34, 0.79, 0.84)       # Auto-suggest only: drain box inside board+river bbox
WATERLOG_FRAC, WATERLOG_SECS, NB = 0.03, 5, 20
CFG, BASE = "regions.json", "baseline.npz"
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

grab_raw = open_camera()
def grab():
    f = grab_raw()
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
            out["drain"] = {"state": ds, "trend": "RISING" if rise > 1 else "FALLING" if rise < -1 else "HOLDING",
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
        log = csv.writer(open("water_log.csv", "a", buffering=1))
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

def pusher():
    while True:
        time.sleep(PUSH_EVERY)
        with lock: d = dict(STATE)
        try:
            urllib.request.urlopen(urllib.request.Request(
                PUSH_URL, json.dumps(d).encode(), {"Content-Type": "application/json"}), timeout=3).close()
        except Exception: pass

# ---------------- HTTP: GUI + API ----------------
class Handler(BaseHTTPRequestHandler):
    def log_message(s, *a): pass
    def _cors(s):
        s.send_header("Access-Control-Allow-Origin", "*")
        s.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        s.send_header("Access-Control-Allow-Headers", "Content-Type")
    def send(s, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        s.send_response(code); s.send_header("Content-Type", ctype)
        s.send_header("Content-Length", str(len(body))); s.send_header("Cache-Control", "no-store")
        s._cors(); s.end_headers(); s.wfile.write(body)
    def do_OPTIONS(s): s.send_response(204); s._cors(); s.end_headers()

    def do_GET(s):
        u = urlparse(s.path); q = parse_qs(u.query)
        try:
            if u.path == "/": s.send(PAGE, "text/html; charset=utf-8")
            elif u.path == "/api/state":
                with lock: d = dict(STATE)
                s.send(d)
            elif u.path == "/api/history":
                n = int(q.get("n", ["300"])[0])
                with lock: d = {k: list(v)[-n:] for k, v in HIST.items()}
                s.send(d)
            elif u.path == "/api/regions": s.send(det.p["reg"])
            elif u.path == "/snapshot.jpg":
                f = latest["raw"]
                if f is None: s.send({"error": "no frame"}, code=503)
                else: s.send(cv2.imencode(".jpg", f)[1].tobytes(), "image/jpeg")
            elif u.path == "/stream.mjpg":
                s.send_response(200); s.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
                s._cors(); s.end_headers(); last = -1
                while True:
                    with cond: cond.wait(2)
                    b, seq = latest["over"], latest["seq"]
                    if b is None or seq == last: continue
                    last = seq
                    s.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(b) + b + b"\r\n")
            elif u.path == "/api/stream":                          # server-sent events, 1 Hz
                s.send_response(200); s.send_header("Content-Type", "text/event-stream")
                s.send_header("Cache-Control", "no-cache"); s._cors(); s.end_headers()
                while True:
                    with lock: d = json.dumps(STATE)
                    s.wfile.write(f"data: {d}\n\n".encode()); time.sleep(1)
            else: s.send({"error": "not found"}, code=404)
        except (BrokenPipeError, ConnectionResetError): pass

    def do_POST(s):
        try:
            body = json.loads(s.rfile.read(int(s.headers.get("Content-Length", 0))) or b"{}")
            if s.path == "/api/regions": det.set_regions(body); s.send({"ok": True, "regions": det.p["reg"]})
            elif s.path == "/api/learn": det.learn_req = True; s.send({"ok": True})
            elif s.path == "/api/suggest":
                f = latest["raw"]
                if f is None: s.send({"error": "no frame"}, code=503)
                else: s.send(suggest(cv2.resize(f, (PROC_W, PROC_H), interpolation=cv2.INTER_AREA)))
            else: s.send({"error": "not found"}, code=404)
        except (ValueError, KeyError, TypeError) as e: s.send({"error": str(e)}, code=400)

PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Water Watch</title>
<style>
:root{--bg:#0b0d10;--card:#14181d;--line:#232a33;--tx:#e6e9ee;--dim:#8b95a3}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.4 system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
header h1{font-size:15px;margin:0;flex:1}
button{background:var(--card);color:var(--tx);border:1px solid var(--line);padding:6px 12px;border-radius:6px;cursor:pointer}
button:hover{border-color:#3b4654}button.on{background:#1f6feb;border-color:#1f6feb}
main{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:16px;padding:16px}
@media(max-width:900px){main{grid-template-columns:1fr}}
.view{position:relative;background:#000;border-radius:8px;overflow:hidden;min-height:120px}
.view img{width:100%;display:block}.view canvas{position:absolute;left:0;top:0;width:100%;height:100%}
.cards{display:grid;gap:12px;align-content:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.card h3{margin:0 0 8px;font-size:12px;color:var(--dim);font-weight:500;text-transform:uppercase;letter-spacing:.06em}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;font-weight:600;margin-bottom:8px}
.row{display:flex;justify-content:space-between;padding:2px 0;color:var(--dim)}.row b{color:var(--tx);font-weight:500}
.tools{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
canvas.sp{width:100%;height:60px;display:block;margin:4px 0 8px}
#warn{color:#f59e0b}small{color:var(--dim)}code{color:var(--tx)}
</style></head><body>
<header><h1>Water Watch</h1><span id=status>connecting</span><small id=meta></small><span id=warn></span>
<button id=bLearn>Learn dry baseline</button><button id=bEdit>Edit regions</button></header>
<main>
<div>
 <div class=view><img id=live src="/stream.mjpg"><canvas id=cv></canvas></div>
 <div id=tools class=tools style="display:none">
  <button data-t=river class=on>River</button><button data-t=drain>Drain</button><button data-t=land>Land</button>
  <button id=bClose>Close shape</button><button id=bUndo>Undo point</button><button id=bClear>Clear region</button>
  <button id=bSug>Auto-suggest</button><button id=bSave>Save</button><button id=bCancel>Cancel</button>
 </div>
 <p id=hint style="display:none"><small>Click to add points. Double-click, Enter or Close shape finishes a polygon. Right-click removes the last point. A region can have several shapes.</small></p>
</div>
<div class=cards>
 <div class=card id=c_river></div><div class=card id=c_drain></div><div class=card id=c_land></div>
 <div class=card><h3>History (last 5 min)</h3>
  <small>river flow</small><canvas class=sp id=s_river></canvas>
  <small>drain level</small><canvas class=sp id=s_drain></canvas>
  <small>land still water</small><canvas class=sp id=s_land></canvas></div>
 <div class=card><h3>API</h3><small>
  <code>GET /api/state</code> current state<br><code>GET /api/stream</code> SSE, 1 Hz<br>
  <code>GET /api/history?n=300</code> 1 Hz series<br><code>GET|POST /api/regions</code> polygons (0-1 coords)<br>
  <code>POST /api/learn</code> relearn dry baseline<br><code>GET /stream.mjpg</code> · <code>GET /snapshot.jpg</code></small></div>
</div></main>
<script>
const $=id=>document.getElementById(id);
const COLS={river:'#3b9eff',drain:'#ff9f1c',land:'#2ecc71'};
const BADGE={DRY:'#6b7280',NO_WATER:'#6b7280',UNLABELED:'#374151',STILL_WATER:'#a855f7',FLOWING:'#0891b2',RUNOFF:'#0891b2',PUDDLING:'#d97706',WATERLOGGED:'#dc2626'};
let edit=false,tool='river',reg={river:[],drain:[],land:[]},cur=[];
const img=$('live'),cv=$('cv'),cx=cv.getContext('2d');

function fit(){cv.width=img.clientWidth;cv.height=img.clientHeight;draw();}
new ResizeObserver(fit).observe(img);
function poly(p,col,closed,fill){
 cx.beginPath();p.forEach(([x,y],i)=>{const X=x*cv.width,Y=y*cv.height;i?cx.lineTo(X,Y):cx.moveTo(X,Y)});
 if(closed)cx.closePath();
 if(fill){cx.fillStyle=col+'40';cx.fill();}
 cx.strokeStyle=col;cx.lineWidth=2;cx.stroke();
 p.forEach(([x,y])=>{cx.fillStyle=col;cx.fillRect(x*cv.width-3,y*cv.height-3,6,6)});
}
function draw(){
 cx.clearRect(0,0,cv.width,cv.height);if(!edit)return;
 for(const k in reg)for(const p of reg[k])poly(p,COLS[k],true,true);
 if(cur.length)poly(cur,COLS[tool],false,false);
}
function closePoly(){if(cur.length>=3)reg[tool].push(cur);cur=[];draw();}
cv.onclick=e=>{if(!edit)return;const r=cv.getBoundingClientRect();cur.push([(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height]);draw();};
cv.ondblclick=()=>{if(edit){cur.pop();closePoly();}};
cv.oncontextmenu=e=>{e.preventDefault();if(edit){cur.pop();draw();}};
addEventListener('keydown',e=>{if(edit&&e.key==='Enter')closePoly();});

document.querySelectorAll('[data-t]').forEach(b=>b.onclick=()=>{
 if(cur.length>=3)closePoly();tool=b.dataset.t;cur=[];
 document.querySelectorAll('[data-t]').forEach(x=>x.classList.toggle('on',x===b));draw();});
function stopEdit(){edit=false;cur=[];img.src='/stream.mjpg?'+Date.now();
 $('tools').style.display='none';$('hint').style.display='none';$('bEdit').classList.remove('on');draw();}
$('bEdit').onclick=async()=>{if(edit)return;reg=await(await fetch('/api/regions')).json();
 edit=true;cur=[];img.src='/snapshot.jpg?'+Date.now();
 $('tools').style.display='flex';$('hint').style.display='block';$('bEdit').classList.add('on');draw();};
$('bClose').onclick=closePoly;$('bUndo').onclick=()=>{cur.pop();draw();};
$('bClear').onclick=()=>{reg[tool]=[];cur=[];draw();};
$('bSug').onclick=async()=>{reg=await(await fetch('/api/suggest',{method:'POST',body:'{}'})).json();cur=[];draw();};
$('bSave').onclick=async()=>{if(cur.length>=3)closePoly();
 await fetch('/api/regions',{method:'POST',body:JSON.stringify(reg)});stopEdit();};
$('bCancel').onclick=stopEdit;
$('bLearn').onclick=async()=>{if(confirm('Model must be DRY and hands out of view. Learn baseline now (~10 s)?'))
 await fetch('/api/learn',{method:'POST',body:'{}'});};

function card(id,title,st,rows){
 $(id).innerHTML=`<h3>${title}</h3><div class=badge style="background:${BADGE[st]||'#374151'}">${st||'-'}</div>`+
 rows.map(([k,v])=>`<div class=row><span>${k}</span><b>${v??'-'}</b></div>`).join('');}
function render(s){
 $('status').textContent=s.status==='learning'?`learning dry baseline ${Math.round(s.progress*100)}%`:
  s.status==='needs_baseline'?'draw regions, then Learn dry baseline':s.status==='error'?'camera error: '+(s.error||''):s.status;
 $('meta').textContent=s.fps?s.fps+' fps':'';$('warn').textContent=s.warning||'';
 const r=s.river||{},d=s.drain||{},l=s.land||{};
 card('c_river','River',r.state,[['trend',r.trend],['flow index',r.flow],['speed px/s',r.speed_px_s],
  ['volatility',r.vol_label?`${r.vol_label} (${r.volatility})`:null],['trend %/min',r.trend_pct_min]]);
 card('c_drain','Drain',d.state,[['trend',d.trend],['level',d.level==null?null:Math.round(d.level*100)+'%'],
  ['rise %/min',d.rise_pct_min],['full in',d.eta_full_s?Math.round(d.eta_full_s/60)+' min':null]]);
 card('c_land','Land',l.state,[['still water',l.still==null?null:(l.still*100).toFixed(1)+'%'],['patches',l.patches],
  ['largest patch',l.largest==null?null:(l.largest*100).toFixed(1)+'%'],['growth %/min',l.growth_pct_min]]);}
new EventSource('/api/stream').onmessage=e=>render(JSON.parse(e.data));
function spark(id,a,col){const c=$(id),x=c.getContext('2d');c.width=c.clientWidth;c.height=60;x.clearRect(0,0,c.width,60);
 if(!a||a.length<2)return;const mx=Math.max(...a,1e-6);x.beginPath();
 a.forEach((v,i)=>{const X=i/(a.length-1)*c.width,Y=56-(v/mx)*52;i?x.lineTo(X,Y):x.moveTo(X,Y)});
 x.strokeStyle=col;x.lineWidth=2;x.stroke();}
async function hist(){try{const h=await(await fetch('/api/history?n=300')).json();
 spark('s_river',h.river,'#22d3ee');spark('s_drain',h.drain,'#ff9f1c');spark('s_land',h.land,'#2ecc71');}catch(e){}}
setInterval(hist,3000);hist();
</script></body></html>"""

if __name__ == "__main__":
    det = Detector(); det.start()
    if PUSH_URL: threading.Thread(target=pusher, daemon=True).start()
    print(f"GUI + API on http://0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()