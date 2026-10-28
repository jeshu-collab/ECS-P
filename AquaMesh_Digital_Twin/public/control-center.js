/* The overview consumes the gateway snapshot and sends explicit camera inspections to its API. */
(() => {
  const $ = id => document.getElementById(id);
  const put = (id, value) => { const el = $(id); if (el) el.textContent = value == null ? "—" : String(value); };
  const setState = (id, value, tone) => {
    const el = $(id); if (!el) return;
    el.textContent = value;
    el.className = `state-pill ${tone || "unknown"}`;
  };
  const ageText = seconds => {
    if (!Number.isFinite(seconds) || seconds < 0) return "Unknown";
    if (seconds < 60) return `${Math.round(seconds)} s ago`;
    return `${Math.floor(seconds / 60)} m ago`;
  };
  const sensorTone = node => {
    if (!node) return ["UNKNOWN", "unknown"];
    const h = String(node.health || "").toUpperCase();
    if (h === "ONLINE") return ["ONLINE", "normal"];
    if (h === "STALE") return ["STALE", "warning"];
    if (h === "OFFLINE" || h === "NO_DATA") return [h.replace("_", " "), "critical"];
    return [h || "UNKNOWN", "unknown"];
  };
  const formatTimestamp = value => {
    if (!value) return "Not provided";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Invalid timestamp" : date.toLocaleString("en-IN", { hour12: false });
  };
  function renderCamera(camera) {
    const img = $("ovCameraImage"), placeholder = $("ovCameraPlaceholder");
    const mediaStatus = $("ovMediaStatus");
    const hasCamera = camera && typeof camera === "object";
    const online = hasCamera && typeof camera.online === "boolean" ? camera.online : null;
    put("ovCameraHealth", online === null ? "WAITING FOR PI" : online ? "ONLINE" : "OFFLINE");
    put("ovCameraState", online === null ? "Waiting for Pi frame" : online ? "Pi camera available" : "Pi camera offline");
    put("ovCaptureTime", formatTimestamp(camera && (camera.capture_timestamp || camera.timestamp)));
    put("ovCaptureEvent", hasCamera ? [camera.event_id, camera.trigger_reason || camera.reason].filter(Boolean).join(" · ") || "Not provided" : "Not provided");
    if (mediaStatus) {
      mediaStatus.textContent = online === null ? "WAITING FOR PI" : online ? "PI CAMERA ONLINE" : "CAMERA OFFLINE";
      mediaStatus.className = `state-pill ${online === true ? "normal" : online === false ? "warning" : "unknown"}`;
    }
    let imageUrl = null;
    if (hasCamera && typeof camera.image_url === "string" && camera.image_url.trim()) {
      try {
        const candidate = new URL(camera.image_url, location.href);
        if ((candidate.protocol === "http:" || candidate.protocol === "https:") && candidate.origin === location.origin) imageUrl = candidate.href;
      } catch { /* malformed or unsafe media URL stays unavailable */ }
    }
    // Keep the last JPEG on screen even when the Pi has missed a heartbeat.
    if (imageUrl && img) {
      img.alt = camera.capture_timestamp ? `Drainage capture from ${formatTimestamp(camera.capture_timestamp)}` : "Latest drainage camera capture";
      const mediaKey = `${imageUrl}|${camera.capture_timestamp || camera.timestamp || ""}`;
      if (img.dataset.mediaKey !== mediaKey) {
        img.dataset.mediaKey = mediaKey; img.hidden = true;
        if (placeholder) placeholder.hidden = false;
        put("ovCameraPlaceholderTitle", "Loading latest camera capture");
        put("ovCameraPlaceholderText", "Waiting for the image to load.");
        img.onload = () => { img.hidden = false; if (placeholder) placeholder.hidden = true; };
        img.onerror = () => { img.hidden = true; if (placeholder) placeholder.hidden = false; put("ovCameraPlaceholderTitle", "Camera image failed to load"); put("ovCameraPlaceholderText", "The backend image URL could not be loaded."); };
        img.src = imageUrl;
      }
    } else {
      if (img) { img.hidden = true; img.removeAttribute("src"); img.removeAttribute("data-media-key"); img.onload = null; img.onerror = null; }
      if (placeholder) placeholder.hidden = false;
      put("ovCameraPlaceholderTitle", online === false ? "Camera is offline" : hasCamera && camera.image_url ? "Image URL unavailable" : "Waiting for Raspberry Pi camera");
      put("ovCameraPlaceholderText", online === false ? "The gateway reports that the camera is offline." : hasCamera ? "The image URL must be a valid same-origin HTTP or HTTPS URL." : "The Pi camera publisher has not uploaded a frame yet.");
    }
  }
  function renderAnalysis(analysis, configured = false) {
    const hasAnalysis = analysis && typeof analysis === "object";
    put("ovAiHealth", hasAnalysis ? "RESULT AVAILABLE" : configured ? "READY · WAITING FOR FRAME" : "NOT CONFIGURED");
    put("ovAiLabel", "GEMINI INSPECTION");
    const status = hasAnalysis && typeof analysis.status === "string" ? analysis.status : "Unavailable";
    const normalized = status.toLowerCase();
    const tone = normalized.includes("severe") ? "critical" : normalized.includes("partial") ? "warning" : normalized === "clear" ? "normal" : "unknown";
    put("ovAiVerdict", hasAnalysis ? status : "Unavailable");
    const verdict = $("ovAiVerdict"); if (verdict) verdict.dataset.tone = tone;
    const confidence = hasAnalysis && typeof analysis.confidence === "number" ? analysis.confidence : NaN;
    put("ovAiConfidence", Number.isFinite(confidence) && confidence >= 0 && confidence <= 100 ? `${(confidence <= 1 ? confidence * 100 : confidence).toFixed(0)}%` : "Unknown");
    put("ovAiAction", !hasAnalysis || typeof analysis.action_required !== "boolean" ? "Unknown" : analysis.action_required ? "YES" : "NO");
    const action = $("ovAiAction"); if (action) action.dataset.tone = !hasAnalysis || typeof analysis.action_required !== "boolean" ? "unknown" : analysis.action_required ? "critical" : "normal";
    put("ovAiReasoning", hasAnalysis && typeof analysis.reasoning === "string" && analysis.reasoning.trim() ? analysis.reasoning : configured ? "Waiting for a Pi camera frame. Choose Analyze latest Pi frame when one is available." : "Set GEMINI_API_KEY in the PC gateway .env file to enable inspections.");
    const recommendation = $("ovAiRecommendation");
    if (recommendation) {
      const text = hasAnalysis && typeof analysis.recommended_action === "string" ? analysis.recommended_action.trim() : "";
      recommendation.hidden = !text;
      recommendation.textContent = text ? `Recommended action: ${text}` : "";
    }
  }
  const eventTime = (event, snap) => {
    if (event.timestamp) {
      const date = new Date(event.timestamp);
      if (!Number.isNaN(date.getTime())) return date.toLocaleTimeString("en-IN", { hour12: false });
    }
    if (Number.isFinite(event.t) && Number.isFinite(snap.server_time)) return ageText(snap.server_time - event.t);
    return "Time unavailable";
  };
  function renderEvents(events, snap) {
    const root = $("ovEvents");
    if (!root) return;
    root.replaceChildren();
    if (!Array.isArray(events) || events.length === 0) {
      const empty = document.createElement("p"); empty.className = "empty-note"; empty.textContent = "No events reported by the twin."; root.append(empty); return;
    }
    events.slice(0, 5).forEach(event => {
      const item = document.createElement("div"); item.className = "overview-event";
      const severity = document.createElement("i");
      const level = String(event.severity || event.level || "info").toLowerCase();
      severity.className = `event-mark ${level === "critical" ? "critical" : (level === "warn" || level === "warning") ? "warning" : "normal"}`;
      const copy = document.createElement("div");
      const title = document.createElement("b"); title.textContent = event.title || "System event";
      const detail = document.createElement("p"); detail.textContent = event.detail || (Array.isArray(event.reasons) ? event.reasons.join(", ") : "");
      copy.append(title, detail);
      const time = document.createElement("time"); time.textContent = eventTime(event, snap);
      item.append(severity, copy, time); root.append(item);
    });
  }
  function render(snap) {
    if (!snap || typeof snap !== "object") {
      put("ovFloodState", "NO DATA"); put("ovFloodDetail", "Gateway API has not returned a snapshot");
      put("ovNodes", "0 / 2"); put("ovGateway", "OFFLINE"); put("ovGatewayDetail", "No response from /api/twin/state");
      put("ovApi", "No response"); put("ovFreshness", "No snapshot");
      setState("ovRainStatus", "NO DATA", "unknown"); setState("ovGasStatus", "NO DATA", "unknown");
      renderEvents([], {}); return;
    }
    const nodes = snap.nodes || {}, rain = nodes["RAIN-01"], gas = nodes["AREA-01"], gateway = snap.gateway || {}, a = snap.assessment || {};
    const risk = String(a.flood_state || "UNASSESSED").toUpperCase();
    const riskTone = risk === "CRITICAL" ? "critical" : (risk === "WARNING" || risk === "WATCH") ? "warning" : risk === "NORMAL" ? "normal" : "unknown";
    put("ovFloodState", risk.replaceAll("_", " "));
    put("ovFloodDetail", a.risk_index == null ? "Risk score unavailable" : `${a.risk_index_label || "Heuristic score"}: ${a.risk_index}/100`);
    const flood = $("ovFloodState"); if (flood) flood.dataset.tone = riskTone;

    put("ovRainValue", rain && rain.rain_mm_h != null ? `${Number(rain.rain_mm_h).toFixed(1)} mm/h` : rain && rain.rain_raw != null ? `${rain.rain_raw} raw` : "Not available");
    put("ovRainRaw", rain && rain.rain_raw != null ? rain.rain_raw : "Not available");
    put("ovRainDetail", rain && rain.rain_mm_h != null ? "Estimated · mm/h calibration" : "Raw ADC · calibration unverified");
    put("ovRainQuality", rain && rain.rain_mm_h != null ? "Backend-provided estimate. Raw sensor packets remain available below." : "Rain sensor calibration is unverified; no rainfall rate is reported.");
    const [rLabel, rTone] = sensorTone(rain); setState("ovRainStatus", rLabel, rTone);
    put("ovRainAge", ageText(rain && rain.age_s));
    put("ovRainLoss", rain && rain.packet_loss_pct != null ? `${rain.packet_loss_pct}%` : "Not reported");
    put("ovRainRssi", rain && rain.rssi_dbm != null ? `${rain.rssi_dbm} dBm` : "Not reported");
    put("ovPressure", rain && rain.pressure_raw != null ? `${rain.pressure_raw} raw · unit unavailable` : "Not available");

    const [gLabel, gTone] = sensorTone(gas); setState("ovGasStatus", gLabel, gTone);
    const soilCalibrated = gas && gas.soil_moisture_pct != null;
    put("ovSoilRaw", soilCalibrated ? Number(gas.soil_moisture_pct).toFixed(1) : gas && gas.soil_raw != null ? gas.soil_raw : "Not available");
    put("ovSoilUnit", soilCalibrated ? "% soil moisture" : "raw ADC · uncalibrated");
    put("ovGasQuality", soilCalibrated ? "Estimated soil moisture from the current backend snapshot." : "Soil calibration is unverified. Gas and motion readings are shown only when present.");
    put("ovGasRaw", gas && gas.gas_raw != null ? gas.gas_raw : "Not available");
    put("ovGasAlert", !gas || gas.gas_alert == null ? "Unknown" : gas.gas_alert ? "ACTIVE" : "Clear");
    const motion = gas && [gas.accel_x, gas.accel_y, gas.accel_z].every(Number.isFinite) ? `${gas.accel_x}, ${gas.accel_y}, ${gas.accel_z}` : "Not reported";
    put("ovMotion", motion);
    put("ovGasAge", ageText(gas && gas.age_s));
    put("ovGasLoss", gas && gas.packet_loss_pct != null ? `${gas.packet_loss_pct}%` : "Not reported");

    const installed = [rain, gas].filter(n => n && String(n.health || "").toUpperCase() === "ONLINE").length;
    const knownNodes = [rain, gas].filter(Boolean).length;
    put("ovNodes", `${installed} / 2`);
    const alerts = Array.isArray(a.alerts) ? a.alerts : [];
    put("ovAlertCount", String(alerts.length));
    put("ovAlertDetail", alerts.length ? `${alerts.filter(x => String(x.level || "").toUpperCase() === "CRITICAL").length} critical · twin assessment` : "No active assessment alerts");
    const serial = gateway.serial_connected === true;
    put("ovGateway", serial ? "CONNECTED" : gateway.serial_connected === false ? "NO UART" : "UNKNOWN");
    put("ovGatewayDetail", gateway.last_rx_age_s == null ? "Raspberry Pi · packet age unavailable" : `Raspberry Pi · last packet ${ageText(gateway.last_rx_age_s)}`);
    put("ovFreshness", snap.timestamp ? `Snapshot ${new Date(snap.timestamp).toLocaleTimeString("en-IN", { hour12: false })}` : "Snapshot time unavailable");
    put("ovApi", "Reachable · snapshot received");
    put("ovSerial", serial ? "USB serial connected" : gateway.serial_connected === false ? "No serial connection" : "Status unavailable");
    const lora = $("flowLora");
    if (lora) lora.classList.toggle("healthy", !!(rain && rain.age_s != null && rain.age_s < 60) || !!(gas && gas.age_s != null && gas.age_s < 60));
    const flowGateway = $("flowGateway"); if (flowGateway) flowGateway.classList.toggle("healthy", serial);
    const flowApi = $("flowApi"); if (flowApi) flowApi.classList.add("healthy");
    put("ovLora", "Recent packets observed");
    const drain = nodes["DRAIN-01"], river = nodes["RIVER-01"];
    put("ovDrain", drain && drain.installed ? drain.health || "UNKNOWN" : "NOT INSTALLED");
    put("ovRiver", river && river.installed ? river.health || "UNKNOWN" : "NOT INSTALLED");
    renderCamera(snap.camera);
    renderAnalysis(snap.ai_analysis, gateway.gemini_configured === true);
    put("ovEventSource", `${Array.isArray(snap.events) ? snap.events.length : 0} from twin`);
    renderEvents(snap.events, snap);
  }
  let lastSnapshot = null;
  function syncAnalyzeButton() {
    const button = $("ovAnalyzeButton");
    // A frame remains analyzable after the live heartbeat expires.
    const cameraReady = !!(lastSnapshot && lastSnapshot.camera && lastSnapshot.camera.image_url);
    const aiReady = !!(lastSnapshot && lastSnapshot.gateway && lastSnapshot.gateway.gemini_configured === true);
    if (button) button.disabled = !cameraReady || !aiReady;
  }
  function update() {
    if (!window.AQ || typeof window.AQ.snap !== "function") return;
    const snapshot = window.AQ.snap();
    if (snapshot !== lastSnapshot) { lastSnapshot = snapshot; render(snapshot); syncAnalyzeButton(); }
  }
  $("ovAnalyzeButton")?.addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Analyzing...";
    put("ovAiHealth", "ANALYZING FRAME");
    put("ovAiReasoning", "Sending the latest Raspberry Pi camera frame to Gemini...");
    try {
      const response = await fetch("/api/ai/analyze", { method: "POST", cache: "no-store" });
      const result = await response.json();
      if (!response.ok) {
        const error = new Error(result.error || `Gateway returned ${response.status}`);
        error.status = response.status;
        throw error;
      }
      renderCamera(result.camera);
      renderAnalysis(result.ai_analysis, true);
    } catch (error) {
      const message = error && error.message ? error.message : "Gemini analysis failed.";
      const rateLimited = !!(error && error.status === 429) || /rate limit|quota|wait/i.test(message);
      put("ovAiHealth", rateLimited ? "RETRY LATER" : "ANALYSIS FAILED");
      put("ovAiReasoning", message);
    } finally {
      button.textContent = "Analyze latest Pi frame";
      syncAnalyzeButton();
    }
  });
  update();
  const timer = setInterval(update, 500);
  window.addEventListener("pagehide", () => clearInterval(timer), { once: true });
})();
