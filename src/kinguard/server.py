"""HTTP dashboard and JSON endpoints.

Serves the bundled dashboard and exposes status, event history, the MJPEG live
stream, and saved evidence snapshots.
"""

from __future__ import annotations

import json
import logging
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .config import EVIDENCE_PATTERN, Config
from .monitor import FallMonitor, make_placeholder_jpeg
from .webcam import WebcamService

logger = logging.getLogger("KinguardAI.web")

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}

STREAM_BOUNDARY = b"--frame\r\n"


class DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    monitor: FallMonitor = None  # type: ignore[assignment]
    webcam: WebcamService = None  # type: ignore[assignment]
    config: Config = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler naming)
        path = unquote(urlparse(self.path).path)

        try:
            if path in ("/", "/index.html"):
                self._serve_static("index.html")
            elif path in ("/style.css", "/app.js"):
                self._serve_static(path.lstrip("/"))
            elif path == "/api/status":
                self._serve_json(self._status())
            elif path == "/api/events":
                self._serve_json({"events": self._merged_events()})
            elif path == "/api/stream":
                self._serve_stream()
            elif path.startswith("/evidence/"):
                self._serve_evidence(path.rsplit("/", 1)[1])
            else:
                self._serve_json({"error": "not found"}, status=404)
        except (BrokenPipeError, ConnectionResetError):
            # The client went away (common for stream connections).
            pass

    def do_POST(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/frame":
                self._handle_frame()
            else:
                self._drain_body()
                self._serve_json({"error": "not found"}, status=404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_frame(self) -> None:
        """Accept an uploaded JPEG and run pose detection on it."""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 8 * 1024 * 1024:
            self._serve_json({"error": "invalid frame"}, status=400)
            return
        body = self.rfile.read(length)
        session_id = parse_qs(urlparse(self.path).query).get(
            "session", ["default"]
        )[0]
        self._serve_json(self.webcam.process(session_id, body))

    def _drain_body(self) -> None:
        """Consume any request body so keep-alive connections stay in sync."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > 0:
            self.rfile.read(length)

    # ------------------------------------------------------------------
    # Status / event aggregation across camera and browser sources
    # ------------------------------------------------------------------
    def _status(self) -> dict:
        if self.webcam.has_active_sessions():
            status = self.webcam.status()
            if status:
                return status
        return self.monitor.snapshot()

    def _merged_events(self, limit: int = 50) -> list[dict]:
        events = list(self.monitor.get_events()) + list(
            self.webcam.get_events()
        )
        events.sort(
            key=lambda event: event.get("timestamp_ms", 0), reverse=True
        )
        return events[:limit]

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------
    def _serve_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, filename: str) -> None:
        resource = files("kinguard") / "dashboard" / filename
        if not resource.is_file():
            self._serve_json({"error": "not found"}, status=404)
            return

        body = resource.read_bytes()
        ext = Path(filename).suffix
        self.send_response(200)
        self.send_header(
            "Content-Type", MIME_TYPES.get(ext, "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_evidence(self, filename: str) -> None:
        if not EVIDENCE_PATTERN.fullmatch(filename):
            self._serve_json({"error": "not found"}, status=404)
            return

        path = self.config.evidence_dir / filename
        if not path.is_file():
            self._serve_json({"error": "not found"}, status=404)
            return

        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_stream(self) -> None:
        self.send_response(200)
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=frame"
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        last_seq = -1
        placeholder_sent = False
        placeholder = make_placeholder_jpeg(
            size=self.config.placeholder_size,
            quality=self.config.jpeg_quality,
        )

        while self.monitor.running:
            frame = self.monitor.wait_for_new_frame(last_seq, timeout=1.0)
            if frame is None:
                if not placeholder_sent and placeholder is not None:
                    jpeg = placeholder
                    placeholder_sent = True
                else:
                    # Keep the connection warm with an empty boundary part.
                    self.wfile.write(STREAM_BOUNDARY + b"\r\n")
                    continue
            else:
                last_seq, jpeg = frame
                placeholder_sent = True

            self.wfile.write(STREAM_BOUNDARY)
            self.wfile.write(b"Content-Type: image/jpeg\r\n")
            self.wfile.write(
                f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
            )
            self.wfile.write(jpeg)
            self.wfile.write(b"\r\n")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.debug("%s - %s", self.address_string(), format % args)


def create_server(
    monitor: FallMonitor, config: Config, webcam: WebcamService
) -> ThreadingHTTPServer:
    """Build the HTTP server, wrapping it in TLS when a cert is configured."""
    DashboardHandler.monitor = monitor
    DashboardHandler.webcam = webcam
    DashboardHandler.config = config
    server = ThreadingHTTPServer((config.host, config.port), DashboardHandler)

    scheme = "http"
    if config.tls_cert and config.tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config.tls_cert, config.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    logger.info(
        "Kinguard AI dashboard available at %s://%s:%s",
        scheme,
        config.host,
        config.port,
    )
    return server


def serve(
    monitor: FallMonitor, config: Config, webcam: WebcamService
) -> None:
    """Run the HTTP server until interrupted."""
    server = create_server(monitor, config, webcam)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down web server gracefully.")
    finally:
        server.server_close()
