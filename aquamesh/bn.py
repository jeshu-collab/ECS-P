"""Small Bayesian network with exact inference by enumeration over the hidden causes.

Hidden:  Rain, SoilSat, Surge, Block, Inund
Evidence nodes are children of hidden nodes.  Source reliability r in [0,1] is applied as
tempered virtual evidence: likelihood = P(obs | parents) ** r  (r=0 -> ignored, r=1 -> full weight).
Probabilities are EXPERT PRIORS for a miniature model - treat as hypotheses to calibrate.
"""
import itertools
import math

HIDDEN = ["Rain", "SoilSat", "Surge", "Block", "Inund"]


def nor(leak, *pairs):
    """Noisy-OR: P(effect) given active causes with strengths w."""
    q = 1.0 - leak
    for active, w in pairs:
        if active:
            q *= 1.0 - w
    return 1.0 - q


def make(open_rd, open_rl, open_dl):
    cpd = {
        "Rain": lambda v: 0.20,
        "SoilSat": lambda v: 0.55 if v["Rain"] else 0.10,
        "Surge": lambda v: 0.60 if v["Rain"] else 0.04,
        "Block": lambda v: 0.08,
        "Inund": lambda v: nor(0.01, (v["Block"] and open_dl, 0.5), (v["Surge"] and open_rl, 0.5),
                               (v["Rain"] and v["SoilSat"], 0.35), (v["Rain"], 0.06)),
    }
    ev = {
        "RainHigh": lambda v: 0.92 if v["Rain"] else 0.06,
        "SoilRising": lambda v: 0.70 if v["Rain"] else 0.12,
        "HxRising": lambda v: 0.70 if v["Rain"] else 0.10,
        "SoilSatObs": lambda v: 0.90 if v["SoilSat"] else 0.05,
        "RiverCoverRising": lambda v: 0.88 if v["Surge"] else 0.06,
        "RiverFlowUp": lambda v: 0.80 if v["Surge"] else 0.08,
        "RiverMotionUp": lambda v: 0.65 if v["Surge"] else 0.15,
        "DrainLevelRising": lambda v: nor(0.03, (v["Rain"], 0.5), (v["Surge"] and open_rd, 0.4), (v["Block"], 0.8)),
        "DrainLevelHigh": lambda v: nor(0.03, (v["Rain"], 0.10), (v["Surge"] and open_rd, 0.10), (v["Block"], 0.8)),
        "DrainFlowFalling": lambda v: 0.90 if v["Block"] else 0.05,
        "DrainFlowRising": lambda v: 0.05 if v["Block"] else (0.80 if v["Rain"] else 0.05),
        "LandWetExpanding": lambda v: 0.85 if v["Inund"] else 0.03,
    }
    return cpd, ev


def infer(cpd, ev, obs):
    """obs: {evidence_node: (bool, reliability)} -> (marginals, joint over hidden)."""
    joint = {}
    for combo in itertools.product((False, True), repeat=len(HIDDEN)):
        v = dict(zip(HIDDEN, combo))
        w = 1.0
        for h in HIDDEN:
            p = cpd[h](v)
            w *= p if v[h] else 1 - p
        for e, (val, r) in obs.items():
            p = ev[e](v)
            w *= max(p if val else 1 - p, 1e-9) ** r
        joint[combo] = w
    z = sum(joint.values())
    joint = {c: w / z for c, w in joint.items()}
    marg = {h: sum(w for c, w in joint.items() if c[i]) for i, h in enumerate(HIDDEN)}
    return marg, joint


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def contributions(cpd, ev, obs, marg):
    """Change in log-odds of each hidden cause when one evidence item is removed (leave-one-out)."""
    out = {}
    for e in obs:
        rest = {k: v for k, v in obs.items() if k != e}
        m2, _ = infer(cpd, ev, rest)
        out[e] = {h: round(logit(marg[h]) - logit(m2[h]), 2) for h in HIDDEN}
    return out


def abduce(joint):
    """Competing explanations for drain accumulation: marginal over (Rain, Block)."""
    i_r, i_b = HIDDEN.index("Rain"), HIDDEN.index("Block")
    tab = {(a, b): 0.0 for a in (False, True) for b in (False, True)}
    for c, w in joint.items():
        tab[(c[i_r], c[i_b])] += w
    names = {(True, False): "rainfall exceeding drain capacity (drain working)",
             (False, True): "obstruction without heavy rain",
             (True, True): "heavy rain AND obstruction",
             (False, False): "neither (noise / other cause)"}
    return sorted(({"label": names[k], "p": round(v, 3)} for k, v in tab.items()), key=lambda x: -x["p"])
