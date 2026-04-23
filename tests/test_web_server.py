"""Tests for src/web_server.py.

Uses a real HTTPServer running in a background thread to test handler behavior.
All requests are made with urllib.request (no external dependencies).
"""

import json
import os
import sys
import threading
import time
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

# Allow imports from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import Config
from gallery import Gallery
from settings_store import SettingsStore
from web_server import GalleryHandler

DUMMY_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG header + padding


# ---------------------------------------------------------------------------
# Fixture: spin up a real HTTP server on an ephemeral port
# ---------------------------------------------------------------------------
@pytest.fixture()
def server(tmp_path, monkeypatch):
    """Create a gallery, config, static dir, and start a test HTTP server."""
    gallery_dir = tmp_path / "gallery"
    gallery_dir.mkdir()
    gallery = Gallery(str(gallery_dir), max_images=100)

    # Create a static directory with a minimal index.html
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<html><body>Kanshi Camera</body></html>", encoding="utf-8"
    )

    # Build a Config with test-friendly defaults
    monkeypatch.setenv("DEVICE_ID", "test-device")
    monkeypatch.setenv("MAX_GALLERY_IMAGES", "100")
    monkeypatch.setenv("USTREAMER_HOST", "127.0.0.1")
    monkeypatch.setenv("USTREAMER_PORT", "8080")
    monkeypatch.setenv("GALLERY_DIR", str(gallery_dir))
    config = Config()

    # Runtime settings store backed by a temp file so tests are isolated.
    settings_path = tmp_path / "state" / "settings.json"
    settings = SettingsStore(str(settings_path), default_interval_min=3)

    # Assign class-level attributes used by the handler
    GalleryHandler.gallery = gallery
    GalleryHandler.config = config
    GalleryHandler.settings = settings
    GalleryHandler.static_dir = static_dir

    # Bind to port 0 => OS picks a free port
    httpd = HTTPServer(("127.0.0.1", 0), GalleryHandler)
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    yield {
        "base_url": f"http://127.0.0.1:{port}",
        "gallery": gallery,
        "gallery_dir": gallery_dir,
        "config": config,
        "static_dir": static_dir,
        "settings": settings,
        "settings_path": settings_path,
    }

    httpd.shutdown()
    httpd.server_close()


def _get(url: str, allow_redirects: bool = True) -> tuple[int, dict, bytes]:
    """Make a GET request. Returns (status, headers_dict, body)."""
    req = Request(url)
    try:
        resp = urlopen(req, timeout=5)
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, resp.read()
    except HTTPError as exc:
        headers = {k.lower(): v for k, v in exc.headers.items()}
        return exc.code, headers, exc.read()


def _get_no_redirect(url: str) -> tuple[int, dict, bytes]:
    """Make a GET request without following redirects."""
    import urllib.request

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise HTTPError(newurl, code, msg, headers, fp)

    opener = urllib.request.build_opener(NoRedirectHandler)
    req = Request(url)
    try:
        resp = opener.open(req, timeout=5)
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, resp.read()
    except HTTPError as exc:
        headers = {k.lower(): v for k, v in exc.headers.items()}
        body = exc.read() if exc.fp else b""
        return exc.code, headers, body


# ---------------------------------------------------------------------------
# GET / serves index.html
# ---------------------------------------------------------------------------
class TestRootServes:
    def test_root_serves_html(self, server):
        status, headers, body = _get(server["base_url"] + "/")
        assert status == 200
        assert "text/html" in headers.get("content-type", "")
        assert b"Kanshi Camera" in body


