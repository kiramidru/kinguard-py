# Kinguard AI

Local elderly fall monitoring with a live web dashboard.

Kinguard uses MediaPipe Pose Landmarker to watch a camera feed for a horizontal
torso (shoulder-to-hip vector) and records a snapshot when the posture stays
horizontal for longer than the configured threshold. Everything runs locally —
no cloud services are involved.

## Features

- Real-time MJPEG live stream with pose overlay
- Fall detection with a "possible fall" warning phase
- Local evidence snapshots saved to `evidence/`
- Incident history in the bundled web dashboard
- **Use your own camera:** remote visitors can click *Use my camera* in the
dashboard and stream from their own webcam — the browser captures frames and
  posts them to the server for pose detection

## Requirements

- Python 3.13+
- A webcam, or a video file path set via `VIDEO_SOURCE`

## Installation

```sh
uv sync
```

## Usage

```sh
uv run kinguard
```

Then open <http://localhost:8080>.

### Using a remote camera (browser webcam)

When the dashboard is hosted on another machine, a visitor can use **their own**
camera instead of a camera attached to the host:

1. Open the dashboard in a browser and click **Use my camera**.
2. Grant camera permission. The browser captures frames and sends them to
   `POST /api/frame`; the server runs the pose model and returns the detection
   result, which the dashboard renders live.

Each browser gets its own fall-detection session, so multiple visitors do not
interfere. Evidence snapshots and the incident history are shared.

> **Security:** the dashboard has no authentication and streams camera footage.
> Do not expose it directly to the internet. Use an SSH tunnel
> (`ssh -L 8080:localhost:8080 user@host`) or a reverse proxy with TLS and auth.

Alternatively, run the module directly after installing:

```sh
python -m kinguard
```

## HTTP API

| Method | Path           | Description                                  |
| ------ | -------------- | -------------------------------------------- |
| GET    | `/api/status`  | Current phase/status (webcam session wins if active) |
| GET    | `/api/events`  | Merged incident history from all sources     |
| GET    | `/api/stream`  | Host-camera MJPEG live stream                |
| POST   | `/api/frame`   | Upload a JPEG frame (`?session=<id>`) for browser-webcam detection |

## Running with Docker

Build and run the container:

```sh
docker build -t kinguard-py .
docker run --rm -p 8080:8080 -v "$PWD/evidence:/app/evidence" kinguard-py
```

Then open `https://<host-ip>:8080` from any device on the network (accept the
one-time self-signed certificate warning). Click **Use my camera** to stream from
the browser's own webcam — this needs no device passthrough and works even when
the Docker host has no camera.

The container serves **HTTPS by default** with a self-signed certificate. That is
deliberate: browsers only expose a webcam (`getUserMedia`) in a secure context,
so `http://<host-ip>:8080` would silently block the camera. If you do not need
the browser-webcam feature, you can switch back to plain HTTP with
`-e TLS_CERT= -e TLS_KEY=`.

To use a camera physically attached to the Docker host (Linux only, works over
plain HTTP too):

```sh
docker run --rm -p 8080:8080 --device /dev/video0:/dev/video0 kinguard-py
```

Or with Compose:

```sh
docker compose up --build
```

The image pre-downloads the pose model at build time, so it runs fully offline
afterwards. The only build-time network access is installing packages and
fetching that model file.

> **Security:** publishing port `8080` exposes an unauthenticated dashboard that
> can stream camera footage. Keep it on a trusted network, or put it behind a
> reverse proxy with TLS and authentication.

### Why HTTPS?

Browsers only grant webcam access (`getUserMedia`) to *secure contexts*:
`https://` or `http://localhost`. A page served over `http://<ip>:port` cannot
open the visitor's camera, so the **Use my camera** button will fail there. Use
HTTPS (as the Docker image does), an SSH tunnel that lands on
`http://localhost:8080`, or a reverse proxy with TLS.

## Configuration

All settings are read from environment variables:

| Variable                | Default                    | Description                          |
| ----------------------- | -------------------------- | ------------------------------------ |
| `HOST`                  | `0.0.0.0`                  | Dashboard bind address               |
| `PORT`                  | `8080`                     | Dashboard port                       |
| `CAMERA_INDEX`          | `0`                        | OpenCV camera index                  |
| `VIDEO_SOURCE`          | *(empty)*                  | Optional video file path (loops)     |
| `FALL_THRESHOLD_SECONDS`| `2.0`                      | Seconds horizontal before a fall     |
| `MODEL_PATH`            | `pose_landmarker.task`     | Pose model path (downloaded on start)|
| `EVIDENCE_DIR`          | `evidence`                 | Where snapshots are saved            |
| `JPEG_QUALITY`          | `70`                       | Stream/snapshot JPEG quality         |
| `FRAME_DELAY_SECONDS`   | `0.03`                     | Delay between processed frames       |
| `TLS_CERT`              | *(empty)*                  | Path to a TLS certificate (enables HTTPS) |
| `TLS_KEY`               | *(empty)*                  | Path to the matching TLS private key |

The MediaPipe pose landmarker model is downloaded automatically on first run
when `MODEL_PATH` does not exist.
