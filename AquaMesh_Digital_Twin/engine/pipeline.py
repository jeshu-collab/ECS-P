"""
The staged pipeline:  ingest -> validate -> estimate state -> reason (forward chaining) -> hysteresis -> decide & explain -> record.

Twin is clock-injectable (clock=callable) so the simulator and the tests can run faster than real time.
"""
import collections, json, threading, time

from . import calibration as C, features, hypothesis, patterns
from .db import DB
from .knowledge import KB, CAPABILITY, RULE_VERSION
from .logic import forward_chain, explain, supporting_leaves, counterfactual, T, F, U, N, NAMES
from .packet import validate
from .statemachine import Hysteresis

WEB = {"FLOOD01": "RAIN-01", "DRAIN01": "DRAIN-01", "GAS01": "AREA-01", "RIVER01": "RIVER-01"}
ELEV = {"RAIN-01": 14, "DRAIN-01": 11, "AREA-01": 7, "RIVER-01": 4}
LOSS_WINDOW = 50
WIN_KEEP_S = 400
FLOOD_WATCH = ("flood_critical", "flood_warning", "flood_watch")
RISK_BASE = {"UNASSESSED": None, "NORMAL": 5, "WATCH": 35, "WARNING": 62, "CRITICAL": 88}
REASON = {  # fact -> reason code shown in alerts / events
    "rain_intensifying_confirmed": "RAINFALL_TREND_RISING", "rain_sustained": "RAIN_SUSTAINED", "rain_contact_confirmed": "NODE_RAIN_ALERT",
    "ground_saturated": "SOIL_MOISTURE_ELEVATED", "drainage_stressed": "DRAIN_LEVEL_HIGH", "drain_blockage_suspected": "DRAIN_BLOCKAGE_SUSPECTED",
    "river_backwater": "RIVER_BACKWATER", "overflow_confirmed": "OVERFLOW_CONTACT", "rain_heavy_confirmed": "HEAVY_WETNESS_PEAK",
    "gas_hazard": "GAS_OVER_THRESHOLD_SUSTAINED", "gas_rising": "GAS_RISING_FAST", "rain_wet_confirmed": "RAIN_CONFIRMED",
}


