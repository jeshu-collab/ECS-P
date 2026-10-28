#!/usr/bin/env python3
"""
AquaMesh Raspberry Pi gateway.  This file only does I/O; all reasoning lives in the engine/ package.

  ESP32 FLOOD01 ─┐  LoRa 433 MHz
  ESP32 GAS01  ──┴──> TTGO receiver ──USB serial──> this script ──> engine.Twin (validate, reason, record)
                                                       │
                          buzzer GPIO 17  <────────────┤  LOCAL: works with no browser and no Internet
                          http://<pi-ip>:8000 <────────┘  dashboard (public/) + JSON API

Run:      python3 aquamesh_pi.py                 (real hardware)
"""
import argparse, base64, csv, json, os, shutil, sys, threading, time, uuid
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = list_ports = None

from engine import calibration as C
from engine.packet import parse_line
from engine.pipeline import Twin

# ───────────────────────── CONFIG ─────────────────────────
BAUD = 115200
BUZZER_GPIO = 17            # active-LOW 3-pin buzzer (unchanged from your original script)
BUZZER_SECONDS = 1.5
CRITICAL_REPEAT_S = 10      # while the engine says CRITICAL (or gas hazard) the buzzer repeats
SERIAL_CANDIDATES = ["/dev/ttyUSB0", "/dev/ttyACM0", "/dev/ttyUSB1", "/dev/ttyACM1", "/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0"]
# ──────────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.abspath(__file__))
# Load local gateway secrets before reading Gemini configuration. Existing
# process environment variables take precedence over values in the .env file.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass  # Environment variables can still be supplied by the service/OS.
PUBLIC = os.path.join(ROOT, "public")          # ONLY this folder is served; source, config and logs are never exposed
LOG_DIR = os.path.join(ROOT, "logs")
CFG = os.path.join(ROOT, "config", "calibration.json")
GEMINI_MODEL = os.environ.get("AQUAMESH_GEMINI_MODEL", "gemini-3.8-flash")
MAX_CAMERA_FRAME_BYTES = 4 * 1024 * 1024
GEMINI_COOLDOWN_S = 10
INSPECTION_LOCK = threading.Lock()
LATEST_CAMERA = None
LATEST_CAMERA_FRAME = None
LATEST_CAMERA_RECEIVED_AT = 0.0
LATEST_AI_ANALYSIS = None
LAST_GEMINI_REQUEST = 0.0

DRAIN_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["Clear", "Partial Blockage", "Severe Blockage", "Uncertain"]},
        "confidence": {"type": "number"},
        "action_required": {"type": "boolean"},
        "reasoning": {"type": "string"},
        "recommended_action": {"type": "string"},
    },
    "required": ["status", "confidence", "action_required", "reasoning", "recommended_action"],
}


def gemini_configured():
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def publish_camera_frame(image_bytes):
    """Save and publish a JPEG sent by the Raspberry Pi camera capture process."""
    global LATEST_CAMERA, LATEST_CAMERA_FRAME, LATEST_CAMERA_RECEIVED_AT
    if not image_bytes or len(image_bytes) > MAX_CAMERA_FRAME_BYTES or not image_bytes.startswith(b"\xff\xd8\xff"):
        raise ValueError("Expected a valid JPEG frame no larger than 4 MB")
    capture_id = str(uuid.uuid4())
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    capture_dir = os.path.join(PUBLIC, "captures")
    os.makedirs(capture_dir, exist_ok=True)
    image_path = os.path.join(capture_dir, "latest.jpg")
    temp_path = image_path + f".{capture_id}.tmp"
    with open(temp_path, "wb") as image_file:
        image_file.write(image_bytes)
    os.replace(temp_path, image_path)
    camera = {"online": True, "image_url": f"/captures/latest.jpg?capture={capture_id}",
              "capture_timestamp": captured_at, "event_id": capture_id,
              "trigger_reason": "Latest frame uploaded by Raspberry Pi camera"}
    with INSPECTION_LOCK:
        LATEST_CAMERA = camera
        LATEST_CAMERA_FRAME = bytes(image_bytes)
        LATEST_CAMERA_RECEIVED_AT = time.monotonic()
    return camera


