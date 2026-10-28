"""
Abductive diagnosis ("what best explains the evidence?") with likelihood ratios.

Forward chaining answers "what follows from the facts"; this module answers "which explanation is most supported".
Each hypothesis has a prior and, per fact, a likelihood ratio if the fact is TRUE and if it is FALSE.
UNKNOWN / NOT_INSTALLED facts contribute NOTHING (no evidence is not evidence of absence).
Output is *relative support among the listed hypotheses*, NOT a calibrated probability.
"""
import math

# hypothesis: (label, prior, {fact: (LR_if_true, LR_if_false)})
H = {
    "DRY": ("Dry / no rain at the node", 0.55, {
        "rain_wet": (0.05, 3.0), "rain_contact": (0.05, 1.4), "rain_persistent": (0.1, 1.5), "ground_saturated": (0.6, 1.1)}),
    "SPLASH": ("Light rain or splash / dew", 0.15, {
        "rain_wet": (4.0, 0.3), "rain_persistent": (0.4, 2.0), "rain_heavy": (0.3, 1.4), "rain_contact": (0.4, 1.3)}),
    "HEAVY_RAIN": ("Sustained heavy rain", 0.08, {
        "rain_wet": (4.0, 0.05), "rain_persistent": (5.0, 0.2), "rain_heavy": (3.0, 0.4), "rain_contact": (3.0, 0.5),
        "rain_intensifying": (2.0, 0.8), "soil_wet_high": (2.0, 0.7)}),
    "RAIN_SENSOR_FAULT": ("Rain sensor fault", 0.02, {
        "rain_sensor_valid": (0.9, 40.0)}),
    "NODE_OUTAGE": ("Node or radio outage", 0.03, {
        "rain_node_fresh": (0.2, 30.0), "area_node_fresh": (0.3, 12.0), "link_loss_high": (4.0, 0.8), "gateway_uart_ok": (0.9, 20.0)}),
    "DRAIN_BLOCKAGE": ("Blocked drain", 0.02, {
        "drain_level_high": (6.0, 0.3), "drain_flow_low": (8.0, 0.4), "rain_wet": (2.0, 0.7)}),
    "GAS_HAZARD": ("Gas leak / gas hazard", 0.03, {
        "gas_sustained": (30.0, 0.4), "gas_rising": (3.0, 0.8), "gas_sensor_valid": (0.9, 10.0)}),
}


def rank(wm):
    scores, ev = {}, {}
    for k, (label, prior, table) in H.items():
        s, used = math.log(prior), []
        for fact, (lt, lf) in table.items():
            v = wm.get(fact)
            if v == "T":
                s += math.log(lt); used.append((fact, "T", lt))
            elif v == "F":
                s += math.log(lf); used.append((fact, "F", lf))
        scores[k], ev[k] = s, used
    m = max(scores.values())
    z = sum(math.exp(v - m) for v in scores.values())
    out = []
    for k, s in scores.items():
        out.append({"id": k, "label": H[k][0], "support": round(100 * math.exp(s - m) / z, 1),
                    "for": [{"fact": f, "value": v, "lr": lr} for f, v, lr in ev[k] if lr > 1.0],
                    "against": [{"fact": f, "value": v, "lr": lr} for f, v, lr in ev[k] if lr < 1.0],
                    "evidence_used": len(ev[k])})
    out.sort(key=lambda x: -x["support"])
    return out
