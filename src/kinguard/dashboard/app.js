/* Kinguard AI dashboard client */

const PHASE_LABELS = {
  starting: "STARTING",
  upright: "UPRIGHT",
  warning: "WARNING",
  fall: "FALL DETECTED",
  error: "ERROR",
  stopped: "STOPPED",
};

const PHASE_META = {
  starting: { pill: "pill-muted", badge: "badge-muted", hero: "" },
  upright: { pill: "pill-ok", badge: "badge-ok", hero: "" },
  warning: { pill: "pill-warn", badge: "badge-warn", hero: "warning" },
  fall: { pill: "pill-alert", badge: "badge-alert", hero: "fall" },
  error: { pill: "pill-warn", badge: "badge-warn", hero: "error" },
  stopped: { pill: "pill-muted", badge: "badge-muted", hero: "error" },
};

const els = {
  connectionPill: document.getElementById("connection-pill"),
  clock: document.getElementById("clock"),
  cameraBadge: document.getElementById("camera-badge"),
  phaseOverlay: document.getElementById("phase-overlay"),
  fpsReadout: document.getElementById("fps-readout"),
  streamState: document.getElementById("stream-state"),
  phaseBadge: document.getElementById("phase-badge"),
  statusHero: document.getElementById("status-hero"),
  statusText: document.getElementById("status-text"),
  statusDetail: document.getElementById("status-detail"),
  posture: document.getElementById("posture-value"),
  fallTimer: document.getElementById("fall-timer"),
  lastEvent: document.getElementById("last-event"),
  storageMode: document.getElementById("storage-mode"),
  incidentList: document.getElementById("incident-list"),
  refreshEventsBtn: document.getElementById("refresh-events-btn"),
  liveStream: document.getElementById("live-stream"),
  webcamToggle: document.getElementById("webcam-toggle"),
  localVideo: document.getElementById("local-video"),
  poseOverlay: document.getElementById("pose-overlay"),
};

function setPill(el, phase) {
  const meta = PHASE_META[phase] || PHASE_META.starting;
  el.className = `pill ${meta.pill}`;
}

function setBadge(el, phase) {
  const meta = PHASE_META[phase] || PHASE_META.starting;
  el.className = `badge ${meta.badge}`;
  el.textContent = PHASE_LABELS[phase] || phase.toUpperCase();
}

function relativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso.replace(" ", "T")).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = Math.max(0, Date.now() - then);
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function pollStatus() {
  try {
    const status = await fetchJson("/api/status");
    // While the visitor's own camera is active, its responses drive the UI.
    if (!webcam.active) renderStatus(status);
    els.connectionPill.textContent = "Live";
    els.connectionPill.className = "pill pill-ok";
  } catch (error) {
    els.connectionPill.textContent = "Offline";
    els.connectionPill.className = "pill pill-alert";
  }
}

function renderStatus(status) {
  const phase = status.phase || "starting";
  const meta = PHASE_META[phase] || PHASE_META.starting;

  setBadge(els.phaseBadge, phase);
  els.statusHero.className = `status-hero ${meta.hero}`.trim();
  els.statusText.textContent = status.status || "Unknown";
  els.statusDetail.textContent = status.camera_ok
    ? "Pose landmarker active"
    : "Waiting for camera feed";

  els.phaseOverlay.textContent = PHASE_LABELS[phase] || status.status;
  els.phaseOverlay.className = `phase-overlay phase-${phase}`;

  els.posture.textContent =
    phase === "fall"
      ? "Horizontal"
      : phase === "warning"
        ? "Leaning"
        : phase === "upright"
          ? "Upright"
          : "—";

  els.fallTimer.textContent = `${(status.fall_seconds || 0).toFixed(1)}s`;
  els.fpsReadout.textContent = `${(status.fps || 0).toFixed(1)} FPS`;

  els.cameraBadge.textContent = status.camera_ok ? "ONLINE" : "OFFLINE";
  els.cameraBadge.className = `badge ${
    status.camera_ok ? "badge-ok" : "badge-muted"
  }`;

  els.storageMode.textContent =
    status.storage === "local" ? "Local snapshots" : "Unknown";

  if (status.last_event_time) {
    els.lastEvent.textContent = relativeTime(status.last_event_time);
  }
}

async function pollEvents() {
  try {
    const payload = await fetchJson("/api/events");
    renderEvents(payload.events || []);
  } catch (error) {
    // Keep the previous list on transient network errors.
  }
}

