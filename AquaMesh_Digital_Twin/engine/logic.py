"""
AquaMesh logic core: a small production-rule engine with FOUR truth values.

    T  true            F  false
    U  unknown now     (sensor stale / offline / needs calibration)  -> NEVER treated as "safe"
    N  not applicable  (sensor is not installed in this deployment)  -> rule is ignored, but reported

Why four values?  A classic rule engine treats "no data" as false, so a dead river sensor would read
as "river is fine".  Here a missing reading propagates as U (or N) through the rules, so the system
can say "cannot assess" instead of "normal".

Two reasoning directions share ONE rule base:

  forward_chain()  data-driven   : leaf facts -> repeatedly fire rules -> every derived fact + firing trace
  prove()          goal-driven   : "why is flood_warning true / not true?" -> AND/OR proof tree,
                                   winning rule, refuting facts, and the exact missing evidence
  counterfactual() re-run forward chaining with some facts overridden, then diff the conclusions
"""
from collections import OrderedDict

T, F, U, N = "T", "F", "U", "N"
NAMES = {T: "TRUE", F: "FALSE", U: "UNKNOWN", N: "NOT_INSTALLED"}


def neg(v):
    return {T: F, F: T}.get(v, v)          # U and N stay as they are


def k_and(vals):
    vals = list(vals)
    if N in vals:
        return N                            # a premise that cannot exist makes the rule inapplicable
    if F in vals:
        return F                            # one definite FALSE refutes the rule even if others are unknown
    if U in vals:
        return U
    return T


def k_or(vals):
    vals = [v for v in vals if v != N]      # inapplicable rules are ignored
    if not vals:
        return N
    if T in vals:
        return T
    if U in vals:
        return U
    return F


class Rule:
    def __init__(self, rid, head, body, text, level=None):
        self.id, self.head, self.text, self.level = rid, head, text, level
        # body: list of (fact, wanted_truth) ; "~x" means x must be FALSE
        self.body = [(b[1:], False) if b.startswith("~") else (b, True) for b in body]

    def eval(self, wm):
        lits = []
        for fact, want in self.body:
            v = wm.get(fact, U)
            if want:
                lits.append(v)
            else:       # "x must be false": a condition that cannot exist (N) is vacuously not true; it is reported separately as not_assessable
                lits.append(T if v == N else neg(v))
        return k_and(lits)


class KnowledgeBase:
    def __init__(self, version, facts, rules):
        self.version = version
        self.facts = facts                                  # name -> dict(label, domain, kind, ...)
        self.rules = rules
        self.by_head = OrderedDict()
        for r in rules:
            self.by_head.setdefault(r.head, []).append(r)
        self.derived = list(self.by_head.keys())
        self.layer = {}
        for f in facts:
            self._layer(f, set())

    def _layer(self, f, stack):
        if f in self.layer:
            return self.layer[f]
        if f in stack:
            raise ValueError("cyclic rule base at " + f)
        if f not in self.by_head:
            self.layer[f] = 0
            return 0
        d = 1 + max(self._layer(b, stack | {f}) for r in self.by_head[f] for b, _ in r.body)
        self.layer[f] = d
        return d


def forward_chain(kb, leaves, overrides=None):
    """leaves: {fact: (value, provenance, detail)}.  Returns (wm, rule_values, trace, rounds)."""
    wm = {k: v[0] for k, v in leaves.items()}
    for k, v in (overrides or {}).items():
        wm[k] = v
    for h in kb.derived:
        wm[h] = U
    rule_vals, trace = {}, []
    rounds, changed = 0, True
    while changed and rounds < 25:
        changed, rounds = False, rounds + 1
        for head in kb.derived:
            if overrides and head in overrides:
                continue
            vals = []
            for r in kb.by_head[head]:
                rv = r.eval(wm)
                if rule_vals.get(r.id) != rv:
                    rule_vals[r.id] = rv
                vals.append(rv)
            nv = k_or(vals)
            if nv != wm[head]:
                prev, wm[head], changed = wm[head], nv, True
                trace.append({"round": rounds, "fact": head, "from": prev, "to": nv,
                              "by": [r.id for r in kb.by_head[head] if rule_vals[r.id] == nv] or
                                    [r.id for r in kb.by_head[head]]})
    for r in kb.rules:                                       # make sure every rule has a final value
        rule_vals[r.id] = r.eval(wm)
    return wm, rule_vals, trace, rounds


