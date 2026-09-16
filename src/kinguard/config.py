"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("KinguardAI.config")

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
)

# Pose landmark indices used by the fall heuristic.
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_HIP = 23
RIGHT_HIP = 24

EVIDENCE_PREFIX = "fall_incident_"
EVIDENCE_PATTERN = re.compile(rf"^{re.escape(EVIDENCE_PREFIX)}(\d+)\.jpg$")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        logger.warning("Invalid value for %s; falling back to %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        logger.warning("Invalid value for %s; falling back to %s", name, default)
        return default


@dataclass(frozen=True)
class Config:
    """Settings shared by the detection monitor and the web server."""

    host: str
    port: int

    model_path: Path
    model_url: str

    camera_index: int
    video_source: str | None
    fall_threshold_seconds: float
    frame_delay_seconds: float

    evidence_dir: Path
    jpeg_quality: int
    tls_cert: str | None
    tls_key: str | None
    placeholder_size: tuple[int, int] = (640, 480)


def load_config() -> Config:
    """Build a :class:`Config` from the process environment."""
    video_source = os.environ.get("VIDEO_SOURCE", "").strip()
    return Config(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8080),
        model_path=Path(os.environ.get("MODEL_PATH", "pose_landmarker.task")),
        model_url=os.environ.get("MODEL_URL", MODEL_URL),
        camera_index=_env_int("CAMERA_INDEX", 0),
        video_source=video_source or None,
        fall_threshold_seconds=_env_float("FALL_THRESHOLD_SECONDS", 2.0),
        frame_delay_seconds=_env_float("FRAME_DELAY_SECONDS", 0.03),
        evidence_dir=Path(os.environ.get("EVIDENCE_DIR", "evidence")),
        jpeg_quality=_env_int("JPEG_QUALITY", 70),
        tls_cert=os.environ.get("TLS_CERT", "").strip() or None,
        tls_key=os.environ.get("TLS_KEY", "").strip() or None,
    )


def ensure_model(config: Config) -> None:
    """Download the MediaPipe pose landmarker model if it is missing."""
    if config.model_path.exists():
        return

    logger.info("Downloading MediaPipe Pose Landmarker model...")
    config.model_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(config.model_url, config.model_path)
    logger.info("Model download complete.")