function renderEvents(events) {
  if (!events.length) {
    els.incidentList.innerHTML =
      '<li class="incident-empty">No fall incidents recorded yet.</li>';
    return;
  }

  els.incidentList.innerHTML = events
    .map((event) => {
      const tags = ['<span class="tag tag-live">Fall detected</span>'];
      tags.push('<span class="tag tag-local">Evidence saved</span>');

      const when = relativeTime(event.time_iso);
      return `
        <li class="incident-item">
          <img src="/evidence/${encodeURIComponent(event.filename)}"
               alt="Fall incident ${event.filename}"
               loading="lazy"
               onerror="this.style.display='none'" />
          <div class="incident-meta">
            <div class="incident-time" title="${event.time_iso}">${when}</div>
            <div class="incident-tags">${tags.join("")}</div>
          </div>
        </li>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ */
/* Bring-your-own-camera mode: the visitor's browser captures frames   */
/* and POSTs them to the server, which runs the pose model.            */
/* ------------------------------------------------------------------ */

const webcam = {
  active: false,
  stream: null,
  busy: false,
  sessionId: Math.random().toString(36).slice(2),
  capture: document.createElement("canvas"),
};

async function startWebcam() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: false,
    });
  } catch (error) {
    els.streamState.textContent = "Camera permission denied";
    return;
  }

  webcam.stream = stream;
  webcam.active = true;
  els.localVideo.srcObject = stream;
  await els.localVideo.play().catch(() => {});

  els.liveStream.removeAttribute("src");
  els.liveStream.hidden = true;
  els.localVideo.hidden = false;
  els.poseOverlay.hidden = false;
  els.webcamToggle.textContent = "Stop my camera";
  els.streamState.textContent = "Streaming from your camera…";
  webcamLoop();
}

function stopWebcam() {
  webcam.active = false;
  if (webcam.stream) {
    webcam.stream.getTracks().forEach((track) => track.stop());
    webcam.stream = null;
  }
  els.localVideo.srcObject = null;
  els.localVideo.hidden = true;
  els.poseOverlay.hidden = true;
  els.liveStream.hidden = false;
  els.liveStream.src = `/api/stream?t=${Date.now()}`;
  els.webcamToggle.textContent = "Use my camera";
  els.streamState.textContent = "Waiting for frames…";
}

function toggleWebcam() {
  if (webcam.active) {
    stopWebcam();
  } else {
    startWebcam();
  }
}

function webcamLoop() {
  if (!webcam.active || webcam.busy) return;

  const video = els.localVideo;
  if (video.readyState < 2 || !video.videoWidth) {
    setTimeout(webcamLoop, 120);
    return;
  }

  webcam.busy = true;
  const capture = webcam.capture;
  capture.width = video.videoWidth;
  capture.height = video.videoHeight;
  capture.getContext("2d").drawImage(video, 0, 0);

  capture.toBlob(
    async (blob) => {
      try {
        const response = await fetch(
          `/api/frame?session=${encodeURIComponent(webcam.sessionId)}`,
          { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: blob }
        );
        if (response.ok) {
          const result = await response.json();
          if (webcam.active) {
            renderStatus(result);
            els.cameraBadge.textContent = "YOUR CAMERA";
            els.cameraBadge.className = "badge badge-ok";
            els.streamState.textContent = "Streaming from your camera…";
            drawOverlay(result);
          }
        }
      } catch (error) {
        // Ignore transient upload failures; the next frame will retry.
      } finally {
        webcam.busy = false;
        if (webcam.active) setTimeout(webcamLoop, 120);
      }
    },
    "image/jpeg",
    0.7
  );
}

function drawOverlay(result) {
  const canvas = els.poseOverlay;
  const video = els.localVideo;
  const width = video.clientWidth;
  const height = video.clientHeight;
  if (!width || !height) return;

  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);

  const landmarks = result.landmarks;
  if (!landmarks) return;

  // Match the letterboxed video content box (object-fit: contain).
  const scale = Math.min(
    width / video.videoWidth,
    height / video.videoHeight
  );
  const boxW = video.videoWidth * scale;
  const boxH = video.videoHeight * scale;
  const offsetX = (width - boxW) / 2;
  const offsetY = (height - boxH) / 2;
  const point = (coords) => [
    offsetX + coords[0] * boxW,
    offsetY + coords[1] * boxH,
  ];

  const shoulderLeft = point(landmarks.shoulder_left);
  const shoulderRight = point(landmarks.shoulder_right);
  const hipLeft = point(landmarks.hip_left);
  const hipRight = point(landmarks.hip_right);
  const shoulderMid = [
    (shoulderLeft[0] + shoulderRight[0]) / 2,
    (shoulderLeft[1] + shoulderRight[1]) / 2,
  ];
  const hipMid = [
    (hipLeft[0] + hipRight[0]) / 2,
    (hipLeft[1] + hipRight[1]) / 2,
  ];

  const line = (a, b, color, lineWidth) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
  };
  const dot = (p, color) => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(p[0], p[1], 5, 0, Math.PI * 2);
    ctx.fill();
  };

  line(shoulderLeft, shoulderRight, "rgba(255,200,0,0.9)", 2);
  line(hipLeft, hipRight, "rgba(255,200,0,0.9)", 2);
  line(shoulderMid, hipMid, "rgba(0,255,255,0.95)", 3);

  dot(shoulderLeft, "#ffffff");
  dot(shoulderRight, "#ffffff");
  dot(hipLeft, "#ffffff");
  dot(hipRight, "#ffffff");
  dot(shoulderMid, "#00ffff");
  dot(hipMid, "#00ffff");
}

els.webcamToggle.addEventListener("click", toggleWebcam);

function tickClock() {
  els.clock.textContent = new Date().toLocaleTimeString();
}

els.refreshEventsBtn.addEventListener("click", pollEvents);

els.liveStream.addEventListener("error", () => {
  if (webcam.active) return;
  els.streamState.textContent = "Stream disconnected — retrying…";
  // Restart the stream after a short delay when the connection drops.
  setTimeout(() => {
    els.liveStream.src = `/api/stream?t=${Date.now()}`;
  }, 2000);
});

setInterval(tickClock, 1000);
setInterval(pollStatus, 1000);
setInterval(pollEvents, 5000);

tickClock();
pollStatus();
pollEvents();
