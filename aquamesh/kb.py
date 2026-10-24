"""Knowledge base: facts, production rules, forward chaining, backward chaining (explanation),
temporal ('before') conditions and the terrain / causal graph.

Facts are TRUE-only (closed world) plus an explicit UNKNOWN set (source unavailable), so the
explainer can say "false" vs "cannot tell".  Rules form a DAG: observed -> hypotheses -> risk.
"""

FACT_INFO = {
    # name: (text, source family)
    "rain_high": ("rain intensity is high", "rain sensor"),
    "soil_rising": ("soil moisture is rising", "soil sensor"),
    "soil_saturated": ("soil is near saturation", "soil sensor"),
    "hx_rising": ("catchment load-cell weight is rising", "load cell"),
    "river_cover_rising": ("river water coverage is rising", "camera"),
    "river_flow_surging": ("river surface motion is above its calm baseline", "camera"),
    "river_motion_up": ("float/fin motion (MPU) is above its calm baseline", "MPU"),
    "drain_level_rising": ("drain water level is rising", "camera"),
    "drain_level_high": ("drain is nearly full", "camera"),
    "drain_flow_falling": ("flow through the drain is falling or has stalled", "camera"),
    "drain_flow_rising": ("flow through the drain is increasing", "camera"),
    "land_wet_expanding": ("standing water on land is expanding", "camera"),
    "land_wet_present": ("standing water is present on land", "camera"),
    "edge_river_drain_open": ("terrain model: water can pass river -> drain", "terrain model"),
    "edge_river_land_open": ("terrain model: water can pass river -> land", "terrain model"),
    "edge_drain_land_open": ("terrain model: water can pass drain -> land", "terrain model"),
    "flood_node_offline": ("FLOOD01 node is not reporting", "network"),
    "gas_node_offline": ("GAS01 node is not reporting", "network"),
    "camera_unavailable": ("camera evidence is unavailable", "network"),
    "gateway_down": ("TTGO gateway is not connected", "network"),
    "rain_sensor_untrusted": ("rain reading is not corroborated by other sensors", "health monitor"),
    # derived
    "runoff_potential_high": ("runoff potential is high (rain on wet / saturating soil)", None),
    "runoff_potential_moderate": ("rain is falling but soil is still absorbing", None),
    "river_surge_suspected": ("a river surge is suspected", None),
    "drain_backflow_risk": ("river water may be backing up into the drain", None),
    "drain_blockage_suspected": ("drain obstruction is suspected (level up, flow down)", None),
    "drain_rise_follows_rain": ("drain rise followed rainfall onset (temporal order)", None),
    "drain_loading_from_rain": ("drain is loading normally from rain-driven inflow", None),
    "drain_overload_suspected": ("drain is overloaded by rain-driven inflow", None),
    "compound_event": ("compound event: river surge AND drain blockage", None),
    "land_inundation_risk_rising": ("land inundation risk is rising", None),
    "river_watch": ("RIVER at WATCH", None), "river_warning": ("RIVER at WARNING", None),
    "drain_watch": ("DRAIN at WATCH", None), "drain_warning": ("DRAIN at WARNING", None),
    "drain_critical": ("DRAIN at CRITICAL", None),
    "land_watch": ("LAND at WATCH", None), "land_warning": ("LAND at WARNING", None),
    "land_critical": ("LAND at CRITICAL", None),
}

TOPO_MAP = {"edge_river_drain_open": "river>drain", "edge_river_land_open": "river>land",
            "edge_drain_land_open": "drain>land"}


class Fact:
    __slots__ = ("name", "since", "origin", "detail", "sources", "rule", "used")

    def __init__(self, name, since, origin, detail="", sources=(), rule=None, used=()):
        self.name, self.since, self.origin, self.detail = name, since or 0, origin, detail
        self.sources, self.rule, self.used = set(sources), rule, list(used)


class Rule:
    def __init__(self, id, then, all=(), any=(), none=(), before=(), doc=""):
        self.id, self.then, self.all, self.any, self.none = id, then, list(all), list(any), list(none)
        self.before, self.doc = list(before), doc

    def test(self, wm):
        used = []
        for n in self.all:
            if n not in wm:
                return None
            used.append(n)
        for a, b, gap in self.before:
            if a not in wm or b not in wm:
                return None
            d = wm[b].since - wm[a].since
            if not 0 <= d <= gap:
                return None
            used += [a, b]
        anyu = [n for n in self.any if n in wm]
        if self.any and not anyu:
            return None
        if any(n in wm for n in self.none):
            return None
        since = max([wm[n].since for n in used] + ([min(wm[n].since for n in anyu)] if anyu else [0]))
        seen, out = set(), []
        for n in used + anyu:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out, since

    def missing(self, wm):
        m = [n for n in self.all if n not in wm]
        for a, b, gap in self.before:
            if a not in wm or b not in wm or not 0 <= wm[b].since - wm[a].since <= gap:
                m.append("%s before %s" % (a, b))
        if self.any and not any(n in wm for n in self.any):
            m.append("one of (%s)" % " | ".join(self.any))
        m += ["NOT %s" % n for n in self.none if n in wm]
        return m


