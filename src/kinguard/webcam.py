"""Browser-webcam fall detection.

Unlike :class:`~kinguard.monitor.FallMonitor`, which reads a camera attached to
the host machine, this service lets a remote visitor use *their own* camera:
the browser captures frames with ``getUserMedia`` and POSTs them to
``/api/frame``. The server runs the same pose model and fall heuristic here.

Each browser gets its own session so concurrent visitors do not interfere. The
evidence snapshots and incident history are shared across all sessions.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

# Quiet OpenCV's noisy camera warnings when importing it.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2
import mediapipe as mp
import numpy as np

from .config import EVIDENCE_PREFIX, Config, ensure_model
from .monitor import torso_is_horizontal

logger = logging.getLogger("KinguardAI.webcam")

# Pose landmark indices surfaced to the browser for the overlay.
OVERLAY_LANDMARKS = {
    "shoulder_left": 11,
    "shoulder_right": 12,
    "hip_left": 23,
    "hip_right": 24,
}

# Sessions idle for longer than this are considered gone.
SESSION_TIMEOUT_SECONDS = 10.0


@dataclass
class _Session:
    """Per-browser fall state."""

    fall_started_at: float | None = None
    event_recorded: bool = False
    phase: str = "upright"
    status: str = "Monitoring Upright"
    horizontal: bool = False
    frame_count: int = 0
    window_start: float = field(default_factory=time.time)
    fps: float = 0.0
    last_seen: float = field(default_factory=time.time)


def _overlay_landmarks(landmarks) -> dict | None:
    if landmarks is None:
        return None
    return {
        name: [landmarks[index].x, landmarks[index].y]
        for name, index in OVERLAY_LANDMARKS.items()
    }


class WebcamService:
    """Runs pose detection on frames uploaded by remote browsers."""

    def __init__(self, config: Config):
        self._config = config
        self._lock = threading.Lock()
        self._landmarker = None
        self._last_timestamp_ms = 0
        self._sessions: dict[str, _Session] = {}
        self._events: list[dict] = []
        self._last_event_time: str | None = None

    # ------------------------------------------------------------------
    # Public API used by the web server
    # ------------------------------------------------------------------
    def get_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._events[:limit])

    def has_active_sessions(self) -> bool:
        now = time.time()
        with self._lock:
            return any(
                now - session.last_seen < SESSION_TIMEOUT_SECONDS
                for session in self._sessions.values()
            )

    def status(self) -> dict:
        """Return the freshest session's status, or an empty dict if idle."""
        with self._lock:
            if not self._sessions:
                return {}
            session = max(
                self._sessions.values(), key=lambda item: item.last_seen
            )
            return {
                "status": session.status,
                "phase": session.phase,
                "camera_ok": True,
                "fps": round(session.fps, 1),
                "fall_seconds": self._fall_seconds(session),
                "last_event_time": self._last_event_time,
                "storage": "local",
                "running": True,
                "source": "webcam",
            }

    def process(self, session_id: str, jpeg_bytes: bytes) -> dict:
        """Detect pose in an uploaded JPEG and advance that session's state."""
        frame = cv2.imdecode(
            np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            return {"error": "invalid frame"}

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB, data=rgb_frame
        )

        with self._lock:
            if self._landmarker is None:
                self._ensure_landmarker()
            if self._landmarker is None:
                return {"error": "pose model unavailable"}

            # detect_for_video requires strictly increasing timestamps.
            timestamp_ms = int(time.time() * 1000)
            if timestamp_ms <= self._last_timestamp_ms:
                timestamp_ms = self._last_timestamp_ms + 1
            self._last_timestamp_ms = timestamp_ms

            result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

            session = self._sessions.setdefault(session_id, _Session())
            self._update_fps(session)
            session.last_seen = time.time()

            landmarks = (
                result.pose_landmarks[0] if result.pose_landmarks else None
            )
            session.horizontal = (
                torso_is_horizontal(landmarks)
                if landmarks is not None
                else False
            )
            self._update_state(session, frame, timestamp_ms)

            return {
                "status": session.status,
                "phase": session.phase,
                "camera_ok": True,
                "fps": round(session.fps, 1),
                "fall_seconds": self._fall_seconds(session),
                "last_event_time": self._last_event_time,
                "storage": "local",
                "running": True,
                "source": "webcam",
                "horizontal": session.horizontal,
                "landmarks": _overlay_landmarks(landmarks),
            }

    # ------------------------------------------------------------------
    # Internal helpers (called with self._lock held)
    # ------------------------------------------------------------------
    def _ensure_landmarker(self) -> None:
        try:
            ensure_model(self._config)
            options = mp.tasks.vision.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(
                    model_asset_path=str(self._config.model_path)
                ),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_poses=1,
            )
            self._landmarker = (
                mp.tasks.vision.PoseLandmarker.create_from_options(options)
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to initialise webcam pose model: %s", exc)
            self._landmarker = None

    def _update_state(
        self, session: _Session, frame: np.ndarray, timestamp_ms: int
    ) -> None:
        if session.horizontal:
            if session.fall_started_at is None:
                session.fall_started_at = time.time()
                logger.warning(
                    "Webcam session %s: horizontal orientation detected.",
                    id(session),
                )
            if self._fall_seconds(session) > (
                self._config.fall_threshold_seconds
            ):
                session.phase = "fall"
                session.status = "ALERT: FALL DETECTED!"
                if not session.event_recorded:
                    logger.critical(
                        "Webcam FALL CONFIRMED! Saving local evidence."
                    )
                    self._record_event(frame, timestamp_ms)
                    session.event_recorded = True
            else:
                session.phase = "warning"
                session.status = "Warning: Possible Fall..."
        else:
            if session.fall_started_at is not None:
                logger.info(
                    "Webcam session %s: recovered upright posture.", id(session)
                )
            session.fall_started_at = None
            session.event_recorded = False
            session.phase = "upright"
            session.status = "Monitoring Upright"

    def _update_fps(self, session: _Session) -> None:
        session.frame_count += 1
        now = time.time()
        elapsed = now - session.window_start
        if elapsed >= 1.0:
            session.fps = session.frame_count / elapsed
            session.frame_count = 0
            session.window_start = now

    @staticmethod
    def _fall_seconds(session: _Session) -> float:
        if session.fall_started_at is None:
            return 0.0
        return round(time.time() - session.fall_started_at, 1)

    def _record_event(self, frame: np.ndarray, timestamp_ms: int) -> None:
        filename = f"{EVIDENCE_PREFIX}{timestamp_ms}.jpg"
        path = self._config.evidence_dir / filename
        self._config.evidence_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), frame)

        event = {
            "id": filename,
            "filename": filename,
            "timestamp_ms": timestamp_ms,
            "time_iso": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(timestamp_ms / 1000)
            ),
        }
        self._events.insert(0, event)
        self._last_event_time = event["time_iso"]
        logger.info("Recorded webcam fall incident: %s", path)
