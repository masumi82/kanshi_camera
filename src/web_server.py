"""Embedded web server for viewing captured camera images."""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import Config
from gallery import Gallery
from settings_store import (
    MAX_INTERVAL_MIN,
    MIN_INTERVAL_MIN,
    SettingsStore,
)

logger = logging.getLogger(__name__)

# Hard upper bound for POST bodies. /api/settings accepts a tiny JSON object.
MAX_JSON_BODY_BYTES = 1024

# Global reference to the server for signal-based shutdown
_server: HTTPServer | None = None


def _handle_signal(signum: int, frame: object) -> None:
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    # server.shutdown() must be called from a non-main thread
    if _server is not None:
        threading.Thread(target=_server.shutdown, daemon=True).start()


class GalleryHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the gallery web interface."""

    gallery: Gallery
    config: Config
    settings: SettingsStore
    static_dir: Path

    def do_GET(self) -> None:
        """Dispatch GET requests to the appropriate handler."""
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path in ("/", "/index.html"):
                self._handle_static()
            elif path == "/api/images":
                self._handle_api_images(parsed.query)
            elif path == "/api/status":
                self._handle_api_status()
            elif path == "/api/dates":
                self._handle_api_dates()
            elif path == "/api/settings":
                self._handle_api_settings_get()
            elif path.startswith("/gallery/"):
                filename = path[len("/gallery/"):]
                self._handle_gallery_image(filename)
            elif path == "/stream":
                self._handle_stream_redirect()
            else:
                self._send_json_error(404, "Not found")
        except Exception as exc:
            logger.exception("Unhandled error processing %s: %s", path, exc)
            self._send_json_error(500, "Internal server error")

    def do_POST(self) -> None:
        """Dispatch POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/settings":
                self._handle_api_settings_post()
            else:
                self._send_json_error(404, "Not found")
        except Exception as exc:
            logger.exception("Unhandled error processing POST %s: %s", path, exc)
            self._send_json_error(500, "Internal server error")

    def _handle_static(self) -> None:
        """Serve the main HTML page."""
        index_path = self.static_dir / "index.html"
        try:
            content = index_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self._send_json_error(404, "index.html not found")
        except OSError as exc:
            logger.error("Failed to read index.html: %s", exc)
            self._send_json_error(500, "Failed to read static file")

    def _handle_api_images(self, query_string: str) -> None:
        """Handle /api/images with optional ?date=, ?limit=, ?offset=, ?order= params."""
        params = parse_qs(query_string)

        date_filter = params.get("date", [""])[0]
        order = params.get("order", ["desc"])[0]
        if order not in ("asc", "desc"):
            order = "desc"

        try:
            limit = int(params.get("limit", ["50"])[0])
            offset = int(params.get("offset", ["0"])[0])
        except ValueError:
            self._send_json_error(400, "Invalid limit or offset parameter")
            return

        # Clamp limit. Raised to 2000 so a full day (1440 @ 1-min cadence) fits.
        limit = max(1, min(limit, 2000))
        offset = max(0, offset)

        images, total = self.gallery.list_images(
            date_filter=date_filter,
            limit=limit,
            offset=offset,
            order=order,
        )

        self._send_json(200, {
            "images": images,
            "total": total,
            "limit": limit,
            "offset": offset,
            "order": order,
        })

    def _get_stream_url(self) -> str:
        """Build a stream URL reachable from the client's browser.

        Prefer the explicit USTREAMER_EXTERNAL_HOST setting so the URL does
        not depend on the untrusted Host request header.  Falls back to the
        Host header hostname only when the config value is empty, which is
        acceptable on a private LAN where header spoofing carries no real risk.
        """
        if self.config.ustreamer_external_host:
            host = self.config.ustreamer_external_host
        else:
            host_header = self.headers.get("Host", "")
            host = host_header.rsplit(":", 1)[0] if host_header else ""
            if not host or host in ("127.0.0.1", "localhost"):
                host = self.config.ustreamer_host
        return f"http://{host}:{self.config.ustreamer_port}/?action=stream"

    def _handle_api_status(self) -> None:
        """Handle /api/status with system status information."""
        self._send_json(200, {
            "device_id": self.config.device_id,
            "gallery_count": self.gallery.count(),
            "max_gallery_images": self.config.max_gallery_images,
            "stream_url": self._get_stream_url(),
        })

    def _handle_api_dates(self) -> None:
        """Handle /api/dates with available date list."""
        dates = self.gallery.get_dates()
        self._send_json(200, {"dates": dates})

    def _handle_api_settings_get(self) -> None:
        """Return current runtime settings and their allowed range."""
        self._send_json(200, {
            "interval_min": self.settings.get_interval_min(),
            "min": MIN_INTERVAL_MIN,
            "max": MAX_INTERVAL_MIN,
        })

    def _handle_api_settings_post(self) -> None:
        """Persist an updated interval_min (1-10)."""
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            self._send_json_error(400, "Invalid Content-Length")
            return
        if length <= 0:
            self._send_json_error(400, "Empty request body")
            return
        if length > MAX_JSON_BODY_BYTES:
            self._send_json_error(413, "Request body too large")
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json_error(400, "Invalid JSON body")
            return

        if not isinstance(payload, dict):
            self._send_json_error(400, "JSON body must be an object")
            return

        interval = payload.get("interval_min")
        try:
            new_value = self.settings.set_interval_min(interval)  # type: ignore[arg-type]
        except ValueError as exc:
            self._send_json_error(400, str(exc))
            return
        except OSError:
            self._send_json_error(500, "Failed to persist settings")
            return

        logger.info("Updated interval_min to %d via API", new_value)
        self._send_json(200, {
            "interval_min": new_value,
            "min": MIN_INTERVAL_MIN,
            "max": MAX_INTERVAL_MIN,
        })

    def _handle_gallery_image(self, filename: str) -> None:
        """Serve a gallery image by filename."""
        image_path = self.gallery.get_image_path(filename)
        if image_path is None:
            self._send_json_error(404, "Image not found")
            return

        try:
            size = image_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "public, max-age=86400")
            self._send_security_headers()
            self.end_headers()
            # Stream in chunks to avoid loading entire file into memory
            with open(image_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except FileNotFoundError:
            self._send_json_error(404, "Image not found")
        except OSError as exc:
            logger.error("Failed to read gallery image %s: %s", filename, exc)
            self._send_json_error(500, "Failed to read image")

    def _handle_stream_redirect(self) -> None:
        """Redirect to the uStreamer MJPEG stream."""
        stream_url = self._get_stream_url()
        self.send_response(302)
        self.send_header("Location", stream_url)
        self.end_headers()

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_json(self, status: int, data: dict) -> None:
        """Send a JSON response."""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json_error(self, status: int, message: str) -> None:
        """Send a JSON error response."""
        self._send_json(status, {"error": message})

    def log_message(self, format: str, *args: object) -> None:
        """Override default log_message to use Python logging."""
        logger.info("%s", format % args)


def main() -> None:
    """Main entry point for the web server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    logger.info("Starting web_server")

    # Register signal handlers
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Load configuration (no validation needed for web server)
    config = Config()

    # Initialize gallery
    gallery = Gallery(
        gallery_dir=config.gallery_dir,
        max_images=config.max_gallery_images,
        max_days=config.max_gallery_days,
    )

    # Shared runtime settings store (interval_min)
    settings = SettingsStore(
        settings_file=config.settings_file,
        default_interval_min=config.capture_interval_min,
    )

    # Set handler class attributes
    GalleryHandler.gallery = gallery
    GalleryHandler.config = config
    GalleryHandler.settings = settings
    GalleryHandler.static_dir = Path(__file__).resolve().parent / "static"

    global _server
    server = HTTPServer((config.web_bind, config.web_port), GalleryHandler)
    _server = server

    logger.info(
        "Web server listening on %s:%d (gallery: %s, %d images)",
        config.web_bind,
        config.web_port,
        config.gallery_dir,
        gallery.count(),
    )

    try:
        server.serve_forever()
    finally:
        server.server_close()
        logger.info("Web server shutdown complete")


if __name__ == "__main__":
    main()