RULES = [
    Rule("R01", "runoff_potential_high", all=["rain_high"], any=["soil_saturated", "soil_rising"],
         doc="Rain on soil that is wetting or saturated produces fast runoff."),
    Rule("R02", "runoff_potential_moderate", all=["rain_high"], none=["soil_saturated", "soil_rising"],
         doc="Rain but soil still absorbing: moderate runoff."),
    Rule("R03", "river_surge_suspected", all=["river_cover_rising"],
         any=["river_flow_surging", "river_motion_up", "runoff_potential_high", "rain_high"],
         doc="Rising river coverage corroborated by motion change or rainfall."),
    Rule("R03b", "river_surge_suspected", all=["river_flow_surging"],
         any=["river_motion_up", "rain_high", "runoff_potential_high"],
         doc="Sustained high river motion (level-based, survives a plateau) corroborated by an independent source."),
    Rule("R04", "drain_backflow_risk", all=["river_surge_suspected", "edge_river_drain_open", "drain_level_rising"],
         doc="A surge only loads the drain if river and drain are hydraulically connected."),
    Rule("R05", "drain_blockage_suspected", all=["drain_level_rising", "drain_flow_falling"],
         doc="Hydraulic signature of an obstruction: water accumulates while throughput falls."),
    Rule("R06", "drain_rise_follows_rain", before=[("rain_high", "drain_level_rising", 900)],
         doc="Temporal reasoning: cause (rain onset) precedes effect (drain rise)."),
    Rule("R05b", "drain_blockage_suspected", all=["drain_level_high", "drain_flow_falling"],
         doc="Full drain whose throughput has collapsed (survives the level plateau)."),
    Rule("R07", "drain_loading_from_rain", all=["drain_level_rising", "drain_flow_rising", "drain_rise_follows_rain"],
         doc="Level and throughput both rising after rainfall: normal loading, drain functioning."),
    Rule("R07b", "drain_overload_suspected", all=["drain_loading_from_rain", "drain_level_high"],
         doc="Loading continues until the drain is nearly full: inflow exceeds capacity."),
    Rule("R08", "compound_event", all=["river_surge_suspected", "drain_blockage_suspected"],
         doc="Two independent hazards active together."),
    Rule("R09", "land_inundation_risk_rising", all=["land_wet_expanding"],
         doc="Direct observation of spreading water overrides inference (fast path)."),
    Rule("R10", "land_inundation_risk_rising", all=["edge_drain_land_open", "drain_level_high"],
         any=["drain_blockage_suspected", "drain_overload_suspected", "soil_saturated", "land_wet_present"],
         doc="A full drain spills onto land if the terrain lets it (drain lip above land) and the cause is active or the land cannot absorb."),
    Rule("R11", "land_inundation_risk_rising", all=["river_surge_suspected", "edge_river_land_open", "soil_saturated"],
         doc="River overbank onto saturated land."),
    Rule("S01", "river_watch", any=["river_cover_rising", "river_flow_surging"], doc="Early river change."),
    Rule("S02", "river_warning", all=["river_surge_suspected"], doc="Surge suspected."),
    Rule("S03", "drain_watch", any=["drain_level_rising", "drain_level_high", "drain_loading_from_rain", "drain_backflow_risk"], doc="Drain loading."),
    Rule("S04", "drain_warning", any=["drain_overload_suspected", "drain_blockage_suspected"],
         doc="Drain hazard hypothesis active."),
    Rule("S04b", "drain_warning", all=["drain_backflow_risk", "drain_level_high"], doc="River backflow into a nearly full drain."),
    Rule("S05", "drain_critical", all=["drain_blockage_suspected", "drain_level_high"], doc="Blocked and full."),
    Rule("S06", "drain_critical", all=["drain_overload_suspected", "drain_level_high"], doc="Overloaded and full."),
    Rule("S07", "land_watch", any=["runoff_potential_high", "land_wet_present"], doc="Runoff potential / puddling."),
    Rule("S08", "land_warning", all=["land_inundation_risk_rising"], doc="Inundation risk rising."),
    Rule("S09", "land_critical", all=["land_inundation_risk_rising", "land_wet_expanding"],
         any=["drain_level_high", "river_surge_suspected"], doc="Water spreading with an active source."),
]
BY_THEN = {}
for _r in RULES:
    BY_THEN.setdefault(_r.then, []).append(_r)

