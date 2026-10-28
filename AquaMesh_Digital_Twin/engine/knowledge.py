"""
The AquaMesh knowledge base.  Edit RULES to change how the system reasons - nothing else needs to change.

Rule syntax:  Rule(id, head, [premises], "text")      "~x" = premise x must be FALSE.
Several rules with the same head are alternatives (OR).  Premises inside one rule are AND.

Layers (computed automatically):  0 = evidence from sensors/links  ->  higher = more abstract conclusions.
"""
from .logic import KnowledgeBase, Rule

RULE_VERSION = "kb-1.0"

# name: (label, domain, short description)         domain: link | rain | soil | gas | hydro | flood | health
_F = [
    # ---- evidence (leaf) facts, produced by features.observe() ----
    ("rain_node_fresh",     "FLOOD01 reporting",           "link",  "Rain node packet newer than the freshness limit"),
    ("area_node_fresh",     "GAS01 reporting",             "link",  "Soil/gas node packet newer than the freshness limit"),
    ("link_loss_high",      "Packet loss high",            "link",  "Lost packets in the recent window above limit"),
    ("gateway_uart_ok",     "Gateway UART open",           "link",  "Pi can read the TTGO receiver"),
    ("rain_sensor_valid",   "Rain sensor plausible",       "rain",  "In range, changing, not stuck"),
    ("rain_wet",            "Surface wet (60 s)",          "rain",  "Wet fraction of last 60 s above limit"),
    ("rain_persistent",     "Wet persists (3 min)",        "rain",  "Wet fraction of last 3 min above limit"),
    ("rain_intensifying",   "Wetness rising",              "rain",  "Last 30 s clearly wetter than the 30 s before"),
    ("rain_heavy",          "Heavy wetness peak",          "rain",  "30 s peak above heavy threshold"),
    ("rain_contact",        "Node rain alert",             "rain",  "FLOOD01 reports STATUS=ALERT (raw above node threshold)"),
    ("soil_sensor_valid",   "Soil sensor plausible",       "soil",  "In range, changing, not stuck"),
    ("soil_wet_high",       "Soil saturated",              "soil",  "Calibrated soil moisture above limit"),
    ("gas_sensor_valid",    "Gas sensor plausible",        "gas",   "In range, not stuck"),
    ("gas_sustained",       "Gas over threshold (sustained)", "gas","MQ above alert threshold in most recent packets"),
    ("gas_rising",          "Gas rising fast",             "gas",   "Positive slope over last minute"),
    ("drain_level_high",    "Drain level high",            "hydro", "Drain water level above alert level"),
    ("drain_flow_low",      "Drain flow low",              "hydro", "Flow below expected for current rain"),
    ("river_level_high",    "River level high",            "hydro", "River level above alert level"),
    ("overflow_contact",    "Overflow contact wet",        "hydro", "Surface-water contact switch closed"),
]

