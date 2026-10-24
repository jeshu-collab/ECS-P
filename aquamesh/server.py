"""HTTP server: dashboard + JSON API (stdlib only)."""
import csv
import io
import json
import mimetypes
import os
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

from .sim import SCENARIOS

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class Ctx:
    engine = ev = cfg = clock = sim = vision = gateway = replay = None
    token = os.environ.get("AQUAMESH_TOKEN", "")


def make_handler(ctx):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, default=str).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Token")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            e = ctx.engine
            try:
                if u.path in ("/", "/index.html"):
                    self._send(open(os.path.join(STATIC, "index.html"), "rb").read(), "text/html; charset=utf-8")
                elif u.path == "/api/snapshot":
                    self._send(e.snapshot())
                elif u.path == "/api/history":
                    self._send(e.history(int(q.get("n", ["900"])[0])))
                elif u.path == "/api/why":
                    self._send(e.why(q.get("goal", [""])[0]))
                elif u.path == "/api/evidence":
                    kinds = q.get("kind", [None])[0]
                    self._send(ctx.ev.recent(int(q.get("n", ["40"])[0]), kinds.split(",") if kinds else None))
                elif u.path == "/api/verify":
                    self._send(ctx.ev.verify())
                elif u.path == "/api/tamper_demo":
                    self._send(ctx.ev.tamper_demo())
                elif u.path == "/api/audit":
                    self._send(ctx.ev.audit(ctx.clock.now(), ctx.vision.jpeg if ctx.vision else None) or {"error": "no data yet"})
                elif u.path == "/api/scenarios":
                    self._send(dict(mode=e.mode, speed=ctx.clock.speed, current=ctx.sim.name if ctx.sim else None,
                                    scenarios=[dict(id=k, title=v["title"], expect=v["expect"]) for k, v in SCENARIOS.items()] if ctx.sim else []))
                elif u.path == "/api/terrain":
                    self._send(e.snapshot().get("terrain", {}))
                elif u.path == "/api/regions" and ctx.vision:
                    self._send(ctx.vision.regions())
                elif u.path == "/snapshot.jpg" and ctx.vision and ctx.vision.jpeg():
                    self._send(ctx.vision.jpeg(), "image/jpeg")
                elif u.path == "/stream.mjpg" and ctx.vision:
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
                    self.end_headers()
                    last = -1
                    while True:
                        ctx.vision.wait_frame()
                        b, seq = ctx.vision.overlay()
                        if b is None or seq == last:
                            continue
                        last = seq
                        self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(b) + b + b"\r\n")
                elif u.path.startswith(("/css/", "/js/", "/assets/")):
                    # Serve dashboard assets from the package's static directory.
                    # Resolve and validate the path to prevent traversal outside STATIC.
                    rel = unquote(u.path.lstrip("/"))
                    target = os.path.realpath(os.path.join(STATIC, rel))
                    if not target.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(target):
                        self._send({"error": "not found", "path": u.path}, code=404)
                    else:
                        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
                        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                            ctype += "; charset=utf-8"
                        self._send(open(target, "rb").read(), ctype)
                elif u.path == "/api/export.csv":
                    h = e.history(1500)
                    keys = sorted(h)
                    ts = sorted({t for k in keys for t, _ in h[k]})
                    idx = {k: dict(h[k]) for k in keys}
                    out = io.StringIO()
                    w = csv.writer(out)
                    w.writerow(["t"] + keys)
                    for t in ts:
                        w.writerow([t] + [idx[k].get(t, "") for k in keys])
                    self._send(out.getvalue(), "text/csv")
                else:
                    self._send({"error": "not found"}, code=404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as ex:
                self._send({"error": str(ex)[:200]}, code=500)

        def do_POST(self):
            u = urlparse(self.path)
            e = ctx.engine
            if ctx.token and self.headers.get("X-Token", "") != ctx.token:
                return self._send({"error": "token required"}, code=401)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if u.path == "/api/ablate":
                    self._send(dict(ok=e.set_ablation(body.get("source"), bool(body.get("off")))))
                elif u.path == "/api/calibrate":
                    self._send(e.calibrate(body.get("sensor"), body.get("point")))
                elif u.path == "/api/terrain":
                    self._send(e.set_terrain(body.get("elev"), body.get("connected")))
                elif u.path == "/api/scenario" and ctx.sim:
                    if body.get("speed"):
                        ctx.clock.set_speed(max(0.5, min(30.0, float(body["speed"]))))
                    if body.get("name"):
                        e.reset_state()
                        ctx.sim.set_scenario(body["name"])
                        ctx.ev.log("system", dict(action="scenario", name=body["name"], speed=ctx.clock.speed))
                    self._send(dict(ok=True, current=ctx.sim.name, speed=ctx.clock.speed))
                elif u.path == "/api/note":
                    ctx.ev.log("note", dict(text=str(body.get("text", ""))[:500]))
                    self._send(dict(ok=True))
                elif u.path == "/api/regions" and ctx.vision:
                    self._send(dict(ok=True, regions=ctx.vision.set_regions(body)))
                elif u.path == "/api/learn" and ctx.vision:
                    ctx.vision.learn()
                    self._send(dict(ok=True))
                elif u.path == "/api/suggest" and ctx.vision:
                    self._send(ctx.vision.suggest() or {"error": "no frame"})
                else:
                    self._send({"error": "not found"}, code=404)
            except Exception as ex:
                self._send({"error": str(ex)[:200]}, code=400)

    return H


def serve(ctx, port):
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(ctx))
    srv.daemon_threads = True
    return srv
