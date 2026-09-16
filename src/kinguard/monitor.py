"""Fall-detection engine.

The detection heuristic is intentionally simple: a fall is suspected when the
torso becomes more horizontal than vertical (shoulder-to-hip vector), and it is
confirmed once that horizontal orientation persists for longer than
``Config.fall_threshold_seconds``.
"""

from __future__ import annotations

import logging
import os
import threading
import time

# Quiet OpenCV's noisy camera warnings (e.g. repeated "can't open camera by
# index" lines). Set OPENCV_LOG_LEVEL=WARN to restore them.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import cv2
import mediapipe as mp
import numpy as np

from .config import (
    EVIDENCE_PREFIX,
    LEFT_HIP,
    LEFT_SHOULDER,
    RIGHT_HIP,
    RIGHT_SHOULDER,
    Config,
    ensure_model,
)

logger = logging.getLogger("KinguardAI.monitor")


def torso_is_horizontal(landmarks) -> bool:
    """Return True when the shoulder-to-hip torso lies more flat than tall.

    Shared by the server-camera monitor and the browser-webcam service.
    """
    shoulder_x = (
        landmarks[LEFT_SHOULDER].x + landmarks[RIGHT_SHOULDER].x
    ) / 2
    hip_x = (landmarks[LEFT_HIP].x + landmarks[RIGHT_HIP].x) / 2
    shoulder_y = (
        landmarks[LEFT_SHOULDER].y + landmarks[RIGHT_SHOULDER].y
    ) / 2
    hip_y = (landmarks[LEFT_HIP].y + landmarks[RIGHT_HIP].y) / 2
    return abs(shoulder_x - hip_x) > abs(shoulder_y - hip_y)


def _draw_torso(frame: np.ndarray, landmarks) -> None:
    """Draw the torso keypoints and the shoulder-to-hip vector."""
    height, width = frame.shape[:2]

    def point(index: int) -> tuple[int, int]:
        lm = landmarks[index]
        return int(lm.x * width), int(lm.y * height)

    shoulder_left = point(LEFT_SHOULDER)
    shoulder_right = point(RIGHT_SHOULDER)
    hip_left = point(LEFT_HIP)
    hip_right = point(RIGHT_HIP)

    shoulder_mid = (
        (shoulder_left[0] + shoulder_right[0]) // 2,
        (shoulder_left[1] + shoulder_right[1]) // 2,
    )
    hip_mid = (
        (hip_left[0] + hip_right[0]) // 2,
        (hip_left[1] + hip_right[1]) // 2,
    )

    for coord in (shoulder_left, shoulder_right, hip_left, hip_right):
        cv2.circle(frame, coord, 6, (255, 255, 255), -1)
        cv2.circle(frame, coord, 8, (0, 0, 0), 2)

    cv2.line(frame, shoulder_left, shoulder_right, (255, 200, 0), 2)
    cv2.line(frame, hip_left, hip_right, (255, 200, 0), 2)
    cv2.line(frame, shoulder_mid, hip_mid, (0, 255, 255), 3)
    cv2.circle(frame, shoulder_mid, 5, (0, 255, 255), -1)
    cv2.circle(frame, hip_mid, 5, (0, 255, 255), -1)