_R = [
    # ---- rain interpretation ----
    Rule("R01", "rain_wet_confirmed",   ["rain_node_fresh", "rain_sensor_valid", "rain_wet"], "fresh + plausible rain node shows wet surface"),
    Rule("R02", "rain_sustained",       ["rain_wet_confirmed", "rain_persistent"],            "wetness confirmed and lasting minutes, not a splash"),
    Rule("R03", "rain_intensifying_confirmed", ["rain_wet_confirmed", "rain_intensifying"],   "wetness confirmed and increasing"),
    Rule("R04", "rain_heavy_confirmed", ["rain_wet_confirmed", "rain_heavy"],                 "wetness confirmed and peak is heavy"),
    Rule("R05", "rain_contact_confirmed", ["rain_node_fresh", "rain_sensor_valid", "rain_contact"], "fresh plausible node alert"),
    # ---- ground / drainage / river ----
    Rule("R06", "ground_saturated",     ["area_node_fresh", "soil_sensor_valid", "soil_wet_high"], "fresh plausible soil probe is saturated"),
    Rule("R07", "drainage_stressed",    ["drain_level_high"],                                 "drain water level above alert level"),
    Rule("R08", "drain_blockage_suspected", ["drain_level_high", "drain_flow_low"],           "drain is full yet flow is low"),
    Rule("R09", "river_backwater",      ["river_level_high", "drain_level_high"],             "river high AND drain high: outflow is blocked from below"),
    Rule("R10", "overflow_confirmed",   ["area_node_fresh", "overflow_contact"],              "fresh node reports water on the surface"),
    # ---- flood severity ladder ----
    Rule("R11", "flood_assessable",     ["rain_node_fresh", "rain_sensor_valid"],             "at least the rain node is usable"),
    Rule("R12", "flood_watch",          ["rain_intensifying_confirmed"],                      "wetness is rising"),
    Rule("R13", "flood_watch",          ["rain_sustained"],                                   "rain has persisted"),
    Rule("R14", "flood_watch",          ["rain_contact_confirmed"],                           "rain node is in alert"),
    Rule("R15", "flood_watch",          ["drainage_stressed"],                                "drain is filling"),
    Rule("R16", "flood_warning",        ["rain_sustained", "ground_saturated"],               "lasting rain on saturated ground"),
    Rule("R17", "flood_warning",        ["rain_sustained", "drainage_stressed"],              "lasting rain while drain is stressed"),
    Rule("R18", "flood_warning",        ["rain_contact_confirmed", "rain_sustained"],         "node alert together with lasting rain"),
    Rule("R19", "flood_warning",        ["rain_intensifying_confirmed", "ground_saturated"],  "rising rain on saturated ground"),
    Rule("R20", "flood_warning",        ["drain_blockage_suspected", "rain_wet_confirmed"],   "suspected blockage while it rains"),
    Rule("R21", "flood_critical",       ["overflow_confirmed"],                               "water is already on the surface"),
    Rule("R22", "flood_critical",       ["river_backwater", "rain_sustained"],                "river backwater during lasting rain"),
    Rule("R23", "flood_critical",       ["rain_heavy_confirmed", "rain_sustained", "ground_saturated", "drainage_stressed"],
                                        "heavy lasting rain, saturated ground and stressed drain together"),
    Rule("R24", "flood_normal",         ["flood_assessable", "~flood_watch", "~flood_warning", "~flood_critical"],
                                        "usable sensors and no warning condition holds"),
    # ---- gas hazard (kept separate from flooding) ----
    Rule("R30", "gas_hazard",           ["area_node_fresh", "gas_sensor_valid", "gas_sustained"], "sustained gas reading above threshold"),
    Rule("R31", "gas_watch",            ["area_node_fresh", "gas_sensor_valid", "gas_rising"],    "gas reading climbing quickly"),
    Rule("R32", "gas_normal",           ["area_node_fresh", "gas_sensor_valid", "~gas_hazard", "~gas_watch"], "gas sensor usable and calm"),
    # ---- system health (kept separate from flood severity) ----
    Rule("R40", "rain_sensor_fault",    ["rain_node_fresh", "~rain_sensor_valid"],            "node is talking but the rain reading is implausible"),
    Rule("R41", "soil_sensor_fault",    ["area_node_fresh", "~soil_sensor_valid"],            "node is talking but the soil reading is implausible"),
    Rule("R42", "gas_sensor_fault",     ["area_node_fresh", "~gas_sensor_valid"],             "node is talking but the gas reading is implausible"),
    Rule("R43", "sensor_fault",         ["rain_sensor_fault"],                                "rain sensor fault"),
    Rule("R44", "sensor_fault",         ["soil_sensor_fault"],                                "soil sensor fault"),
    Rule("R45", "sensor_fault",         ["gas_sensor_fault"],                                 "gas sensor fault"),
    Rule("R46", "node_outage",          ["~rain_node_fresh"],                                 "FLOOD01 silent"),
    Rule("R47", "node_outage",          ["~area_node_fresh"],                                 "GAS01 silent"),
    Rule("R48", "comms_degraded",       ["link_loss_high"],                                   "too many packets lost"),
    Rule("R49", "comms_degraded",       ["~gateway_uart_ok"],                                 "Pi cannot read the receiver"),
    Rule("R50", "system_degraded",      ["node_outage"],                                      "a node is out"),
    Rule("R51", "system_degraded",      ["comms_degraded"],                                   "communication is degraded"),
]

_DERIVED_LABELS = {
    "rain_wet_confirmed": "Rain confirmed", "rain_sustained": "Rain sustained", "rain_intensifying_confirmed": "Rain intensifying",
    "rain_heavy_confirmed": "Heavy rain confirmed", "rain_contact_confirmed": "Node alert confirmed",
    "ground_saturated": "Ground saturated", "drainage_stressed": "Drainage stressed",
    "drain_blockage_suspected": "Drain blockage suspected", "river_backwater": "River backwater",
    "overflow_confirmed": "Surface overflow", "flood_assessable": "Flood risk assessable", "flood_watch": "FLOOD WATCH",
    "flood_warning": "FLOOD WARNING", "flood_critical": "FLOOD CRITICAL", "flood_normal": "Flood normal",
    "gas_hazard": "GAS HAZARD", "gas_watch": "Gas watch", "gas_normal": "Gas normal",
    "rain_sensor_fault": "Rain sensor fault", "soil_sensor_fault": "Soil sensor fault", "gas_sensor_fault": "Gas sensor fault",
    "sensor_fault": "SENSOR FAULT", "node_outage": "Node outage", "comms_degraded": "Comms degraded", "system_degraded": "SYSTEM DEGRADED",
}
_DOMAIN = {"flood": "flood", "gas": "gas", "rain": "rain", "ground": "soil", "drain": "hydro", "river": "hydro", "overflow": "hydro",
           "sensor": "health", "soil": "soil", "node": "health", "comms": "health", "system": "health"}

# Hydrological capability that is lost when a sensor is NOT installed (reported, never faked)
CAPABILITY = {"drain_blockage_suspected": "Drain blockage detection", "river_backwater": "River backwater detection",
              "overflow_confirmed": "Surface overflow detection", "drainage_stressed": "Drain level monitoring"}

facts = {n: {"label": l, "domain": d, "desc": t, "kind": "evidence"} for n, l, d, t in _F}
for r in _R:
    if r.head not in facts:
        prefix = r.head.split("_")[0]
        facts[r.head] = {"label": _DERIVED_LABELS.get(r.head, r.head), "domain": _DOMAIN.get(prefix, "flood"), "desc": "", "kind": "derived"}

KB = KnowledgeBase(RULE_VERSION, facts, _R)
