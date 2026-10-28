"""Versioned calibration.  Raw readings are always stored raw; physical units appear ONLY when verified."""
import json, os

DEFAULT = {
    "version": "cal-0.1-UNVERIFIED",
    "rain": {"verified": False, "wet_on_raw": 100, "heavy_raw": 1000, "contact_raw": 500, "mm_per_hour_at_4095": None},
    "soil": {"verified": False, "direction": "unknown", "dry_raw": None, "wet_raw": None, "saturated_pct": 70},
    "gas": {"alert_raw": 1500, "rising_raw_per_min": 60},
    "hx": {"verified": False},
    "limits": {"online_s": 15, "stale_s": 60, "loss_high_pct": 30},
    "hydro": {"drain_alert_cm": 9, "river_alert_cm": 20, "flow_low_lpm": 5},
}

# Used ONLY by the simulator so the demo can exercise every rule. Everything it feeds is labelled SIMULATED.
SIM = {
    "version": "cal-SIM-demo",
    "rain": {"verified": True, "wet_on_raw": 100, "heavy_raw": 1000, "contact_raw": 500, "mm_per_hour_at_4095": 120.0},
    "soil": {"verified": True, "direction": "lower_is_wetter", "dry_raw": 3800, "wet_raw": 1200, "saturated_pct": 70},
    "gas": {"alert_raw": 1500, "rising_raw_per_min": 60},
    "hx": {"verified": False},
    "limits": {"online_s": 15, "stale_s": 60, "loss_high_pct": 30},
    "hydro": {"drain_alert_cm": 9, "river_alert_cm": 20, "flow_low_lpm": 5},
}


def load(path):
    try:
        with open(path) as f:
            c = json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULT))
    for k, v in DEFAULT.items():
        if isinstance(v, dict):
            c.setdefault(k, {})
            for kk, vv in v.items():
                c[k].setdefault(kk, vv)
        else:
            c.setdefault(k, v)
    return c


def soil_pct(cal, raw):
    s = cal["soil"]
    if raw is None or not s.get("verified") or s.get("direction") not in ("lower_is_wetter", "higher_is_wetter"):
        return None
    d, w = s.get("dry_raw"), s.get("wet_raw")
    if d is None or w is None or d == w:
        return None
    return round(100 * max(0.0, min(1.0, (raw - d) / (w - d))), 1)


def rain_mm_h(cal, raw):
    r = cal["rain"]
    if raw is None or not r.get("verified") or not r.get("mm_per_hour_at_4095"):
        return None
    return round(max(0.0, min(1.0, raw / 4095.0)) * r["mm_per_hour_at_4095"], 1)


def quality(cal):
    """Quality label per sensor, shown on the dashboard."""
    return {
        "rain": "CALIBRATED" if cal["rain"].get("verified") and cal["rain"].get("mm_per_hour_at_4095") else "RAW_ONLY",
        "soil": "CALIBRATED" if soil_pct(cal, cal["soil"].get("dry_raw") or 0) is not None else "CALIBRATION_REQUIRED",
        "gas": "THRESHOLD_FROM_FIRMWARE",
        "hx": "UNCALIBRATED_RAW",
    }
