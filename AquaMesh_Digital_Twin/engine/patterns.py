"""
Stage B: historical/template pattern matching on the rain-wetness window.
Similarity is NOT probability; matches below MIN_SIM are rejected ("no known pattern").
Templates are synthetic shapes (labelled SIMULATED) until real labelled storms are recorded.
"""
import math

BINS, MIN_SIM, SCALE_FLOOR = 12, 0.55, 300.0


def _tpl():
    ramp = [i / (BINS - 1) for i in range(BINS)]
    return {
        "FLAT_DRY":        ("No wetness", [0.0] * BINS),
        "BURST_DECAY":     ("Single burst then decay (drops / splash)", [0, 0, 0.1, 1, 0.8, 0.6, 0.45, 0.3, 0.2, 0.1, 0.05, 0]),
        "RAMP_UP":         ("Building storm (steady increase)", ramp),
        "SUSTAINED_WET":   ("Sustained wetness plateau", [0.1, 0.5, 0.8, 0.9, 0.85, 0.9, 0.8, 0.85, 0.9, 0.8, 0.85, 0.9]),
        "INTERMITTENT":    ("Intermittent showers", [0.8, 0.0, 0.7, 0.0, 0.9, 0.1, 0.6, 0.0, 0.8, 0.0, 0.7, 0.1]),
        "STUCK_HIGH":      ("Flat high line (stuck sensor)", [0.95] * BINS),
    }


TPL = _tpl()


def resample(win, now, span=180):
    """Mean per time-bin over the last `span` seconds; None when not enough data."""
    pts = [(t, v) for t, v in win if now - span <= t <= now]
    if len(pts) < 6:
        return None
    step, out = span / BINS, []
    for b in range(BINS):
        lo, hi = now - span + b * step, now - span + (b + 1) * step
        vs = [v for t, v in pts if lo <= t < hi + (1e-9 if b == BINS - 1 else 0)]
        out.append(sum(vs) / len(vs) if vs else (out[-1] if out else 0.0))
    return out


def match(win, now):
    s = resample(win, now)
    if s is None:
        return {"status": "INSUFFICIENT_DATA", "best": None, "all": []}
    scale = max(max(s), SCALE_FLOOR)
    norm = [v / scale for v in s]
    res = []
    for k, (label, shape) in TPL.items():
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(norm, shape)) / BINS)
        res.append({"id": k, "label": label, "similarity": round(100 * math.exp(-d / 0.35), 1), "shape": shape})
    res.sort(key=lambda r: -r["similarity"])
    best = res[0]
    ok = best["similarity"] >= 100 * MIN_SIM
    return {"status": "MATCH" if ok else "NO_KNOWN_PATTERN", "best": best if ok else None, "all": res[:4],
            "series": [round(v, 1) for v in s], "norm": [round(v, 3) for v in norm],
            "note": "similarity is not probability; templates are synthetic"}
