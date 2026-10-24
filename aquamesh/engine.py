"""Engine: ties gateway -> health -> features -> rules -> Bayesian fusion -> risk/alerts -> evidence log."""
import math
import threading
import time
from collections import deque

from . import bn, kb
from .evidence import norm
from .features import Features, EVENTS
from .health import Health, CHANNELS, BASE_REL, ABLATABLE
from .protocol import NodeLink, parse_line
from .util import hhmmss

BN_MAP = [("RainHigh", "rain_high", "rain"), ("SoilRising", "soil_rising", "soil"),
          ("SoilSatObs", "soil_saturated", "soil"), ("HxRising", "hx_rising", "hx"),
          ("RiverCoverRising", "river_cover_rising", "cam_river"), ("RiverFlowUp", "river_flow_surging", "cam_river"),
          ("RiverMotionUp", "river_motion_up", "dist"), ("DrainLevelRising", "drain_level_rising", "cam_drain"),
          ("DrainLevelHigh", "drain_level_high", "cam_drain"), ("DrainFlowFalling", "drain_flow_falling", "cam_drain"), ("DrainFlowRising", "drain_flow_rising", "cam_drain"),
          ("LandWetExpanding", "land_wet_expanding", "cam_land")]
EV_SRC = {e: s for _, e, s in BN_MAP}
EV_SRC.update({"land_wet_present": "cam_land", "drain_level_high": "cam_drain"})
ZW = {"river": {"cam_river": .6, "dist": .2, "rain": .2},
      "drain": {"cam_drain": .50, "dist": .20, "rain": .15, "cam_river": .15},
      "land": {"cam_land": .5, "soil": .3, "rain": .2}}
HYP_LABEL = {"Rain": "Heavy rainfall", "SoilSat": "Soil saturated", "Surge": "River surge",
             "Block": "Drain obstruction", "Inund": "Land inundation"}
ZONE_PROB = {"river": ("Surge", "P(surge)"), "drain": ("Block", "P(obstruction)"), "land": ("Inund", "P(inundation)")}
WHY_BN = {"river_surge_suspected": "Surge", "river_warning": "Surge", "river_watch": "Surge",
          "drain_blockage_suspected": "Block", "drain_warning": "Block", "drain_critical": "Block",
          "drain_overload_suspected": "Rain", "land_inundation_risk_rising": "Inund", "land_warning": "Inund",
          "land_critical": "Inund", "land_watch": "Inund", "runoff_potential_high": "Rain"}