def prove(kb, wm, leaves, goal, _depth=0, _seen=None):
    """Backward chaining.  Builds the AND/OR proof tree for `goal` from the settled working memory."""
    val = wm.get(goal, U)
    meta = kb.facts.get(goal, {})
    node = {"fact": goal, "label": meta.get("label", goal), "value": val}
    if goal not in kb.by_head:
        lv = leaves.get(goal, (val, "UNAVAILABLE", ""))
        node.update(kind="evidence", provenance=lv[1], detail=lv[2])
        return node
    node["kind"] = "derived"
    node["via"] = []
    for r in kb.by_head[goal]:
        prem = []
        for f, want in r.body:
            child = prove(kb, wm, leaves, f, _depth + 1)
            child["wanted"] = want
            prem.append(child)
        node["via"].append({"rule": r.id, "text": r.text, "value": r.eval(wm), "premises": prem})
    return node


def explain(kb, wm, leaves, goal):
    """Plain-language answer to 'why is <goal> (not) true?' produced by backward chaining."""
    tree = prove(kb, wm, leaves, goal)
    lines, missing, refuted = [], [], []

    def lab(n):
        return n["label"]

    def walk(n, ind=0):
        pad = "  " * ind
        if n["kind"] == "evidence":
            tag = "" if n["value"] in (T, F) else "  <- " + NAMES[n["value"]] + (f" ({n['detail']})" if n.get("detail") else "")
            if n["value"] == U:
                missing.append(n["fact"])
            lines.append(f"{pad}- {lab(n)} = {NAMES[n['value']]} [{n['provenance']}]{tag}")
            return
        win = [v for v in n["via"] if v["value"] == n["value"]]
        lines.append(f"{pad}- {lab(n)} = {NAMES[n['value']]}")
        if n["value"] == T:
            v = win[0]
            lines.append(f"{pad}  because {v['rule']}: {v['text']}")
            for p in v["premises"]:
                walk(p, ind + 2)
        elif n["value"] == F:
            for v in n["via"]:
                bad = [p for p in v["premises"] if (p["value"] == F and p["wanted"]) or (p["value"] == T and not p["wanted"])]
                if bad:
                    lines.append(f"{pad}  {v['rule']} blocked: {lab(bad[0])} is {NAMES[bad[0]['value']]}")
                    refuted.append(bad[0]["fact"])
        elif n["value"] == U:
            for v in n["via"]:
                if v["value"] in (U,):
                    lines.append(f"{pad}  {v['rule']} cannot be decided:")
                    for p in v["premises"]:
                        if p["value"] == U:
                            walk(p, ind + 2)
        else:
            lines.append(f"{pad}  not applicable: needs a sensor that is not installed")

    walk(tree)
    return {"goal": goal, "value": wm.get(goal, U), "tree": tree, "text": lines, "missing_evidence": sorted(set(missing)),
            "refuting_facts": sorted(set(refuted))}


def supporting_leaves(kb, wm, goal):
    """Leaf facts that actually make `goal` TRUE (only the winning rules are followed)."""
    out, stack = [], [goal]
    seen = set()
    while stack:
        g = stack.pop()
        if g in seen:
            continue
        seen.add(g)
        if g not in kb.by_head:
            out.append(g)
            continue
        for r in kb.by_head[g]:
            if r.eval(wm) == T:
                stack.extend(f for f, want in r.body if want)
    return out


def counterfactual(kb, leaves, overrides, watch):
    """Same recorded leaf facts, different assumptions.  `watch` = derived facts to compare."""
    base, _, _, _ = forward_chain(kb, leaves)
    alt, _, trace, _ = forward_chain(kb, leaves, overrides)
    diff = [{"fact": f, "label": kb.facts.get(f, {}).get("label", f), "before": base[f], "after": alt[f]}
            for f in watch if base.get(f) != alt.get(f)]
    return {"overrides": overrides, "changed": diff, "before": {f: base[f] for f in watch},
            "after": {f: alt[f] for f in watch}, "trace": trace}
