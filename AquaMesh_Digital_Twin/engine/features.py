"""
Stage 1-3 of the pipeline: turn raw windows into EVIDENCE FACTS (T/F/U/N) with provenance.

Provenance tags:  MEASURED  (direct comparison of a raw reading)      ESTIMATED (needs calibration / statistics)
                  SIMULATED (anything produced by the simulator)       UNAVAILABLE (no usable data)
"""
from . import calibration as C

STUCK_N = 10          # identical non-zero readings in a row => stuck sensor


def _in(win, now, span):
    return [(t, v) for t, v in win if now - span <= t <= now]


def mean(a):
    return sum(a) / len(a) if a else None


def slope_per_min(pts):
    """Least-squares slope of (t, v) in units per minute."""
    n = len(pts)
    if n < 3:
        return None
    t0 = pts[0][0]
    xs = [(t - t0) / 60.0 for t, _ in pts]
    ys = [v for _, v in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return None if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def stuck(win):
    last = [v for _, v in list(win)[-STUCK_N:]]
    return len(last) >= STUCK_N and len(set(last)) == 1 and last[0] != 0


def observe(nodes, cal, now, uart_ok, installed, sim, grace=False):
    """nodes: hw -> NodeState dict.  Returns (leaves, metrics)."""
    lim, prov_m, prov_e = cal["limits"], ("SIMULATED" if sim else "MEASURED"), ("SIMULATED" if sim else "ESTIMATED")
    L, M = {}, {}

    def put(name, val, prov, detail=""):
        L[name] = (val, prov if val in ("T", "F") else "UNAVAILABLE", detail)

    def age(hw):
        n = nodes.get(hw)
        return None if not n or n.get("last") is None else now - n["last"]

    def fresh(hw):
        a = age(hw)
        return None if a is None else a <= lim["online_s"]

    def win(hw, k):
        n = nodes.get(hw)
        return n["win"].get(k, []) if n else []

    # ---- link layer ----
    for hw, fact in (("FLOOD01", "rain_node_fresh"), ("GAS01", "area_node_fresh")):
        a = age(hw)
        if a is None and grace:
            put(fact, "U", prov_m, "gateway just started: listening")
        elif a is None:
            put(fact, "F", prov_m, "never heard")
        else:
            put(fact, "T" if a <= lim["online_s"] else "F", prov_m, f"last packet {a:.0f}s ago")
        M[hw + "_age_s"] = None if a is None else round(a, 1)
    losses = [n["loss_pct"] for hw, n in nodes.items() if hw in ("FLOOD01", "GAS01")]
    if losses:
        put("link_loss_high", "T" if max(losses) >= lim["loss_high_pct"] else "F", prov_m, f"max loss {max(losses):.0f}%")
    else:
        put("link_loss_high", "U", prov_m, "no packets yet")
    put("gateway_uart_ok", "T" if uart_ok else "F", prov_m, "serial open" if uart_ok else "no serial port")

    # ---- rain node ----
    rc = cal["rain"]
    rf = fresh("FLOOD01")
    w = win("FLOOD01", "RAIN")
    s60, s180 = _in(w, now, 60), _in(w, now, 180)
    if rf and w:
        raw = w[-1][1]
        M["rain_raw"] = raw
        valid = not stuck(w)
        put("rain_sensor_valid", "T" if valid else "F", prov_m, "ok" if valid else f"stuck at {raw} for {STUCK_N}+ packets")
        if len(s60) >= 3:
            f60 = sum(1 for _, v in s60 if v >= rc["wet_on_raw"]) / len(s60)
            M["rain_wet_frac_60"] = round(f60, 2)
            put("rain_wet", "T" if f60 >= 0.15 else "F", prov_m, f"wet {f60*100:.0f}% of last 60s (raw>={rc['wet_on_raw']})")
        else:
            put("rain_wet", "U", prov_m, "warming up (<3 samples)")
        if len(s180) >= 3:
            f180 = sum(1 for _, v in s180 if v >= rc["wet_on_raw"]) / len(s180)
            M["rain_wet_frac_180"] = round(f180, 2)
            put("rain_persistent", "T" if f180 >= 0.35 else "F", prov_m, f"wet {f180*100:.0f}% of last 3 min")
        else:
            put("rain_persistent", "U", prov_m, "warming up")
        a, b = [v for t, v in _in(w, now, 30)], [v for t, v in w if now - 60 <= t < now - 30]
        if len(a) >= 3 and len(b) >= 3:
            ma, mb = mean(a), mean(b)
            M["rain_mean_30"], M["rain_mean_prev_30"] = round(ma, 1), round(mb, 1)
            put("rain_intensifying", "T" if ma >= 150 and ma >= 1.5 * mb + 80 else "F", prov_e, f"mean30 {ma:.0f} vs prev {mb:.0f}")
        else:
            put("rain_intensifying", "U", prov_e, "warming up")
        pk = max(v for _, v in _in(w, now, 30)) if _in(w, now, 30) else raw
        M["rain_peak_30"] = pk
        put("rain_heavy", "T" if pk >= rc["heavy_raw"] else "F", prov_m, f"30s peak {pk} (heavy>={rc['heavy_raw']})")
        recent = [v for _, v in _in(w, now, 10)] or [raw]
        st = nodes["FLOOD01"].get("status")
        put("rain_contact", "T" if (st == "ALERT" or max(recent) > rc["contact_raw"]) else "F", prov_m,
            f"node STATUS={st}, raw {raw} (node alerts above {rc['contact_raw']})")
        M["rain_mm_h"] = C.rain_mm_h(cal, raw)
    else:
        why = "node not fresh" if rf is False else "no data"
        for f in ("rain_sensor_valid", "rain_wet", "rain_persistent", "rain_intensifying", "rain_heavy", "rain_contact"):
            put(f, "U", prov_m, why)

    # ---- soil / gas node ----
    af = fresh("GAS01")
    sw, gw = win("GAS01", "SOIL"), win("GAS01", "MQ")
    if af and sw:
        raw = sw[-1][1]
        M["soil_raw"] = raw
        put("soil_sensor_valid", "F" if stuck(sw) else "T", prov_m, "stuck" if stuck(sw) else "ok")
        pct = C.soil_pct(cal, mean([v for _, v in _in(sw, now, 30)]) or raw)
        M["soil_pct"] = pct
        sat = cal["soil"].get("saturated_pct", 70)
        if pct is None:
            put("soil_wet_high", "U", prov_e, "CALIBRATION_REQUIRED: soil direction/dry/wet raw not verified")
        else:
            put("soil_wet_high", "T" if pct >= sat else "F", prov_e, f"{pct:.0f}% (limit {sat}%)")
    else:
        for f in ("soil_sensor_valid", "soil_wet_high"):
            put(f, "U", prov_m, "node not fresh" if af is False else "no data")
    gc = cal["gas"]
    if af and gw:
        raw = gw[-1][1]
        M["gas_raw"] = raw
        put("gas_sensor_valid", "F" if stuck(gw) else "T", prov_m, "stuck" if stuck(gw) else "ok")
        last3 = [v for _, v in gw[-3:]]
        if len(last3) >= 2:
            over = sum(1 for v in last3 if v > gc["alert_raw"])
            put("gas_sustained", "T" if over >= 2 else "F", prov_m, f"{over}/{len(last3)} recent packets > {gc['alert_raw']}")
        else:
            put("gas_sustained", "U", prov_m, "warming up")
        g60 = _in(gw, now, 60)
        sl = slope_per_min(g60) if len(g60) >= 6 else None
        M["gas_slope_per_min"] = None if sl is None else round(sl, 1)
        put("gas_rising", "U" if sl is None else ("T" if sl >= gc["rising_raw_per_min"] else "F"), prov_e,
            "warming up" if sl is None else f"slope {sl:.0f}/min (limit {gc['rising_raw_per_min']})")
    else:
        for f in ("gas_sensor_valid", "gas_sustained", "gas_rising"):
            put(f, "U", prov_m, "node not fresh" if af is False else "no data")

    # ---- hydrology nodes: NOT_INSTALLED is 'N', never 0 ----
    hc = cal.get("hydro", {})
    def hydro(fact, hw, key, test, label):
        if not installed.get(hw):
            L[fact] = ("N", "UNAVAILABLE", f"{hw} not installed")
            return
        fr, ww = fresh(hw), win(hw, key)
        if fr and ww:
            put(fact, "T" if test(ww[-1][1]) else "F", prov_m, f"{label} {ww[-1][1]}")
            M[f"{hw}_{key}"] = ww[-1][1]
        else:
            put(fact, "U", prov_m, "node not fresh" if fr is False else "no data")
    hydro("drain_level_high", "DRAIN01", "LVL", lambda v: v >= hc.get("drain_alert_cm", 9), "drain level cm")
    hydro("drain_flow_low", "DRAIN01", "FLOW", lambda v: v < hc.get("flow_low_lpm", 5), "flow L/min")
    hydro("river_level_high", "RIVER01", "LVL", lambda v: v >= hc.get("river_alert_cm", 20), "river level cm")
    ovf_seen = bool(win("GAS01", "OVF"))
    if not (ovf_seen or installed.get("OVERFLOW")):
        L["overflow_contact"] = ("N", "UNAVAILABLE", "no overflow switch fitted")
    elif af and ovf_seen:
        put("overflow_contact", "T" if win("GAS01", "OVF")[-1][1] else "F", prov_m, "float switch")
    else:
        put("overflow_contact", "U", prov_m, "node not fresh")
    return L, M