def _annotate_frame(
    frame: np.ndarray, status_text: str, landmarks
) -> np.ndarray:
    """Overlay a status banner, timestamp, and pose keypoints on a frame."""
    if landmarks is not None:
        _draw_torso(frame, landmarks)

    if "ALERT" in status_text:
        banner_color = (0, 0, 200)
        text = "FALL DETECTED"
    elif "Warning" in status_text:
        banner_color = (0, 140, 220)
        text = "POSSIBLE FALL"
    elif "Camera" in status_text or "Error" in status_text:
        banner_color = (80, 80, 80)
        text = status_text
    else:
        banner_color = (0, 160, 0)
        text = "MONITORING UPRIGHT"

    height, width = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (width, 56), banner_color, -1)
    cv2.putText(
        frame,
        text,
        (16, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        time.strftime("%Y-%m-%d %H:%M:%S"),
        (width - 240, height - 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return frame


def make_placeholder_jpeg(
    size: tuple[int, int] = (640, 480),
    text: str = "NO CAMERA SIGNAL",
    quality: int = 70,
) -> bytes | None:
    """Return a JPEG placeholder shown while the camera is unavailable."""
    width, height = size
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        text,
        (70, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (0, 0, 255),
        3,
        cv2.LINE_AA,
    )
    ok, buffer = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    return buffer.tobytes() if ok else None


class FallMonitor:
    """Runs the fall-detection loop in a background thread."""

    def __init__(self, config: Config):
        self._config = config
        self._lock = threading.RLock()
        self._frame_condition = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Shared state published to the dashboard.
        self._status_text = "Starting..."
        self._phase = "starting"
        self._camera_ok = False
        self._fps = 0.0
        self._last_event_time: str | None = None
        self._fall_seconds = 0.0
        self._frame_jpeg: bytes | None = None
        self._frame_seq = 0

        self._events: list[dict] = []
        self._last_logged_status: str | None = None

    # ------------------------------------------------------------------
    # Public API used by the web server
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._config.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="FallMonitor", daemon=True
        )
        self._thread.start()
        logger.info("Fall monitor thread started.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def snapshot(self) -> dict:
        """Return a JSON-serialisable status snapshot."""
        with self._lock:
            return {
                "status": self._status_text,
                "phase": self._phase,
                "camera_ok": self._camera_ok,
                "fps": round(self._fps, 1),
                "fall_seconds": round(self._fall_seconds, 1),
                "last_event_time": self._last_event_time,
                "storage": "local",
                "running": self.running,
            }

    def get_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._events[:limit])

    def wait_for_new_frame(
        self, last_seq: int, timeout: float = 1.0
    ) -> tuple[int, bytes] | None:
        """Block until a frame newer than ``last_seq`` is available."""
        with self._frame_condition:
            frame = self._latest_frame(last_seq)
            if frame is None:
                self._frame_condition.wait(timeout)
                frame = self._latest_frame(last_seq)
        return frame

    # ------------------------------------------------------------------
    # Detection loop
    # ------------------------------------------------------------------
    def _run(self) -> None:
        ensure_model(self._config)
        self._set_state("starting", "Initialising pose model...")

        landmarker = self._create_landmarker()
        if landmarker is None:
            return

        cap: cv2.VideoCapture | None = None
        fall_started_at: float | None = None
        event_recorded = False
        frame_count = 0
        fps_window_start = time.time()
        retry_delay = 2.0

        logger.info(
            "Kinguard AI vision monitoring active. Press Ctrl+C to stop."
        )

        try:
            while not self._stop_event.is_set():
                if cap is None or not cap.isOpened():
                    cap = self._open_capture(retry_delay)
                    if cap is None:
                        if self._config.video_source is not None:
                            self._set_state(
                                "error",
                                "Error: cannot open video source: "
                                f"{self._config.video_source}",
                            )
                            return
                        retry_delay = min(retry_delay * 2, 10.0)
                        continue
                    retry_delay = 2.0

                self._set_camera_ok(True)
                success, frame = cap.read()
                if not success:
                    cap = self._handle_read_failure(cap)
                    continue

                timestamp_ms = int(time.time() * 1000)
                fall_started_at, event_recorded = self._process_frame(
                    frame,
                    timestamp_ms,
                    landmarker,
                    fall_started_at,
                    event_recorded,
                )

                # Cheap FPS estimate over a one-second window.
                frame_count += 1
                now = time.time()
                elapsed = now - fps_window_start
                if elapsed >= 1.0:
                    with self._lock:
                        self._fps = frame_count / elapsed
                    frame_count = 0
                    fps_window_start = now

                time.sleep(self._config.frame_delay_seconds)

        except Exception:  # noqa: BLE001 - keep the loop alive for the UI
            logger.exception("Unhandled error in monitoring loop.")
            self._set_state("error", "Error: monitoring loop crashed.")
        finally:
            if cap is not None:
                cap.release()
            landmarker.close()
            # Preserve terminal error messages (e.g. an unopenable video
            # source) instead of overwriting them with "stopped".
            if self._phase != "error":
                self._set_state("stopped", "Monitoring stopped.")

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------
    def _create_landmarker(self):
        try:
            options = mp.tasks.vision.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(
                    model_asset_path=str(self._config.model_path)
                ),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_poses=1,
            )
            return mp.tasks.vision.PoseLandmarker.create_from_options(options)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to initialise PoseLandmarker: %s", exc)
            self._set_state(
                "error", f"Error: could not load pose model: {exc}"
            )
            return None

    def _open_capture(self, retry_delay: float) -> cv2.VideoCapture | None:
        source = (
            self._config.video_source
            if self._config.video_source is not None
            else self._config.camera_index
        )
        cap = cv2.VideoCapture(source)
        if cap.isOpened():
            return cap

        cap.release()
        self._set_camera_ok(False)
        self._set_state("error", "Video source unavailable - retrying...")
        self._stop_event.wait(retry_delay)
        return None

    def _handle_read_failure(
        self, cap: cv2.VideoCapture | None
    ) -> cv2.VideoCapture | None:
        if self._config.video_source is not None:
            # Reached the end of a video file; loop back to the start.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            return cap

        logger.error("Camera feed unavailable or frame capture failed.")
        if cap is not None:
            cap.release()
        self._set_camera_ok(False)
        self._set_state("error", "Camera unavailable - retrying...")
        return None

    def _process_frame(
        self,
        frame: np.ndarray,
        timestamp_ms: int,
        landmarker,
        fall_started_at: float | None,
        event_recorded: bool,
    ) -> tuple[float | None, bool]:
        """Run pose detection and the fall state machine for one frame."""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        detection_result = landmarker.detect_for_video(mp_image, timestamp_ms)

        status_text = "Monitoring Upright"
        phase = "upright"
        landmarks = None

        if detection_result.pose_landmarks:
            landmarks = detection_result.pose_landmarks[0]
            is_horizontal = self._torso_is_horizontal(landmarks)

            if is_horizontal:
                if fall_started_at is None:
                    fall_started_at = time.time()
                    logger.warning(
                        "Horizontal orientation detected. Tracking duration..."
                    )
                elif time.time() - fall_started_at > (
                    self._config.fall_threshold_seconds
                ):
                    status_text = "ALERT: FALL DETECTED!"
                    phase = "fall"
                    if not event_recorded:
                        logger.critical(
                            "FALL CONFIRMED! Saving local evidence snapshot."
                        )
                        self._record_event(frame, timestamp_ms)
                        event_recorded = True
                else:
                    status_text = "Warning: Possible Fall..."
                    phase = "warning"
            else:
                if fall_started_at is not None:
                    logger.info(
                        "Subject recovered upright posture. Resetting timers."
                    )
                fall_started_at = None
                event_recorded = False

        self._log_status_change(status_text)
        self._update_fall_timer(fall_started_at)
        self._set_state(phase, status_text)

        annotated = _annotate_frame(frame.copy(), status_text, landmarks)
        self._publish_frame(annotated)
        return fall_started_at, event_recorded

    @staticmethod
    def _torso_is_horizontal(landmarks) -> bool:
        return torso_is_horizontal(landmarks)

    # ------------------------------------------------------------------
    # State / publishing helpers
    # ------------------------------------------------------------------
    def _latest_frame(self, last_seq: int) -> tuple[int, bytes] | None:
        if self._frame_jpeg is not None and self._frame_seq != last_seq:
            return self._frame_seq, self._frame_jpeg
        return None

    def _set_state(self, phase: str, status_text: str) -> None:
        with self._lock:
            self._phase = phase
            self._status_text = status_text

    def _log_status_change(self, status_text: str) -> None:
        if status_text == self._last_logged_status:
            return
        self._last_logged_status = status_text
        if "ALERT" in status_text:
            logger.error(status_text)
        elif "Warning" in status_text:
            logger.warning(status_text)
        else:
            logger.info(status_text)

    def _set_camera_ok(self, ok: bool) -> None:
        with self._lock:
            self._camera_ok = ok

    def _update_fall_timer(self, fall_started_at: float | None) -> None:
        with self._lock:
            self._fall_seconds = (
                0.0 if fall_started_at is None else time.time() - fall_started_at
            )

    def _publish_frame(self, frame: np.ndarray) -> None:
        ok, buffer = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._config.jpeg_quality]
        )
        if not ok:
            return
        with self._frame_condition:
            self._frame_jpeg = buffer.tobytes()
            self._frame_seq += 1
            self._frame_condition.notify_all()

    def _record_event(self, frame: np.ndarray, timestamp_ms: int) -> None:
        filename = f"{EVIDENCE_PREFIX}{timestamp_ms}.jpg"
        path = self._config.evidence_dir / filename
        cv2.imwrite(str(path), frame)

        event = {
            "id": filename,
            "filename": filename,
            "timestamp_ms": timestamp_ms,
            "time_iso": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(timestamp_ms / 1000)
            ),
        }
        with self._lock:
            self._events.insert(0, event)
            self._last_event_time = event["time_iso"]
        logger.info("Recorded fall incident: %s", path)