LEVELS = ["NORMAL", "WATCH", "WARNING", "CRITICAL"]
ZONE_FACTS = {z: {1: z + "_watch", 2: z + "_warning", 3: z + "_critical"} for z in ("river", "drain", "land")}


def forward(observed):
    """Forward chaining to a fixed point. observed: {name: Fact}. Returns (wm, firings)."""
    wm, firings = dict(observed), []
    changed = True
    while changed:
        changed = False
        for r in RULES:
            if r.then in wm:
                continue
            res = r.test(wm)
            if res:
                used, since = res
                src = set()
                for n in used:
                    src |= wm[n].sources
                wm[r.then] = Fact(r.then, since, "derived", sources=src, rule=r.id, used=used)
                firings.append(dict(rule=r.id, then=r.then, premises=used, doc=r.doc))
                changed = True
    return wm, firings


def explain(goal, wm, unknown, reasons=None, _depth=0):
    """Backward chaining: prove `goal` top-down; return an evidence tree (also for FALSE/UNKNOWN goals)."""
    reasons = reasons or {}
    text = FACT_INFO.get(goal, (goal, None))[0]
    node = dict(name=goal, text=text, children=[], rule=None, detail="", since=None, sources=[], missing=[])
    f = wm.get(goal)
    if f is not None:
        node.update(status="TRUE", detail=f.detail, since=f.since or None, sources=sorted(f.sources))
        if f.origin == "derived":
            sat = [(r, r.test(wm)) for r in BY_THEN.get(goal, [])]
            sat = [(r, t) for r, t in sat if t]          # ALL satisfied rules = all independent support paths
            used = []
            for r, (u, _) in sat:
                used += [n for n in u if n not in used]
            node["rule"] = "+".join(r.id for r, _ in sat) or f.rule
            node["rule_doc"] = " | ".join(r.doc for r, _ in sat)
            node["children"] = [explain(n, wm, unknown, reasons, _depth + 1) for n in used]
        return node
    node["status"] = "UNKNOWN" if goal in unknown else "FALSE"
    if goal in unknown:
        node["detail"] = reasons.get(goal, "source unavailable")
    rules = BY_THEN.get(goal)
    if rules:
        best = min(rules, key=lambda r: len(r.missing(wm)))
        node["rule"], node["rule_doc"] = best.id, best.doc
        node["missing"] = best.missing(wm)
        prem = best.all + [n for n in best.any] + [n for n in best.none if False]
        node["children"] = [explain(n, wm, unknown, reasons, _depth + 1) for n in prem]
    return node


def leaves(tree, out=None):
    out = [] if out is None else out
    if tree["status"] == "TRUE" and not tree["children"] and tree.get("rule") is None:
        out.append(tree)
    for c in tree["children"]:
        leaves(c, out)
    return out


def rule_chain(tree, out=None):
    out = [] if out is None else out
    for c in tree["children"]:
        rule_chain(c, out)
    if tree["status"] == "TRUE" and tree.get("rule") and tree["rule"] not in out:
        out.append(tree["rule"])
    return out


def narrate(tree, caveats=()):
    if tree["status"] == "TRUE":
        ls, seen = leaves(tree), set()
        parts = []
        for l in ls:
            if l["name"] in seen or l["name"].startswith("edge_"):
                continue
            seen.add(l["name"])
            parts.append("%s%s" % (l["text"], " (%s)" % l["detail"] if l["detail"] else ""))
        fam = sorted({s for l in ls for s in l["sources"] if s != "terrain model"})
        txt = "%s because: %s." % (tree["text"][:1].upper() + tree["text"][1:], "; ".join(parts) or "direct observation")
        txt += " Independent evidence families: %s (%d)." % (", ".join(fam) or "none", len(fam))
        if len(fam) <= 1 and ls:
            txt += " Single-source conclusion: lower confidence."
    elif tree["status"] == "UNKNOWN":
        txt = "Cannot tell whether %s: %s." % (tree["text"], tree["detail"] or "source unavailable")
    else:
        txt = "NOT concluded: %s." % tree["text"]
        if tree["missing"]:
            txt += " Closest rule %s still needs: %s." % (tree["rule"], "; ".join(tree["missing"]))
        unk = [c for c in tree["children"] if c["status"] == "UNKNOWN"]
        if unk:
            txt += " Unknown (data unavailable): %s." % ", ".join(c["text"] for c in unk)
    if caveats:
        txt += " Caveats: %s." % "; ".join(caveats)
    return txt


def edges_open(terrain):
    el, tol = terrain["elev"], terrain["tol"]
    out = {}
    for k, v in TOPO_MAP.items():
        a, b = v.split(">")
        out[k] = bool(terrain["connected"].get(v, True)) and el[a] + tol >= el[b]
    return out