class Twin:
    def __init__(self, db_path, calib, clock=time.time, mode="LIVE", allow_future_nodes=False, installed=None, on_event=None):
        self.db, self.cal, self.clock, self.mode = DB(db_path), calib, clock, mode
        self.sim = mode == "SIMULATED"
        self.allow_future = allow_future_nodes
        self.installed = installed or {"FLOOD01": True, "GAS01": True, "DRAIN01": False, "RIVER01": False}
        self.nodes, self.lock = {}, threading.RLock()
        self.uart_ok, self.started = False, clock()
        self.gw = {"packets_total": 0, "bad_lines": 0, "duplicates": 0, "last_rx": None, "last_line": ""}
        self.flood_sm, self.gas_sm = Hysteresis(), Hysteresis("UNASSESSED", up_s=6, down_s=30)
        self.health = "OK"
        self.last = None
        self.events = collections.deque(maxlen=80)           # in-memory mirror for the dashboard feed
        self.on_event = on_event or (lambda e: None)
        self._last_db_ts, self._last_sig = 0.0, None

    # ───────────────────────── stage 1-2: ingest + validate ─────────────────────────
    def reject(self, line, errs):
        with self.lock:
            self.gw["bad_lines"] += 1
            self.db.rejected(self.clock(), line, "; ".join(errs))

    def ingest(self, d, text, raw_line=""):
        now = self.clock()
        pkt, errs, _ = validate(d, text, self.allow_future)
        if pkt is None:
            self.reject(raw_line or text, errs)
            return None
        hw = pkt["hw"]
        with self.lock:
            n = self.nodes.setdefault(hw, {"received": 0, "lost": collections.deque(maxlen=LOSS_WINDOW), "pkt": None, "status": None,
                                           "win": collections.defaultdict(list), "last": None, "loss_pct": 0.0, "first": now})
            self.gw["packets_total"] += 1
            self.gw["last_rx"], self.gw["last_line"] = now, raw_line or text
            if pkt["pkt"] is not None and n["pkt"] is not None:
                if pkt["pkt"] == n["pkt"] and n.get("last_vals") == pkt["values"]:
                    self.gw["duplicates"] += 1
                    return None
                gap = pkt["pkt"] - n["pkt"] - 1
                if gap > 0:
                    for _ in range(min(gap, LOSS_WINDOW)):
                        n["lost"].append(1)
                elif pkt["pkt"] <= n["pkt"]:
                    self._event("NODE_RESTART", "info", f"{hw} restarted", "Packet counter went backwards", None, now)
            n["lost"].append(0)
            n["loss_pct"] = round(100 * sum(n["lost"]) / max(1, len(n["lost"])), 1)
            n["pkt"], n["received"], n["last"], n["status"] = pkt["pkt"], n["received"] + 1, now, pkt["status"]
            n["rssi"], n["snr"], n["vbat"], n["last_vals"] = pkt.get("rssi"), pkt.get("snr"), pkt.get("vbat"), dict(pkt["values"])
            for k, v in pkt["values"].items():
                w = n["win"][k]
                w.append((now, v))
                while w and now - w[0][0] > WIN_KEEP_S:
                    w.pop(0)
            self.db.telemetry(now, pkt, self.cal["version"], self.mode)
        return pkt

    # ───────────────────────── stage 3-6: estimate, reason, decide ─────────────────────────
    def assess(self):
        now = self.clock()
        with self.lock:
            leaves, metrics = features.observe(self.nodes, self.cal, now, self.uart_ok, self.installed, self.sim, grace=(now - self.started < 25))
            wm, rule_vals, trace, rounds = forward_chain(KB, leaves)
            hyps = hypothesis.rank(wm)
            pat = patterns.match(self.nodes.get("FLOOD01", {}).get("win", {}).get("RAIN", []), now)

            cand = self._flood_candidate(wm)
            fstate, ftrans = self.flood_sm.step(cand, now)
            gcand = "WARNING" if wm.get("gas_hazard") == T else "WATCH" if wm.get("gas_watch") == T else "NORMAL" if wm.get("gas_normal") == T else "UNASSESSED"
            gstate, gtrans = self.gas_sm.step(gcand, now)
            health = "SENSOR_FAULT" if wm.get("sensor_fault") == T else "DEGRADED" if wm.get("system_degraded") == T else \
                     "PARTIAL_COVERAGE" if any(v[0] == N for v in leaves.values()) else "OK"
            not_assessable = sorted({CAPABILITY[f] for f in CAPABILITY if wm.get(f) == N})
            unresolved = self._unresolved(wm, leaves, fstate)
            known = [v for v in leaves.values() if v[0] in (T, F)]
            applicable = [v for v in leaves.values() if v[0] != N]
            coverage = round(100 * len(known) / max(1, len(applicable)))
            risk = self._risk(fstate, wm)
            a = {"flood": fstate, "gas": gstate, "health": health, "risk_index": risk, "candidate": cand, "coverage_pct": coverage}
            sig = (fstate, gstate, health, tuple(sorted((k, v) for k, v in wm.items() if v in (T,) and k in KB.derived)))
            aid = None
            if sig != self._last_sig or now - self._last_db_ts >= 10 or ftrans or gtrans:
                aid = self.db.assessment(now, self.mode, a, self.cal["version"], RULE_VERSION, leaves, metrics, hyps[0]["id"])
                self._last_sig, self._last_db_ts = sig, now
            if aid is None and (ftrans or gtrans or self.health != health):
                aid = self.db.assessment(now, self.mode, a, self.cal["version"], RULE_VERSION, leaves, metrics, hyps[0]["id"])
            if ftrans:
                self._transition("flood", ftrans, wm, leaves, aid, now)
            if gtrans:
                self._transition("gas", gtrans, wm, leaves, aid, now)
            health = self._dwell_health(health, now)
            if health != self.health:
                old, self.health = self.health, health
                if health in ("DEGRADED", "SENSOR_FAULT") or old in ("DEGRADED", "SENSOR_FAULT"):
                    sev = "warn" if health in ("DEGRADED", "SENSOR_FAULT") else "info"
                    goal = "sensor_fault" if health == "SENSOR_FAULT" else "system_degraded"
                    self._event("HEALTH_" + health, sev, f"System health {old} -> {health}", self._why_short(goal, wm, leaves), goal, now, aid, leaves, wm)
            self.last = {"wm": wm, "leaves": leaves, "rule_vals": rule_vals, "trace": trace, "rounds": rounds, "metrics": metrics, "hyps": hyps,
                         "pattern": pat, "a": a, "t": now, "aid": aid, "not_assessable": not_assessable, "unresolved": unresolved,
                         "hys": {"flood": self.flood_sm.info(now), "gas": self.gas_sm.info(now)}}
            return self.last

    def _dwell_health(self, cand, now):
        """Health also uses hysteresis: a new value must persist (10 s to worsen, 30 s to recover)."""
        if cand == self.health:
            self._h_pend = None
            return self.health
        if getattr(self, "_h_pend", None) is None or self._h_pend[0] != cand:
            self._h_pend = (cand, now)
        worse = {"OK": 0, "PARTIAL_COVERAGE": 0, "DEGRADED": 1, "SENSOR_FAULT": 2}
        need = 10 if worse[cand] > worse[self.health] else 30
        return cand if now - self._h_pend[1] >= need else self.health

    def _flood_candidate(self, wm):
        for lvl, fact in (("CRITICAL", "flood_critical"), ("WARNING", "flood_warning"), ("WATCH", "flood_watch")):
            if wm.get(fact) == T:
                return lvl
        return "NORMAL" if wm.get("flood_normal") == T else "UNASSESSED"

    def _unresolved(self, wm, leaves, state):
        """Higher severities that are UNKNOWN (not FALSE): we cannot rule them out. Uses backward chaining to name the missing evidence."""
        out = []
        order = {"WATCH": 1, "WARNING": 2, "CRITICAL": 3}
        for lvl, fact in (("WATCH", "flood_watch"), ("WARNING", "flood_warning"), ("CRITICAL", "flood_critical")):
            if wm.get(fact) == U and order[lvl] > order.get(state, 0):
                ex = explain(KB, wm, leaves, fact)
                out.append({"level": lvl, "goal": fact, "missing": [{"fact": m, "label": KB.facts[m]["label"], "detail": leaves.get(m, (0, 0, ""))[2]} for m in ex["missing_evidence"]]})
        return out

    def _risk(self, state, wm):
        base = RISK_BASE.get(state)
        if base is None:
            return None
        bonus = sum(1 for f in ("rain_wet_confirmed", "rain_sustained", "rain_intensifying_confirmed", "rain_heavy_confirmed", "ground_saturated",
                                "drainage_stressed", "rain_contact_confirmed") if wm.get(f) == T)
        return min(100, base + bonus * 3)

    def _why_short(self, goal, wm, leaves):
        ex = explain(KB, wm, leaves, goal)
        return "; ".join(l.strip("- ").strip() for l in ex["text"][:4])

    # ───────────────────────── stage 7: decide, explain, record ─────────────────────────
    def _evidence(self, goal, wm, leaves):
        sup = supporting_leaves(KB, wm, goal) if wm.get(goal) == T else []
        return {"goal": goal, "reason_codes": [REASON[f] for f in KB.derived if f in REASON and wm.get(f) == T and f in _closure(goal, wm)] or
                                              [REASON.get(goal, goal.upper())],
                "supporting": {f: {"value": leaves[f][0], "provenance": leaves[f][1], "detail": leaves[f][2]} for f in sup if f in leaves},
                "data_quality": self.quality(), "calibration": self.cal["version"], "kb_version": RULE_VERSION,
                "node_health": {hw: self.node_health(hw, self.clock()) for hw in ("FLOOD01", "GAS01")},
                "not_assessable": sorted({CAPABILITY[f] for f in CAPABILITY if wm.get(f) == N}), "mode": self.mode}

    def _transition(self, domain, tr, wm, leaves, aid, now):
        goal = {"flood": {"WATCH": "flood_watch", "WARNING": "flood_warning", "CRITICAL": "flood_critical", "NORMAL": "flood_normal", "UNASSESSED": "flood_assessable"},
                "gas": {"WARNING": "gas_hazard", "WATCH": "gas_watch", "NORMAL": "gas_normal", "UNASSESSED": "area_node_fresh"}}[domain][tr["to"]]
        ev = self._evidence(goal, wm, leaves)
        self.db.transition(now, domain, tr["from"], tr["to"], ev["reason_codes"], aid)
        to = tr["to"]
        sev = "critical" if to == "CRITICAL" or (domain == "gas" and to == "WARNING") else "warn" if to in ("WARNING", "WATCH", "UNASSESSED") else "info"
        title = f"{domain.upper()} {tr['from']} -> {tr['to']}"
        etype = f"{domain.upper()}_RISK_{tr['to']}"
        self._event(etype, sev, title, ", ".join(ev["reason_codes"]) or "-", goal, now, aid, leaves, wm, ev)

    def _event(self, etype, sev, title, detail, goal, now, aid=None, leaves=None, wm=None, ev=None):
        eid = None
        if goal and leaves is not None:
            ev = ev or self._evidence(goal, wm, leaves)
            eid = self.db.event(now, etype, sev, title, detail, goal, ev, aid)
        else:
            eid = self.db.event(now, etype, sev, title, detail, goal, {"mode": self.mode}, aid)
        e = {"id": eid, "t": now, "type": etype, "title": title, "detail": detail, "severity": sev, "goal": goal, "assessment_id": aid}
        self.events.appendleft(e)
        print(f"[{sev.upper():8}] {title}: {detail}")
        self.on_event(e)

    # ───────────────────────── helpers used by the API ─────────────────────────
    def node_health(self, hw, now):
        n = self.nodes.get(hw)
        if not n or n.get("last") is None:
            return "NO_DATA" if self.installed.get(hw) else "NO_SENSOR"
        age = now - n["last"]
        return "ONLINE" if age <= self.cal["limits"]["online_s"] else "STALE" if age <= self.cal["limits"]["stale_s"] else "OFFLINE"

    def quality(self):
        q = C.quality(self.cal)
        a = self.last
        if a:
            if a["wm"].get("rain_sensor_fault") == T: q["rain"] = "SENSOR_FAULT"
            if a["wm"].get("soil_sensor_fault") == T: q["soil"] = "SENSOR_FAULT"
            if a["wm"].get("gas_sensor_fault") == T: q["gas"] = "SENSOR_FAULT"
            hx = (self.nodes.get("FLOOD01") or {}).get("last_vals", {}).get("HX")
            if hx == 0: q["hx"] = "NOT_READY(0)"
        q["drain"] = "SIMULATED" if self.sim and self.installed.get("DRAIN01") else "NOT_INSTALLED" if not self.installed.get("DRAIN01") else "OK"
        q["river"] = "SIMULATED" if self.sim and self.installed.get("RIVER01") else "NOT_INSTALLED" if not self.installed.get("RIVER01") else "OK"
        return q

    def why(self, goal):
        a = self.last or self.assess()
        if goal not in KB.facts:
            return {"error": f"unknown goal '{goal}'", "goals": KB.derived}
        return explain(KB, a["wm"], a["leaves"], goal)

    def counterfactual(self, overrides, assessment_id=None, watch=None):
        """overrides: {fact: 'T'|'F'|'U'|'N'}.  Uses a recorded assessment (leaf facts) or the latest one."""
        leaves = None
        if assessment_id:
            row = self.db.assessment_row(int(assessment_id))
            if row:
                leaves = {k: tuple(v) for k, v in json.loads(row["leaves_json"]).items()}
        if leaves is None:
            leaves = (self.last or self.assess())["leaves"]
        ov = {k: v for k, v in overrides.items() if k in KB.facts and v in (T, F, U, N)}
        return counterfactual(KB, leaves, ov, watch or list(FLOOD_WATCH) + ["flood_normal", "drainage_stressed", "drain_blockage_suspected", "gas_hazard"])

    def replay(self, aid):
        """Re-run forward chaining on a STORED assessment: the full graph exactly as the engine saw it then."""
        row = self.db.assessment_row(int(aid))
        if not row:
            return None
        leaves = {k: tuple(v) for k, v in json.loads(row["leaves_json"]).items()}
        wm, rv, trace, rounds = forward_chain(KB, leaves)
        return {"assessment": {k: row[k] for k in ("id", "ts", "flood_state", "gas_state", "health", "risk_index", "kb_version", "calib", "mode")},
                "graph": graph(wm, rv, leaves), "trace": trace, "rounds": rounds, "hypotheses": hypothesis.rank(wm), "metrics": json.loads(row["metrics_json"])}

    # ───────────────────────── API snapshot ─────────────────────────
    def snapshot(self, gateway_extra=None):
        a = self.last or self.assess()
        now = self.clock()
        cal, M, wm, L = self.cal, a["metrics"], a["wm"], a["leaves"]
        nodes = {}
        for hw, web in WEB.items():
            n = self.nodes.get(hw)
            base = {"hw_id": hw, "elevation_m": ELEV[web], "installed": bool(self.installed.get(hw)), "health": self.node_health(hw, now)}
            if n and n.get("last") is not None:
                lv = n.get("last_vals", {})
                base.update(age_s=round(now - n["last"], 1), status=n["status"], pkt=n["pkt"], received=n["received"],
                            packet_loss_pct=n["loss_pct"], rssi_dbm=n.get("rssi"), snr_db=n.get("snr"), vbat=n.get("vbat"))
                if hw == "FLOOD01":
                    base.update(rain_raw=lv.get("RAIN"), pressure_raw=lv.get("HX"), alert_raw_threshold=cal["rain"]["contact_raw"],
                                rain_mm_h=C.rain_mm_h(cal, lv.get("RAIN")), wetness_mean_30=M.get("rain_mean_30"), wetness_peak_30=M.get("rain_peak_30"),
                                wetness_frac_60=M.get("rain_wet_frac_60"))
                if hw == "GAS01":
                    base.update(soil_raw=lv.get("SOIL"), soil_moisture_pct=M.get("soil_pct"), gas_raw=lv.get("MQ"),
                                gas_alert_threshold=cal["gas"]["alert_raw"], gas_alert=wm.get("gas_hazard") == T, overflow=lv.get("OVF"),
                                accel_x=lv.get("AX"), accel_y=lv.get("AY"), accel_z=lv.get("AZ"),
                                gyro_x=lv.get("GX"), gyro_y=lv.get("GY"), gyro_z=lv.get("GZ"))
                if hw == "DRAIN01":
                    base.update(drain_level_cm=lv.get("LVL"), drain_flow_lpm=lv.get("FLOW"))
                if hw == "RIVER01":
                    base.update(river_level_cm=lv.get("LVL"))
            nodes[web] = base
        prov = "SIMULATED" if self.sim else "MEASURED"
        assessment = self.assessment_public(a)
        gw = dict(self.gw)
        gateway = {"serial_connected": self.uart_ok, "packets_total": gw["packets_total"], "bad_lines": gw["bad_lines"], "duplicates": gw["duplicates"],
                   "last_rx_age_s": None if gw["last_rx"] is None else round(now - gw["last_rx"], 1), "uptime_s": int(now - self.started)}
        gateway.update(gateway_extra or {})
        return {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "server_time": now, "mode": "SIMULATED" if self.sim else "LIVE", "provenance_default": prov,
                "nodes": nodes, "assessment": assessment, "gateway": gateway,
                "events": [{"t": e["t"], "title": e["title"], "detail": e["detail"], "severity": e["severity"], "id": e["id"], "goal": e["goal"]} for e in list(self.events)[:40]]}

    def assessment_public(self, a):
        wm, L = a["wm"], a["leaves"]
        flood, gas = a["a"]["flood"], a["a"]["gas"]
        alerts = []
        if flood in ("WATCH", "WARNING", "CRITICAL"):
            goal = {"WATCH": "flood_watch", "WARNING": "flood_warning", "CRITICAL": "flood_critical"}[flood]
            alerts.append({"level": flood, "domain": "flood", "goal": goal, "title": f"Flood {flood.lower()}", "reasons": self._evidence(goal, wm, L)["reason_codes"]})
        if gas in ("WATCH", "WARNING"):
            goal = "gas_hazard" if gas == "WARNING" else "gas_watch"
            alerts.append({"level": "CRITICAL" if gas == "WARNING" else "WATCH", "domain": "gas", "goal": goal, "title": "Gas hazard" if gas == "WARNING" else "Gas rising", "reasons": [REASON.get(goal, goal)]})
        if a["a"]["health"] == "SENSOR_FAULT":
            alerts.append({"level": "FAULT", "domain": "health", "goal": "sensor_fault", "title": "Sensor fault", "reasons": ["SENSOR_FAILS_VALIDATION"]})
        if a["a"]["health"] == "DEGRADED":
            alerts.append({"level": "DEGRADED", "domain": "health", "goal": "system_degraded", "title": "Coverage / link degraded", "reasons": ["NODE_OR_LINK_UNAVAILABLE"]})
        if flood == "UNASSESSED":
            alerts.append({"level": "UNASSESSED", "domain": "flood", "goal": "flood_assessable", "title": "Flood risk cannot be assessed", "reasons": ["RAIN_NODE_UNUSABLE"]})
        for u in a["unresolved"]:
            alerts.append({"level": "UNRESOLVED", "domain": "flood", "goal": u["goal"], "title": f"{u['level'].title()} cannot be ruled out", "reasons": [m["label"] + ": " + m["detail"] for m in u["missing"]]})
        ind = [
            {"label": "Rain wetness (30 s mean, raw ADC)", "value": a["metrics"].get("rain_mean_30"), "unit": "raw", "type": "MEASURED" if not self.sim else "SIMULATED"},
            {"label": "Rain wetness (30 s peak, raw ADC)", "value": a["metrics"].get("rain_peak_30"), "unit": "raw", "type": "MEASURED" if not self.sim else "SIMULATED"},
            {"label": "Rainfall rate", "value": a["metrics"].get("rain_mm_h"), "unit": "mm/h", "type": "ESTIMATED" if a["metrics"].get("rain_mm_h") is not None else "UNAVAILABLE", "why": None if a["metrics"].get("rain_mm_h") is not None else "rain sensor not calibrated"},
            {"label": "Soil moisture", "value": a["metrics"].get("soil_pct"), "unit": "%", "type": "ESTIMATED" if a["metrics"].get("soil_pct") is not None else "UNAVAILABLE", "why": None if a["metrics"].get("soil_pct") is not None else "CALIBRATION_REQUIRED"},
            {"label": "Gas slope", "value": a["metrics"].get("gas_slope_per_min"), "unit": "raw/min", "type": "ESTIMATED" if a["metrics"].get("gas_slope_per_min") is not None else "UNAVAILABLE"},
            {"label": "Drain / river level", "value": None, "unit": "cm", "type": "SIMULATED" if self.sim and self.installed.get("DRAIN01") else "UNAVAILABLE", "why": "" if self.sim else "NOT_INSTALLED"},
            {"label": "30-second flood probability", "value": None, "unit": "%", "type": "UNAVAILABLE", "why": "no validated predictor (needs labelled flood data)"},
        ]
        return {
            "kb_version": RULE_VERSION, "calibration": self.cal["version"], "flood_state": flood, "gas_state": gas, "health": a["a"]["health"],
            "risk_index": a["a"]["risk_index"], "risk_index_label": "LIVE HEURISTIC SCORE (not a probability)", "evidence_coverage_pct": a["a"]["coverage_pct"],
            "candidate": a["a"]["candidate"], "hysteresis": a["hys"], "quality": self.quality(), "not_assessable": a["not_assessable"],
            "alerts": alerts, "indicators": ind, "hypotheses": a["hyps"], "pattern": a["pattern"], "metrics": a["metrics"],
            "graph": graph(wm, a["rule_vals"], L), "trace": a["trace"], "rounds": a["rounds"], "assessment_id": a["aid"],
            "hydro": self.hydro(wm), "mode": self.mode,
            "visual": {"rain_intensity": round(min(100, (a["metrics"].get("rain_mean_30") or 0) / 12), 1), "basis": "30 s mean of raw wetness, visual only"},
        }

    def hydro(self, wm):
        """Which links of the rain -> drain -> low area -> river chain may animate: only when the model supplies the state."""
        def st(f):
            return {"T": "ACTIVE", "F": "INACTIVE", "U": "UNKNOWN", "N": "NOT_INSTALLED"}.get(wm.get(f, "U"), "UNKNOWN")
        return {"edges": [{"from": "RAIN-01", "to": "DRAIN-01", "state": st("rain_wet_confirmed"), "basis": "rain_wet_confirmed"},
                          {"from": "DRAIN-01", "to": "AREA-01", "state": st("drainage_stressed"), "basis": "drainage_stressed"},
                          {"from": "AREA-01", "to": "RIVER-01", "state": st("river_backwater") if wm.get("overflow_confirmed") != T else "ACTIVE", "basis": "river_backwater / overflow"}]}


def _closure(goal, wm):
    """All derived facts that are TRUE and feed the TRUE goal (for reason codes)."""
    out, stack = set(), [goal]
    while stack:
        g = stack.pop()
        if g in out:
            continue
        out.add(g)
        for r in KB.by_head.get(g, []):
            if r.eval(wm) == T:
                stack.extend(f for f, want in r.body if want)
    return out


def graph(wm, rule_vals, leaves):
    """Flat description of the inference network for the dashboard."""
    nodes, edges = [], []
    show = set(KB.facts)
    for f, meta in KB.facts.items():
        lv = leaves.get(f)
        nodes.append({"id": f, "label": meta["label"], "domain": meta["domain"], "kind": meta["kind"], "layer": KB.layer[f], "value": wm.get(f, U),
                      "prov": lv[1] if lv else "DERIVED", "detail": lv[2] if lv else ""})
    for r in KB.rules:
        for b, want in r.body:
            edges.append({"from": b, "to": r.head, "rule": r.id, "want": want, "rule_value": rule_vals.get(r.id, U)})
    return {"nodes": nodes, "edges": edges, "rules": [{"id": r.id, "head": r.head, "text": r.text, "value": rule_vals.get(r.id, U)} for r in KB.rules]}
