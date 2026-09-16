# syntax=docker/dockerfile:1

FROM python:3.13-slim

# System libraries required by MediaPipe / OpenCV (both are headless-friendly,
# but MediaPipe still links against GLib/GL and the OpenMP runtime).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libegl1 \
        libgles2 \
        libopengl0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
        curl \
        openssl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for reproducible, lockfile-driven installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install runtime dependencies first so this layer is cached across code edits.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy the application and install it into the virtualenv.
COPY src ./src
RUN uv sync --frozen --no-dev

# Pre-download the pose model so the container runs fully offline.
ENV MODEL_PATH=/app/pose_landmarker.task
RUN curl -fsSL -o "$MODEL_PATH" \
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"

# Generate a self-signed certificate. Browsers require a secure context
# (HTTPS or localhost) before they will expose a webcam via getUserMedia, so
# serving over TLS lets the "Use my camera" feature work when the dashboard is
# reached by IP address. Expect a one-time certificate warning.
RUN mkdir -p /app/certs \
    && openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -keyout /app/certs/key.pem -out /app/certs/cert.pem \
        -subj "/CN=kinguard"

ENV PATH="/app/.venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=8080 \
    EVIDENCE_DIR=/app/evidence \
    TLS_CERT=/app/certs/cert.pem \
    TLS_KEY=/app/certs/key.pem

RUN mkdir -p "$EVIDENCE_DIR"
VOLUME ["/app/evidence"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, ssl, urllib.request; scheme = 'https' if os.environ.get('TLS_CERT') else 'http'; ctx = ssl._create_unverified_context(); urllib.request.urlopen(f\"{scheme}://localhost:{os.environ.get('PORT', '8080')}/api/status\", context=ctx)" || exit 1

CMD ["kinguard"]
