# Dashboard integration guide

## Current architecture and source of truth

The browser reads the Python PC gateway's `GET /api/twin/state` snapshot. The gateway also accepts Pi camera frames at `POST /api/camera/frame` and user-triggered Gemini inspections at `POST /api/ai/analyze`. AI text is inserted as text, not HTML.

The configured physical nodes are `RAIN-01` (`FLOOD01`) and `AREA-01` (`GAS01`). `DRAIN-01` and `RIVER-01` are not installed in `config/topology.json`. The gateway no longer offers a sensor simulator; readings are shown only when present in serial packets. Sensor fields are optional and the UI shows unavailable states when they are absent.

The PC gateway exposes HTTP JSON and the dashboard uses two-second polling. Camera frames and Gemini results are included in `/api/twin/state`.

## Run with live hardware

From `AquaMesh_Digital_Twin/`:

```powershell
python aquamesh_pi.py
```

Open `http://localhost:8000`. The gateway reads sensors from detected USB serial ports; no synthetic sensor readings are generated.

## Measurements, status and thresholds

The dashboard displays backend values and provenance. Unverified ADC calibration stays raw: `pressure_raw` is a HX711 count and is not converted to pressure; soil percentage and rainfall rate are omitted when calibration is unverified. The overview shows packet age, packet loss, and RSSI only when reported. Water level/distance and turbidity are marked unavailable when the installed payload does not provide them. Stale, offline, absent, and not-installed devices are distinct states.

Thresholds and assessment rules remain in the existing backend: `config/calibration.json`, `config/topology.json`, and `engine/knowledge.py` / `engine/features.py`. The UI does not add a second set of flood thresholds. The backend labels `risk_index` as a live heuristic score, not a probability. Assessment alerts come from `assessment.alerts`; the alert view supports severity filtering and an acknowledgement label stored only for the current browser session. It is not sent to or saved by the gateway. Camera refresh and AI action execution are not supported by this backend.

## Fetch/poll contract

The existing page uses this pattern in `public/app.js`; the dashboard consumes its parsed snapshot:

```js
const response = await fetch("/api/twin/state", {
  cache: "no-store",
  signal: AbortSignal.timeout(3500)
});
if (!response.ok) throw new Error(`Gateway returned ${response.status}`);
const snapshot = await response.json();
if (!snapshot || typeof snapshot !== "object" || !snapshot.nodes) {
  throw new Error("Invalid twin snapshot");
}
```

The deployed code uses `AbortController` for compatibility. The backend snapshot includes `timestamp`, `mode`, `nodes`, `assessment`, `gateway`, recent `events`, and when available, `camera` and `ai_analysis`. `events[].t` is gateway monotonic time, not a UTC timestamp; the overview displays it as elapsed age when `server_time` is available.

## Future WebSocket integration

There is no WebSocket server in the current project. If a future backend publishes the same snapshot on a documented endpoint, it can replace polling with a single socket and fall back to the existing GET route. Example only; `/api/twin/stream` is not currently implemented:

```js
let retry = 0;
let socket;
function connectTwinStream() {
  socket = new WebSocket(`${location.origin.replace(/^http/, "ws")}/api/twin/stream`);
  socket.onopen = () => { retry = 0; stopPolling(); };
  socket.onmessage = ({ data }) => {
    try {
      const snapshot = JSON.parse(data);
      if (snapshot && snapshot.nodes && snapshot.assessment) renderSnapshot(snapshot);
    } catch { /* retain last valid snapshot and report malformed stream data */ }
  };
  socket.onclose = () => {
    startPolling();
    const delay = Math.min(30_000, 500 * 2 ** retry++) + Math.random() * 250;
    setTimeout(connectTwinStream, delay);
  };
}
```

Do not enable this example until the Pi API implements and documents the matching stream and message schema.

## Raspberry Pi camera and Gemini inspection

The PC browser does not access its webcam. A camera capture process on the Raspberry Pi sends JPEG frames to the PC gateway's `POST /api/camera/frame` with `Content-Type: image/jpeg` and the JPEG bytes as the request body. The gateway saves the latest frame under `public/captures/latest.jpg`; the dashboard keeps the last image visible between uploads and marks the camera offline only after 25 seconds without a new frame. A same-process camera driver can call `publish_camera_frame(jpeg_bytes)` directly instead of making an HTTP request.

For a one-frame transport check from the Pi, save an image as `latest.jpg` and run:

```sh
curl -X POST -H 'Content-Type: image/jpeg' --data-binary @latest.jpg http://127.0.0.1:8000/api/camera/frame
```

The dashboard's **Analyze latest Pi frame** button sends `POST /api/ai/analyze` to the gateway. The gateway analyzes the most recently uploaded frame and publishes the structured result in `ai_analysis` on `/api/twin/state`. It does not call Gemini for every camera frame; analysis is user-triggered and rate limited.

Install dependencies on the Windows PC with `python -m pip install -r requirements.txt`. Put `GEMINI_API_KEY=your_key` in the ignored `.env` file in this folder and restart the PC gateway; the key is not stored in source control or sent to the browser. The dashboard's **Analyze latest Pi frame** button sends the latest stored JPEG to Gemini through the PC gateway and displays the result below the image. Analysis is user-triggered and rate limited; failed requests show an error under the result area.

The frame upload API is camera-driver independent. The capture process still depends on the camera type: Raspberry Pi CSI module or USB UVC camera.
