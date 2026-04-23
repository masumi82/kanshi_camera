"""Tests for src/capture_uploader.py clock-boundary logic."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import capture_uploader  # noqa: E402
from capture_uploader import capture_snapshot, next_boundary, sleep_until_next_capture  # noqa: E402


VALID_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 1000


def _make_config():
    os.environ.setdefault("UPLOAD_URL", "https://example.com/api")
    os.environ.setdefault("API_KEY", "key")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from config import Config
    return Config()


class TestCaptureSnapshot:
    @patch("capture_uploader.requests.get")
    def test_returns_bytes_for_valid_jpeg(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = VALID_JPEG
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = capture_snapshot(_make_config())
        assert result == VALID_JPEG

    @patch("capture_uploader.requests.get")
    def test_returns_none_for_empty_response(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b""
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = capture_snapshot(_make_config())
        assert result is None

    @patch("capture_uploader.requests.get")
    def test_returns_none_for_non_jpeg_content_type(self, mock_get):
        mock_response = MagicMock()
        mock_response.content = b"<html>error</html>"
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = capture_snapshot(_make_config())
        assert result is None

    @patch("capture_uploader.requests.get")
    def test_returns_none_for_invalid_magic_bytes(self, mock_get):
        mock_response = MagicMock()
        # Valid Content-Type but not a JPEG (e.g. PNG-like bytes)
        mock_response.content = b"\x89PNG\r\n" + b"\x00" * 100
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = capture_snapshot(_make_config())
        assert result is None


class _FakeSettings:
    def __init__(self, value: int) -> None:
        self._value = value
        self.reads = 0

    def get_interval_min(self) -> int:
        self.reads += 1
        return self._value

    def set(self, value: int) -> None:
        self._value = value


class TestNextBoundary:
    @pytest.mark.parametrize(
        "interval_min,now,expected",
        [
            # 1-minute cadence: :00, :01, :02 ...
            (1, 0.0, 60.0),
            (1, 59.5, 60.0),
            (1, 60.0, 120.0),  # exactly on boundary moves to next
            (1, 60.001, 120.0),
            # 5-minute cadence: :00, :05, :10 ...
            (5, 0.0, 300.0),
            (5, 299.999, 300.0),
            (5, 300.0, 600.0),
            (5, 301.0, 600.0),
            # 10-minute cadence
            (10, 0.0, 600.0),
            (10, 600.0, 1200.0),
            (10, 1201.0, 1800.0),
        ],
    )
    def test_boundary(self, interval_min, now, expected):
        assert next_boundary(interval_min, now) == expected

    def test_returns_strictly_future(self):
        # Across a range of "now" values, next_boundary must be > now.
        for now in [0.0, 12.3, 99.9, 300.0, 1234.56, 9999.0]:
            for iv in range(1, 11):
                assert next_boundary(iv, now) > now


class TestSleepUntilNextCapture:
    def setup_method(self):
        capture_uploader._shutdown_requested = False

    def _patch_time(self, monkeypatch, sequence):
        """Advance time through a preset sequence on each time.time() call.

        The final value repeats forever so the sleep loop eventually settles.
        """
        it = iter(sequence)
        last = [sequence[-1]]

        def fake_time():
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            return last[0]

        monkeypatch.setattr(capture_uploader.time, "time", fake_time)
        monkeypatch.setattr(capture_uploader.time, "sleep", lambda _s: None)

    def test_returns_true_when_boundary_reached(self, monkeypatch):
        # Start at 55s before a 60s boundary, then jump to 60 (boundary hit).
        self._patch_time(monkeypatch, [5.0, 5.0, 60.0])
        settings = _FakeSettings(1)
        assert sleep_until_next_capture(settings) is True

    def test_returns_true_when_past_boundary(self, monkeypatch):
        # Simulate crossing the boundary mid-sleep (now = 60.2 on 2nd check).
        self._patch_time(monkeypatch, [59.0, 59.0, 60.2])
        settings = _FakeSettings(1)
        assert sleep_until_next_capture(settings) is True

    def test_shortening_interval_pulls_target_earlier(self, monkeypatch):
        # Initially interval=5 (period=300), starting at t=10 -> target=300.
        # After the first sleep, interval drops to 1 -> new target should be 60.
        # By the 3rd time.time(), we're at t=60.2 which is past the new target.
        self._patch_time(monkeypatch, [10.0, 10.0, 60.2])
        settings = _FakeSettings(5)

        # Patch get_interval_min so the 2nd read returns 1
        reads = {"n": 0}
        def varying():
            reads["n"] += 1
            return 5 if reads["n"] == 1 else 1
        settings.get_interval_min = varying  # type: ignore[method-assign]

        assert sleep_until_next_capture(settings) is True

    def test_respects_shutdown(self, monkeypatch):
        # Stay well before the boundary; set shutdown flag after first sleep.
        calls = {"n": 0}
        def fake_sleep(_s):
            calls["n"] += 1
            capture_uploader._shutdown_requested = True

        monkeypatch.setattr(capture_uploader.time, "time", lambda: 0.1)
        monkeypatch.setattr(capture_uploader.time, "sleep", fake_sleep)
        settings = _FakeSettings(1)
        assert sleep_until_next_capture(settings) is False
