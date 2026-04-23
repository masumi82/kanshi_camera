"""Tests for src/uploader.py."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

# Allow imports from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import Config
from uploader import upload_image

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
DUMMY_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG header + padding


def _make_config(**overrides) -> Config:
    """Create a Config with sensible defaults for testing.

    Builds Config from environment variables, setting required defaults first,
    then applying any overrides.
    """
    defaults = {
        "UPLOAD_URL": "https://example.com/api/v1/images",
        "API_KEY": "test-api-key-123",
        "DEVICE_ID": "test-device",
    }
    defaults.update(overrides)

    saved = {}
    for key, value in defaults.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = value

    try:
        cfg = Config()
    finally:
        for key, old_val in saved.items():
            if old_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_val

    return cfg


# ---------------------------------------------------------------------------
# Upload success
# ---------------------------------------------------------------------------
class TestUploadSuccess:
    @patch("uploader.requests.post")
    def test_returns_true_on_http_200(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        config = _make_config()
        result = upload_image(DUMMY_IMAGE, config, "test.jpg")
        assert result is True


# ---------------------------------------------------------------------------
# Upload failure – HTTP errors
# ---------------------------------------------------------------------------
class TestUploadHttpErrors:
    @patch("uploader.requests.post")
    def test_returns_false_on_http_500(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_post.return_value = mock_response

        config = _make_config()
        result = upload_image(DUMMY_IMAGE, config, "test.jpg")
        assert result is False


# ---------------------------------------------------------------------------
# Upload failure – connection error
# ---------------------------------------------------------------------------
class TestUploadConnectionError:
    @patch("uploader.requests.post")
    def test_returns_false_on_connection_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        config = _make_config()
        result = upload_image(DUMMY_IMAGE, config, "test.jpg")
        assert result is False


# ---------------------------------------------------------------------------
# Upload failure – timeout
# ---------------------------------------------------------------------------
class TestUploadTimeout:
    @patch("uploader.requests.post")
    def test_returns_false_on_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Request timed out")

        config = _make_config()
        result = upload_image(DUMMY_IMAGE, config, "test.jpg")
        assert result is False


# ---------------------------------------------------------------------------
# Correct Authorization header
# ---------------------------------------------------------------------------
class TestAuthorizationHeader:
    @patch("uploader.requests.post")
    def test_request_includes_bearer_token(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        config = _make_config(API_KEY="my-secret-key")
        upload_image(DUMMY_IMAGE, config, "test.jpg")

        _, kwargs = mock_post.call_args
        assert "headers" in kwargs
        assert kwargs["headers"]["Authorization"] == "Bearer my-secret-key"


# ---------------------------------------------------------------------------
# Multipart/form-data
# ---------------------------------------------------------------------------
class TestMultipartUpload:
    @patch("uploader.requests.post")
    def test_request_uses_files_parameter(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        config = _make_config()
        upload_image(DUMMY_IMAGE, config, "capture.jpg")

        _, kwargs = mock_post.call_args
        assert "files" in kwargs
        # The files dict should contain an "image" key
        assert "image" in kwargs["files"]
        file_tuple = kwargs["files"]["image"]
        assert file_tuple[0] == "capture.jpg"  # filename
        assert file_tuple[1] == DUMMY_IMAGE  # data
        assert file_tuple[2] == "image/jpeg"  # content type


# ---------------------------------------------------------------------------
# Permanent HTTP error (4xx) classification
# ---------------------------------------------------------------------------
class TestPermanentHttpErrors:
    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 405, 422])
    @patch("uploader.requests.post")
    def test_returns_false_on_permanent_4xx(self, mock_post, status_code):
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_post.return_value = mock_response

        config = _make_config()
        result = upload_image(DUMMY_IMAGE, config, "test.jpg")
        assert result is False

    @pytest.mark.parametrize("status_code", [408, 429, 500, 502, 503])
    @patch("uploader.requests.post")
    def test_returns_false_on_retryable_errors(self, mock_post, status_code):
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_post.return_value = mock_response

        config = _make_config()
        result = upload_image(DUMMY_IMAGE, config, "test.jpg")
        assert result is False


# ---------------------------------------------------------------------------
# captured_at argument
# ---------------------------------------------------------------------------
class TestCapturedAt:
    @patch("uploader.requests.post")
    def test_explicit_captured_at_is_sent(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        config = _make_config()
        upload_image(DUMMY_IMAGE, config, "test.jpg", captured_at="2026-01-01T10:00:00+00:00")

        _, kwargs = mock_post.call_args
        assert kwargs["data"]["captured_at"] == "2026-01-01T10:00:00+00:00"

    @patch("uploader.requests.post")
    def test_empty_captured_at_falls_back_to_now(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        config = _make_config()
        upload_image(DUMMY_IMAGE, config, "test.jpg", captured_at="")

        _, kwargs = mock_post.call_args
        assert "captured_at" in kwargs["data"]
        assert kwargs["data"]["captured_at"] != ""


# ---------------------------------------------------------------------------
# UPLOAD_URL uses HTTPS
# ---------------------------------------------------------------------------
class TestHttpsEnforcement:
    @patch("uploader.requests.post")
    def test_upload_url_is_https(self, mock_post):
        """Verify that the config's upload_url starts with https://."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        config = _make_config()
        upload_image(DUMMY_IMAGE, config, "test.jpg")

        args, kwargs = mock_post.call_args
        # First positional arg or url kwarg
        url = args[0] if args else kwargs.get("url", "")
        assert url.startswith("https://"), f"UPLOAD_URL must use HTTPS, got: {url}"