# ---------------------------------------------------------------------------
# GET /api/images
# ---------------------------------------------------------------------------
class TestApiImages:
    def test_api_images_returns_json(self, server):
        status, headers, body = _get(server["base_url"] + "/api/images")
        assert status == 200
        assert "application/json" in headers.get("content-type", "")
        data = json.loads(body)
        assert "images" in data
        assert "total" in data
        assert data["total"] == 0
        assert data["images"] == []

    def test_api_images_with_date_filter(self, server):
        gallery_dir = server["gallery_dir"]
        (gallery_dir / "20260101_100000.jpg").write_bytes(b"jan")
        (gallery_dir / "20260201_100000.jpg").write_bytes(b"feb")

        status, headers, body = _get(
            server["base_url"] + "/api/images?date=20260101"
        )
        assert status == 200
        data = json.loads(body)
        assert data["total"] == 1
        assert data["images"][0]["filename"] == "20260101_100000.jpg"

    def test_api_images_with_pagination(self, server):
        gallery_dir = server["gallery_dir"]
        for i in range(5):
            (gallery_dir / f"20260101_10000{i}.jpg").write_bytes(b"data")

        status, headers, body = _get(
            server["base_url"] + "/api/images?limit=2&offset=0"
        )
        assert status == 200
        data = json.loads(body)
        assert data["total"] == 5
        assert len(data["images"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

    def test_api_images_clamps_limit(self, server):
        status, headers, body = _get(
            server["base_url"] + "/api/images?limit=9999"
        )
        assert status == 200
        data = json.loads(body)
        # Limit should be clamped to 2000 (raised to fit a full day)
        assert data["limit"] == 2000

    def test_api_images_order_asc(self, server):
        gallery_dir = server["gallery_dir"]
        (gallery_dir / "20260101_100000.jpg").write_bytes(b"a")
        (gallery_dir / "20260101_100100.jpg").write_bytes(b"b")
        (gallery_dir / "20260101_100200.jpg").write_bytes(b"c")
        status, _h, body = _get(
            server["base_url"] + "/api/images?order=asc"
        )
        assert status == 200
        data = json.loads(body)
        assert data["order"] == "asc"
        assert [i["filename"] for i in data["images"]] == [
            "20260101_100000.jpg",
            "20260101_100100.jpg",
            "20260101_100200.jpg",
        ]

    def test_api_images_invalid_order_falls_back_to_desc(self, server):
        status, _h, body = _get(
            server["base_url"] + "/api/images?order=sideways"
        )
        assert status == 200
        data = json.loads(body)
        assert data["order"] == "desc"


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------
class TestApiStatus:
    def test_api_status_returns_json(self, server):
        status, headers, body = _get(server["base_url"] + "/api/status")
        assert status == 200
        assert "application/json" in headers.get("content-type", "")
        data = json.loads(body)
        assert data["device_id"] == "test-device"
        assert "gallery_count" in data
        assert "max_gallery_images" in data
        assert "stream_url" in data


# ---------------------------------------------------------------------------
# GET /api/dates
# ---------------------------------------------------------------------------
class TestApiDates:
    def test_api_dates_returns_json(self, server):
        gallery_dir = server["gallery_dir"]
        (gallery_dir / "20260101_100000.jpg").write_bytes(b"a")
        (gallery_dir / "20260201_100000.jpg").write_bytes(b"b")

        status, headers, body = _get(server["base_url"] + "/api/dates")
        assert status == 200
        assert "application/json" in headers.get("content-type", "")
        data = json.loads(body)
        assert "dates" in data
        assert "20260201" in data["dates"]
        assert "20260101" in data["dates"]


# ---------------------------------------------------------------------------
# GET /gallery/<filename>
# ---------------------------------------------------------------------------
class TestGalleryImage:
    def test_gallery_image_serves_jpeg(self, server):
        gallery_dir = server["gallery_dir"]
        (gallery_dir / "20260322_143015.jpg").write_bytes(DUMMY_IMAGE)

        status, headers, body = _get(
            server["base_url"] + "/gallery/20260322_143015.jpg"
        )
        assert status == 200
        assert headers.get("content-type") == "image/jpeg"
        assert body == DUMMY_IMAGE

    def test_gallery_image_not_found(self, server):
        status, headers, body = _get(
            server["base_url"] + "/gallery/missing.jpg"
        )
        assert status == 404
        data = json.loads(body)
        assert "error" in data

    def test_gallery_image_traversal_blocked(self, server):
        status, headers, body = _get(
            server["base_url"] + "/gallery/../etc/passwd"
        )
        assert status == 404
        data = json.loads(body)
        assert "error" in data


# ---------------------------------------------------------------------------
# GET /stream redirects
# ---------------------------------------------------------------------------
class TestStreamRedirect:
    def test_stream_redirect(self, server):
        status, headers, body = _get_no_redirect(
            server["base_url"] + "/stream"
        )
        assert status == 302
        location = headers.get("location", "")
        assert "action=stream" in location
        assert "127.0.0.1" in location
        assert "8080" in location


# ---------------------------------------------------------------------------
# Unknown path returns 404
# ---------------------------------------------------------------------------
class TestUnknownPath:
    def test_unknown_path_returns_404(self, server):
        status, headers, body = _get(server["base_url"] + "/unknown")
        assert status == 404
        data = json.loads(body)
        assert "error" in data


# ---------------------------------------------------------------------------
# /api/settings GET/POST
# ---------------------------------------------------------------------------
def _post_json(url: str, payload) -> tuple[int, bytes]:
    if isinstance(payload, (dict, list)):
        body = json.dumps(payload).encode("utf-8")
    elif isinstance(payload, bytes):
        body = payload
    else:
        body = str(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urlopen(req, timeout=5)
        return resp.status, resp.read()
    except HTTPError as exc:
        return exc.code, exc.read()


class TestApiSettings:
    def test_get_returns_default(self, server):
        status, _h, body = _get(server["base_url"] + "/api/settings")
        assert status == 200
        data = json.loads(body)
        assert data["interval_min"] == 3  # default_interval_min from fixture
        assert data["min"] == 1
        assert data["max"] == 10

    def test_post_updates_interval(self, server):
        status, body = _post_json(
            server["base_url"] + "/api/settings", {"interval_min": 7}
        )
        assert status == 200
        data = json.loads(body)
        assert data["interval_min"] == 7

        status, _h, body = _get(server["base_url"] + "/api/settings")
        assert json.loads(body)["interval_min"] == 7
        assert server["settings"].get_interval_min() == 7

    @pytest.mark.parametrize("bad", [0, 11, -1, 100, "5", 1.5, None, True])
    def test_post_rejects_invalid(self, server, bad):
        status, body = _post_json(
            server["base_url"] + "/api/settings", {"interval_min": bad}
        )
        assert status == 400
        assert "error" in json.loads(body)

    def test_post_rejects_non_object(self, server):
        status, body = _post_json(server["base_url"] + "/api/settings", [1, 2])
        assert status == 400
        assert "error" in json.loads(body)

    def test_post_rejects_invalid_json(self, server):
        status, body = _post_json(
            server["base_url"] + "/api/settings", b"not-json"
        )
        assert status == 400
        assert "error" in json.loads(body)

    def test_post_rejects_oversized_body(self, server):
        huge = {"interval_min": 5, "pad": "x" * 4096}
        status, body = _post_json(server["base_url"] + "/api/settings", huge)
        assert status == 413
        assert "error" in json.loads(body)