def analyze_drain_frame(image_bytes, mime_type="image/jpeg"):
    """Inspect one user-captured frame. The key is read only from the gateway environment."""
    if not gemini_configured():
        raise RuntimeError("Set GEMINI_API_KEY in the gateway environment")
    if mime_type != "image/jpeg" or not image_bytes.startswith(b"\xff\xd8\xff"):
        raise ValueError("Expected a valid JPEG camera frame")
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Gemini SDK missing; install google-genai") from exc

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    interaction = client.interactions.create(
        model=GEMINI_MODEL,
        input=[
            {
                "type": "text",
                "text": (
                    "You are viewing a small-scale physical demonstration model of an urban "
                    "drainage and flood system, not a full-size real-world scene. Treat the "
                    "objects, water, and debris as miniature model features. Inspect the model's "
                    "drainage inlet and classify only its visible blockage as Clear, Partial "
                    "Blockage, Severe Blockage, or Uncertain. In your reasoning, distinguish "
                    "visible blockage from any visible pooled or overflowing water. Give confidence "
                    "from 0.0 to 1.0, whether a person should inspect the model, concise evidence-based "
                    "reasoning, and a practical recommendation. Do not infer future flooding, real-world "
                    "flood risk, or actual water depth from a still image. If the view is unclear, "
                    "classify it as Uncertain. Treat this only as visual support; a human must make "
                    "safety decisions."
                ),
            },
            {"type": "image", "data": base64.b64encode(image_bytes).decode("ascii"), "mime_type": mime_type},
        ],
        response_format={"type": "text", "mime_type": "application/json", "schema": DRAIN_ANALYSIS_SCHEMA},
    )
    analysis = json.loads(interaction.output_text)
    confidence = float(analysis["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("Gemini returned confidence outside the 0.0–1.0 range")
    if analysis["status"] not in {"Clear", "Partial Blockage", "Severe Blockage", "Uncertain"}:
        raise ValueError("Gemini returned an unsupported inspection status")
    analysis["confidence"] = confidence
    analysis["event_id"] = str(uuid.uuid4())
    analysis["timestamp"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return analysis


def gemini_error_summary(exc):
    """Return actionable Gemini failure text without exposing request credentials."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None) or getattr(exc, "status_code", None) or getattr(exc, "code", None)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    if code in (401, 403):
        return f"Gemini rejected the API key or project access (HTTP {code}). Check the key and API permissions."
    if code == 429:
        return "Gemini rate limit or quota reached (HTTP 429). Wait, then check the Gemini API quota for this project."
    if code == 400:
        return "Gemini rejected the request (HTTP 400). Check the configured model and response schema."
    if code is not None and code >= 500:
        return f"Gemini is temporarily unavailable (HTTP {code}). Try again shortly."
    error_name = type(exc).__name__.lower()
    if "timeout" in error_name:
        return "The request to Gemini timed out. Check the gateway's internet connection, then retry."
    if "connect" in error_name or "network" in error_name:
        return "The gateway could not connect to Gemini. Check its internet connection, then retry."
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return "Gemini returned a result the gateway could not read. Check the model response and gateway version."
    return f"Gemini request failed ({type(exc).__name__}). Check the gateway console for the detailed error."


class Buzzer:
    """Non-blocking, local-only buzzer."""
    def __init__(self, pin):
        self.available, self.dev, self.on_until, self._t = False, None, 0.0, None
        try:
            from gpiozero import OutputDevice
            self.dev = OutputDevice(pin, active_high=False, initial_value=False)
            self.available = True
        except Exception as e:
            print(f"[buzzer] GPIO unavailable ({e}); running without buzzer")

    def pulse(self, seconds=BUZZER_SECONDS):
        self.on_until = max(self.on_until, time.time() + seconds)
        if self._t and self._t.is_alive():
            return
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        if self.dev: self.dev.on()
        while time.time() < self.on_until:
            time.sleep(0.05)
        if self.dev: self.dev.off()

    @property
    def is_on(self): return time.time() < self.on_until

    def close(self):
        if self.dev: self.dev.off()


class Gateway:
    def __init__(self, twin, buzzer):
        self.twin, self.buzzer = twin, buzzer
        self.ports, self.lock, self.last_crit_pulse = set(), threading.Lock(), 0.0
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, "readings.csv")
        new = not os.path.exists(path)
        self._f = open(path, "a", newline="", buffering=1)
        self._w = csv.writer(self._f)
        if new:
            self._w.writerow(["time_iso", "from", "pkt", "status", "rain_raw", "hx_raw", "mq_raw", "soil_raw", "rssi", "snr"])
        twin.on_event = self.on_event

    def feed(self, line):
        d, text = parse_line(line)
        if d is None:
            return
        pkt = self.twin.ingest(d, text, line)
        if pkt is None:
            return
        v = pkt["values"]
        self._w.writerow([datetime.now().isoformat(timespec="seconds"), pkt["hw"], pkt["pkt"], pkt["status"], v.get("RAIN"), v.get("HX"),
                          v.get("MQ"), v.get("SOIL"), pkt.get("rssi"), pkt.get("snr")])
        if pkt["status"] == "ALERT" and pkt["hw"] in ("FLOOD01", "GAS01"):
            self.buzzer.pulse()                       # same behaviour as your original script

    def on_event(self, e):
        if e["severity"] == "critical":
            self.buzzer.pulse(3.0)

    def watchdog(self):
        """1 Hz: re-run the reasoning (so silence is noticed even with no packets) and drive the local alarm."""
        while True:
            time.sleep(1)
            self.twin.uart_ok = bool(self.ports)
            try:
                a = self.twin.assess()["a"]
            except Exception as ex:                   # never let reasoning kill the alarm loop
                print(f"[engine] assess error: {ex}")
                continue
            if (a["flood"] == "CRITICAL" or a["gas"] == "WARNING") and time.time() - self.last_crit_pulse > CRITICAL_REPEAT_S:
                self.last_crit_pulse = time.time()
                self.buzzer.pulse(2.0)

    def gateway_extra(self):
        return {"serial_port": ", ".join(sorted(self.ports)) or None, "baud": BAUD,
                "cpu_temp_c": _cpu_temp(), "disk_used_pct": _disk_pct(), "buzzer_available": self.buzzer.available,
                "buzzer_on": self.buzzer.is_on, "system_uptime_s": _sys_uptime(), "serial_connected": bool(self.ports),
                "gemini_configured": gemini_configured()}


def _cpu_temp():
    try: return round(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000, 1)
    except Exception: return None
def _sys_uptime():
    try: return int(float(open("/proc/uptime").read().split()[0]))
    except Exception: return None
def _disk_pct():
    try:
        u = shutil.disk_usage("/"); return round(100 * u.used / u.total)
    except Exception: return None


# ───────────────────────── serial ─────────────────────────
def serial_ports(forced):
    if forced:
        return [p.strip() for p in forced.split(",") if p.strip()]
    found = [p.device for p in list_ports.comports()]
    if sys.platform.startswith("win"):
        return found
    return list(dict.fromkeys(found + [p for p in SERIAL_CANDIDATES if os.path.exists(p)]))


def serial_worker(gw, port):
    while True:
        try:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = port, BAUD, 0.5
            s.dtr = s.rts = False                 # opening the port must not reset the ESP32
            s.open()
            ser = s
        except Exception:
            time.sleep(3)
            continue
        with gw.lock:
            gw.ports.add(port)
        gw.twin._event("UART_CONNECTED", "info", "Sensor UART connected", f"{port} @ {BAUD}", None, gw.twin.clock())
        try:
            while True:
                b = ser.readline()
                if not b:
                    continue
                msg = b.decode(errors="replace").strip()
                if msg:
                    print(msg.encode("ascii", errors="replace").decode("ascii"))
                    gw.feed(msg)
        except Exception as e:
            gw.twin._event("UART_LOST", "warn", "Sensor UART lost", f"{port}: {e}", None, gw.twin.clock())
        finally:
            with gw.lock:
                gw.ports.discard(port)
            try: ser.close()
            except Exception: pass
        time.sleep(2)


def serial_loop(gw, forced):
    started = set()
    while True:
        for p in serial_ports(forced):
            if p not in started:
                started.add(p)
                threading.Thread(target=serial_worker, args=(gw, p), daemon=True).start()
        time.sleep(5)


# ───────────────────────── HTTP ─────────────────────────
def make_handler(gw):
    twin = gw.twin

    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k): super().__init__(*a, directory=PUBLIC, **k)
        def log_message(self, *a): pass
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def _json(self, obj, code=200):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            p = u.path.rstrip("/")
            if p.startswith("/api/"):
                try:
                    if p == "/api/twin/state":
                        snapshot = twin.snapshot(gw.gateway_extra())
                        with INSPECTION_LOCK:
                            if LATEST_CAMERA:
                                camera = dict(LATEST_CAMERA)
                                camera_age = max(0.0, time.monotonic() - LATEST_CAMERA_RECEIVED_AT)
                                # Pi publishes every 8 seconds; allow normal Wi-Fi jitter.
                                camera["online"] = camera_age <= 25.0
                                camera["age_s"] = round(camera_age, 1)
                                snapshot["camera"] = camera
                            if LATEST_AI_ANALYSIS:
                                snapshot["ai_analysis"] = dict(LATEST_AI_ANALYSIS)
                        return self._json(snapshot)
                    if p == "/api/twin/assessment":
                        return self._json(twin.assessment_public(twin.last or twin.assess()))
                    if p == "/api/twin/why":
                        return self._json(twin.why(q.get("goal", "flood_watch")))
                    if p == "/api/twin/events":
                        return self._json(twin.db.events(int(q.get("limit", 50))))
                    if p == "/api/twin/event":
                        r = twin.db.event_row(int(q.get("id", 0)))
                        if not r:
                            return self._json({"error": "not found"}, 404)
                        r["evidence"] = json.loads(r.pop("evidence_json"))
                        return self._json(r)
                    if p == "/api/twin/timeline":
                        since = twin.clock() - 60 * float(q.get("minutes", 15))
                        return self._json({"points": twin.db.timeline(since), "transitions": twin.db.transitions(30)})
                    if p == "/api/twin/replay":
                        r = twin.replay(int(q.get("id", 0)))
                        return self._json(r or {"error": "not found"}, 200 if r else 404)
                    if p == "/api/db/counts":
                        return self._json(twin.db.counts())
                except Exception as ex:
                    return self._json({"error": str(ex)}, 500)
                return self._json({"error": "no such endpoint"}, 404)
            if u.path.startswith("/logs") or u.path.endswith((".py", ".service", ".sh", ".db")):
                return self.send_error(404)
            super().do_GET()

        def do_POST(self):
            p = urlparse(self.path).path.rstrip("/")
            try:
                if p in ("/api/camera/frame", "/api/ai/analyze"):
                    global LATEST_AI_ANALYSIS, LAST_GEMINI_REQUEST
                    origin = self.headers.get("Origin")
                    host = self.headers.get("Host", "")
                    if origin and origin.rstrip("/") not in (f"http://{host}", f"https://{host}"):
                        return self._json({"error": "Cross-origin request rejected"}, 403)
                    length = int(self.headers.get("Content-Length", 0))
                    if length > MAX_CAMERA_FRAME_BYTES:
                        return self._json({"error": "JPEG must be no larger than 4 MB"}, 413)
                    mime_type = "image/jpeg"
                    if p == "/api/camera/frame" or length:
                        mime_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        if mime_type != "image/jpeg":
                            return self._json({"error": "Send a JPEG frame with Content-Type: image/jpeg"}, 415)
                        if length < 4:
                            return self._json({"error": "JPEG frame is empty or too small"}, 400)
                        frame = self.rfile.read(length)
                        if len(frame) != length:
                            return self._json({"error": "Incomplete JPEG frame"}, 400)
                        try:
                            camera = publish_camera_frame(frame)
                        except ValueError as exc:
                            return self._json({"error": str(exc)}, 400)
                        if p == "/api/camera/frame":
                            return self._json({"camera": camera})
                    else:
                        with INSPECTION_LOCK:
                            frame = LATEST_CAMERA_FRAME
                            camera = dict(LATEST_CAMERA) if LATEST_CAMERA else None
                    if not gemini_configured():
                        return self._json({"error": "Set GEMINI_API_KEY in the PC gateway .env file, then restart the gateway"}, 503)
                    if frame is None or camera is None:
                        return self._json({"error": "No Raspberry Pi camera frame is available yet"}, 409)
                    with INSPECTION_LOCK:
                        remaining = GEMINI_COOLDOWN_S - (time.monotonic() - LAST_GEMINI_REQUEST)
                        if remaining > 0:
                            return self._json({"error": f"Please wait {int(remaining) + 1} seconds before another analysis"}, 429)
                        LAST_GEMINI_REQUEST = time.monotonic()

                    try:
                        analysis = analyze_drain_frame(frame, mime_type)
                    except Exception as exc:
                        print(f"[gemini] analysis failed: {type(exc).__name__}: {exc}")
                        return self._json({"error": gemini_error_summary(exc)}, 502)
                    analysis["event_id"] = camera["event_id"]
                    with INSPECTION_LOCK:
                        LATEST_AI_ANALYSIS = analysis
                    return self._json({"camera": camera, "ai_analysis": analysis})

                n = int(self.headers.get("Content-Length", 0))
                if n > 8192:
                    return self._json({"error": "body too large"}, 413)
                body = json.loads(self.rfile.read(n) or b"{}")
                if p == "/api/twin/counterfactual":
                    return self._json(twin.counterfactual(body.get("overrides", {}), body.get("assessment_id")))
            except Exception as ex:
                return self._json({"error": str(ex)}, 400)
            self._json({"error": "no such endpoint"}, 404)      # telemetry can ONLY arrive over the serial port
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--serial", default=None, help="force serial device(s), comma-separated")
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    os.makedirs(LOG_DIR, exist_ok=True)
    buzzer = Buzzer(BUZZER_GPIO)
    if serial is None:
        raise SystemExit("pyserial missing:  pip install pyserial   (or: sudo apt install python3-serial)")
    topo = json.load(open(os.path.join(ROOT, "config", "topology.json")))["nodes"]
    installed = {v["hw_id"]: bool(v["installed"]) for v in topo.values()}
    future = installed.get("DRAIN01") or installed.get("RIVER01")
    twin = Twin(os.path.join(LOG_DIR, "aquamesh.db"), C.load(CFG), allow_future_nodes=bool(future), installed=installed)
    gw = Gateway(twin, buzzer)
    threading.Thread(target=serial_loop, args=(gw, a.serial), daemon=True).start()
    threading.Thread(target=gw.watchdog, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(gw))
    print(f"AquaMesh gateway up.  Dashboard: http://{a.host}:{a.port}   calibration={twin.cal['version']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        buzzer.close()


if __name__ == "__main__":
    main()