class Engine:
    def __init__(self, cfg, clock, ev, vision_get=None, gw_status=None, vision_jpeg=None, mode="LIVE"):
        self.cfg, self.clock, self.ev = cfg, clock, ev
        self.vision_get, self.gw_status, self.vision_jpeg = vision_get, gw_status, vision_jpeg
        self.mode = mode
        self.lock = threading.RLock()
        self.health = Health(cfg["period_s"])
        self.feat = Features(cfg["th"])
        self.reset_state()

    def reset_state(self):
        with self.lock:
            self.links = {}
            self.health.reset()
            self.feat.reset()
            self.ablate = set()
            self.prev_events, self.t0 = {}, None
            self.raw_used, self.raw_n = {}, {}
            self.hist = {}
            self.timeline = deque(maxlen=250)
            self.alerts, self.active_alerts, self.aid = deque(maxlen=200), {}, 0
            self.prev_active, self.prev_level, self.prev_fired = set(), {}, set()
            self.last_feat_log = self.last_dec_log = -1e9
            self.wm, self.unknown, self.reasons, self.snap = {}, set(), {}, {}
            self.last_post = None

    # ------------------------------------------------------------------ ingest
    def ingest_line(self, line, rx=None):
        with self.lock:
            rx = self.clock.now() if rx is None else rx
            p = parse_line(line)
            if not p:
                return
            node = p["node"]
            role = self.cfg["nodes"].get(node)
            link = self.links.setdefault(node, NodeLink(node))
            if role is None:
                self.ev.log("packet_rejected", {"line": p["raw"], "reason": "unknown node"}, rx)
                return
            if not p["crc_ok"]:
                link.crc_bad += 1
                self.ev.log("packet_rejected", {"line": p["raw"], "reason": "crc"}, rx)
                self._raise("crc:" + node, "INFO", "Corrupted packet from %s" % node,
                            "CRC-8 mismatch: packet dropped (accidental corruption or naive tampering).", rx, expire=60)
                return
            v = link.accept(p, rx)
            if v != "ok":
                self.ev.log("packet_rejected", {"line": p["raw"], "reason": v}, rx)
                if v in ("replay", "stale_timestamp"):
                    self._raise("replay:" + node, "WARN", "Replay / stale packet from %s" % node,
                                "Rejected (%s): PKT/UP inconsistent with the live session - possible replay or delayed packet." % v,
                                rx, expire=90)
                return
            self.ev.log("packet", {"line": p["raw"], "rssi": p["rssi"], "snr": p["snr"]}, rx)
            vals = self.health.on_packet(rx, role, p["fields"])
            cal = self.cfg["cal"]
            for ch, val in vals.items():
                if val is None:
                    continue
                if ch in ("rain", "soil"):
                    n = norm(val, cal[ch]["dry"], cal[ch]["wet"])
                    self.feat.feed(rx, ch + "_n", n)
                    self.raw_used[ch], self.raw_n[ch] = val, n
                else:
                    self.feat.feed(rx, ch, val)
                    self.raw_used[ch] = val

    # ------------------------------------------------------------------ alerts
    def _raise(self, key, level, title, detail, t, zone=None, expire=None):
        a = self.active_alerts.get(key)
        if a:
            if a["level"] != level or a["detail"] != detail:
                a.update(level=level, detail=detail)
            return
        self.aid += 1
        a = dict(id=self.aid, key=key, t=t, level=level, title=title, detail=detail, zone=zone, active=True,
                 t_clear=None, expire=(t + expire) if expire else None)
        self.active_alerts[key] = a
        self.alerts.appendleft(a)
        self.ev.log("alert", dict(a, event="raised"), t)
        self.ev.outbox_add(dict(type="alert", **a))

    def _clear(self, key, t):
        a = self.active_alerts.pop(key, None)
        if a:
            a["active"], a["t_clear"] = False, t
            self.ev.log("alert", dict(a, event="cleared"), t)

    # ------------------------------------------------------------------ tick
    def tick(self):
        with self.lock:
            t = self.clock.now()
            if self.t0 is None:
                self.t0 = t
            vis = self.vision_get() if self.vision_get else None
            gw = self.gw_status() if self.gw_status else {"connected": True}
            H = self.health.status(t, self.links, vis, gw, self.ablate, self.prev_events, self.cfg["nodes"])
            avail = {k for k, v in H.items() if isinstance(v, dict) and v.get("rel", 0) > 0}
            if vis and vis.get("status") == "running":
                if "cam_river" in avail:
                    self.feat.feed(t, "river_cover", vis["river"].get("cover"))
                    self.feat.feed(t, "river_flow", vis["river"].get("flow"))
                if "cam_drain" in avail:
                    self.feat.feed(t, "drain_level", vis["drain"].get("level"))
                    self.feat.feed(t, "drain_flow", vis["drain"].get("flow"))
                if "cam_land" in avail:
                    self.feat.feed(t, "land_still", vis["land"].get("still"))
            E = self.feat.evaluate(t, avail)
            self.prev_events = E

            # ---- facts
            obs, unknown, reasons = {}, set(), {}
            for name, e in E.items():
                if e["known"]:
                    if e["active"]:
                        obs[name] = kb.Fact(name, e["since"], "observed", e["value"], [kb.FACT_INFO[name][1]])
                else:
                    unknown.add(name)
                    src = EV_SRC.get(name)
                    r = H.get(src, {}).get("reasons") if src else None
                    reasons[name] = "; ".join(r) if r else "insufficient data (warming up)"
            eo = kb.edges_open(self.cfg["terrain"])
            for k, v in eo.items():
                if v:
                    obs[k] = kb.Fact(k, 0, "observed", "", ["terrain model"])
            grace = (t - self.t0) > 40
            sysf = {"flood_node_offline": H["flood_node"]["state"] == "OFFLINE" or (grace and H["flood_node"]["state"] == "NO_DATA"),
                    "gas_node_offline": H["gas_node"]["state"] == "OFFLINE" or (grace and H["gas_node"]["state"] == "NO_DATA"),
                    "camera_unavailable": all(H["cam_" + z]["rel"] == 0 for z in ("river", "drain", "land")) and grace,
                    "gateway_down": H["gateway"]["state"] == "DOWN",
                    "rain_sensor_untrusted": H.get("rain_uncorroborated", False)}
            for k, v in sysf.items():
                if v:
                    obs[k] = kb.Fact(k, t, "observed", "", ["network" if k != "rain_sensor_untrusted" else "health monitor"])
            wm, firings = kb.forward(obs)
            self.wm, self.unknown, self.reasons = wm, unknown, reasons

            # ---- Bayesian fusion
            cpd, evd = bn.make(eo["edge_river_drain_open"], eo["edge_river_land_open"], eo["edge_drain_land_open"])
            used = [(n, e, s) for n, e, s in BN_MAP if E[e]["known"] and H.get(s, {}).get("rel", 0) > 0]
            if E["drain_level_high"]["active"] and not E["drain_level_rising"]["active"]:
                # saturation censoring: a full drain cannot keep "rising", so absence of a rise is uninformative
                used = [u for u in used if u[0] != "DrainLevelRising"]
            k = {}
            for _, _, s in used:
                k[s] = k.get(s, 0) + 1
            bobs = {}
            for n, e, s in used:
                r = min(1.0, H[s]["rel"] / BASE_REL[s]) / (k[s] ** 0.25)
                bobs[n] = (E[e]["active"], r)
            marg, joint = bn.infer(cpd, evd, bobs)
            contrib = bn.contributions(cpd, evd, bobs, marg) if bobs else {}
            abd = bn.abduce(joint)
            self.last_post = marg

            # ---- zones
            zones = {}
            for z in ("river", "drain", "land"):
                lvl = 0
                for i, nm in kb.ZONE_FACTS[z].items():
                    if nm in wm:
                        lvl = max(lvl, i)
                cov, deg = 0.0, []
                for s, w in ZW[z].items():
                    a = min(1.0, H[s]["rel"] / BASE_REL[s])
                    cov += w * a
                    if a < 0.99:
                        deg.append("%s %s%s" % (s, H[s]["state"].lower(), " (%s)" % H[s]["reasons"][0] if H[s]["reasons"] else ""))
                goal = kb.ZONE_FACTS[z][lvl] if lvl else z + "_warning"
                tree = kb.explain(goal, wm, unknown, reasons)
                fam = {s for l in kb.leaves(tree) for s in l["sources"] if s != "terrain model"} if lvl else set()
                indep = 1.0 if (lvl == 0 or len(fam) >= 2) else 0.75
                conf = round(cov * indep, 2)
                status = "UNKNOWN" if cov < 0.3 else "DEGRADED" if cov < 0.9 else "OK"
                pn, pl = ZONE_PROB[z]
                prob = marg[pn]
                level = kb.LEVELS[lvl] if not (lvl == 0 and status == "UNKNOWN") else "UNKNOWN"
                if lvl:
                    reason = kb.narrate(tree, deg)
                elif status != "OK":
                    reason = "No risk evidence, but coverage is reduced: %s." % "; ".join(deg)
                else:
                    reason = "No active risk evidence."
                agree = ("agree" if (lvl >= 2) == (prob >= 0.5) else "rules say %s, probability says %s" %
                         (kb.LEVELS[lvl], "high" if prob >= 0.5 else "low"))
                zones[z] = dict(level=level, lvl=lvl, status=status, confidence=conf,
                                conf_label="HIGH" if conf >= .75 else "MEDIUM" if conf >= .5 else "LOW",
                                prob=round(prob, 3), prob_label=pl, reason=reason, goal=goal, agree=agree,
                                coverage=round(cov, 2), families=sorted(fam))
            # ---- alerts
            for z, zd in zones.items():
                key = "zone:" + z
                if zd["lvl"] >= 2:
                    self._raise(key, "CRITICAL" if zd["lvl"] == 3 else "WARNING",
                                "%s %s" % (z.upper(), zd["level"]), zd["reason"], t, zone=z)
                    self.active_alerts[key]["level"] = "CRITICAL" if zd["lvl"] == 3 else "WARNING"
                    self.active_alerts[key]["title"] = "%s %s" % (z.upper(), zd["level"])
                    self.active_alerts[key]["detail"] = zd["reason"]
                else:
                    self._clear(key, t)
                if zd["status"] == "UNKNOWN":
                    self._raise("unknown:" + z, "WARN", "%s state UNKNOWN" % z.upper(),
                                "Not enough working evidence to call this zone safe: " + zd["reason"], t, zone=z)
                else:
                    self._clear("unknown:" + z, t)
            conds = {}
            for role in ("flood", "gas"):
                n = H[role + "_node"]
                conds["node:" + role] = (sysf[role + "_node_offline"], "WARN", "%s offline" % n["label"],
                                         "; ".join(n["reasons"]) or "never heard from this node")
            conds["gateway"] = (sysf["gateway_down"], "WARN", "Gateway (TTGO) down", "No LoRa data can reach the Pi.")
            conds["camera"] = (sysf["camera_unavailable"], "WARN", "Camera evidence unavailable", "; ".join(H["cam_river"]["reasons"]))
            for ch in CHANNELS:
                st = H[ch]["state"]
                conds["chan:" + ch] = (st in ("FAULT", "SUSPECT"), "WARN", "%s sensor %s" % (ch.upper(), st.lower()),
                                       "; ".join(H[ch]["reasons"]))
            for key, (c, lv, title, det) in conds.items():
                self._raise(key, lv, title, det, t) if c else self._clear(key, t)
            for key, a in list(self.active_alerts.items()):
                if a.get("expire") and t > a["expire"]:
                    self._clear(key, t)

            # ---- timeline + logs
            active = {n for n in wm if wm[n].origin == "derived" or n in E} - {n for n in wm if n.startswith("edge_")}
            for n in sorted(active - self.prev_active):
                self.timeline.appendleft(dict(t=t, ev="ON", name=n, text=kb.FACT_INFO.get(n, (n,))[0],
                                              rule=wm[n].rule, origin=wm[n].origin))
            for n in sorted(self.prev_active - active):
                self.timeline.appendleft(dict(t=t, ev="OFF", name=n, text=kb.FACT_INFO.get(n, (n,))[0], rule=None, origin=""))
            self.prev_active = active
            fired = {f["then"] for f in firings}
            if fired != self.prev_fired:
                self.ev.log("fire", dict(added=sorted(fired - self.prev_fired), removed=sorted(self.prev_fired - fired),
                                         firings=[dict(rule=f["rule"], then=f["then"], premises=f["premises"]) for f in firings]), t)
                self.prev_fired = fired
            levels = {z: d["level"] for z, d in zones.items()}
            if levels != self.prev_level or t - self.last_dec_log >= 30:
                self.ev.log("decision", dict(zones={z: dict(level=d["level"], conf=d["confidence"], prob=d["prob"], reason=d["reason"][:300])
                                                    for z, d in zones.items()}), t)
                self.prev_level, self.last_dec_log = levels, t
            cal = self.cfg["cal"]
            if t - self.last_feat_log >= self.cfg["period_s"]:
                self.last_feat_log = t
                vv = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.feat.values.items()}
                self.ev.log("feat", dict(rain_raw=self.raw_used.get("rain"), rain_n=self.raw_n.get("rain"),
                                         soil_raw=self.raw_used.get("soil"), soil_n=self.raw_n.get("soil"),
                                         cal={"rain": dict(cal["rain"]), "soil": dict(cal["soil"])}, vals=vv,
                                         events=sorted(n for n, e in E.items() if e["active"]),
                                         post={h: round(p, 3) for h, p in marg.items()}), t)
            self.ev.flush()
            self.ev.maybe_audit(t, self.vision_jpeg, cal)

            # ---- history (1 Hz)
            V = self.feat.values
            row = dict(rain=V.get("rain_n"), soil=V.get("soil_n"), hx=V.get("hx"), dist=V.get("dist"), mq=V.get("mq"),
                       river_cover=V.get("river_cover"), river_flow=V.get("river_flow"), drain_level=V.get("drain_level"),
                       drain_flow=V.get("drain_flow"), land_still=V.get("land_still"),
                       p_rain=marg["Rain"], p_surge=marg["Surge"], p_block=marg["Block"], p_inund=marg["Inund"],
                       sev_river=zones["river"]["lvl"], sev_drain=zones["drain"]["lvl"], sev_land=zones["land"]["lvl"])
            for k2, v in row.items():
                self.hist.setdefault(k2, deque(maxlen=1500)).append((t, v))

            # ---- snapshot
            self.snap = dict(
                t=t, mode=self.mode, speed=self.clock.speed, uptime=t - self.t0, zones=zones,
                hyp=[dict(name=h, label=HYP_LABEL[h], p=round(marg[h], 3)) for h in bn.HIDDEN],
                abduction=abd, contributions=contrib,
                events={n: dict(active=e["active"], known=e["known"], since=e["since"], value=e["value"],
                                reason=reasons.get(n, "")) for n, e in E.items()},
                facts=[dict(name=n, text=kb.FACT_INFO.get(n, (n,))[0], origin=f.origin, since=f.since, rule=f.rule,
                            detail=f.detail, sources=sorted(f.sources)) for n, f in wm.items() if not n.startswith("edge_")],
                firings=[dict(rule=f["rule"], then=f["then"], premises=f["premises"], doc=f["doc"]) for f in firings],
                health={k: v for k, v in H.items() if isinstance(v, dict)},
                links=[l.stats() for l in self.links.values()],
                gateway=gw, alerts=[a for a in list(self.alerts)[:40]],
                timeline=list(self.timeline)[:60],
                terrain=dict(self.cfg["terrain"], open=eo),
                ablate=sorted(self.ablate), cal=cal,
                raw=dict(self.raw_used), vision=vis, evidence=self.ev.stats())

    # ------------------------------------------------------------------ API helpers
    def snapshot(self):
        with self.lock:
            return self.snap

    def history(self, n=900):
        with self.lock:
            return {k: [(round(t, 1), v) for t, v in list(d)[-n:]] for k, d in self.hist.items()}

    def why(self, goal):
        with self.lock:
            if goal not in kb.FACT_INFO:
                return dict(error="unknown goal")
            tree = kb.explain(goal, self.wm, self.unknown, self.reasons)
            caveats = [kb.FACT_INFO[n][0] for n in ("flood_node_offline", "gas_node_offline", "camera_unavailable",
                                                      "gateway_down", "rain_sensor_untrusted") if n in self.wm]
            out = dict(goal=goal, tree=tree, text=kb.narrate(tree, caveats), chain=kb.rule_chain(tree))
            h = WHY_BN.get(goal)
            if h and self.last_post:
                out["bayes"] = dict(node=h, label=HYP_LABEL[h], p=round(self.last_post[h], 3),
                                    contributions={e: c[h] for e, c in self.snap.get("contributions", {}).items() if abs(c[h]) > 0.05})
            return out

    def set_ablation(self, src, off):
        with self.lock:
            if src not in ABLATABLE:
                return False
            (self.ablate.add if off else self.ablate.discard)(src)
            self.ev.log("system", dict(action="ablation", source=src, disabled=bool(off)))
            return True

    def calibrate(self, sensor, point):
        with self.lock:
            if sensor not in ("rain", "soil") or point not in ("dry", "wet") or sensor not in self.raw_used:
                return dict(ok=False, error="no reading yet or bad request")
            old = dict(self.cfg["cal"][sensor])
            self.cfg["cal"][sensor][point] = int(self.raw_used[sensor])
            self.cfg.save()
            self.ev.log("system", dict(action="calibrate", sensor=sensor, point=point, old=old, new=dict(self.cfg["cal"][sensor])))
            return dict(ok=True, cal=self.cfg["cal"][sensor])

    def set_terrain(self, elev=None, connected=None):
        with self.lock:
            tr = self.cfg["terrain"]
            before = dict(elev=dict(tr["elev"]), connected=dict(tr["connected"]))
            for k, v in (elev or {}).items():
                if k in tr["elev"]:
                    tr["elev"][k] = float(v)
            for k, v in (connected or {}).items():
                if k in tr["connected"]:
                    tr["connected"][k] = bool(v)
            self.cfg.save()
            self.ev.log("terrain", dict(before=before, after=dict(elev=dict(tr["elev"]), connected=dict(tr["connected"]))))
            return dict(tr, open=kb.edges_open(tr))
