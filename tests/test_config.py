"""Tests for src/config.py."""

import os
import sys

import pytest

# Allow imports from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import Config


# ---------------------------------------------------------------------------
# Helper: build a minimal valid environment
# ---------------------------------------------------------------------------
def _valid_env() -> dict[str, str]:
    """Return a dict of environment variables that passes validation."""
    return {
        "UPLOAD_URL": "https://example.com/api/v1/images",
        "API_KEY": "test-api-key-123",
    }


# ---------------------------------------------------------------------------
# validate() – success
# ---------------------------------------------------------------------------
class TestValidateSuccess:
    def test_validate_returns_true_with_required_vars(self, monkeypatch):
        for key, value in _valid_env().items():
            monkeypatch.setenv(key, value)
        cfg = Config()
        assert cfg.validate() is True


# ---------------------------------------------------------------------------
# validate() – missing required variables
# ---------------------------------------------------------------------------
class TestValidateMissingRequired:
    def test_exit_when_upload_url_missing(self, monkeypatch):
        env = _valid_env()
        del env["UPLOAD_URL"]
        # Clear all kanshi-related env vars first
        for key in ("UPLOAD_URL", "API_KEY"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        cfg = Config()
        with pytest.raises(SystemExit):
            cfg.validate()

    def test_exit_when_api_key_missing(self, monkeypatch):
        env = _valid_env()
        del env["API_KEY"]
        for key in ("UPLOAD_URL", "API_KEY"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        cfg = Config()
        with pytest.raises(SystemExit):
            cfg.validate()


# ---------------------------------------------------------------------------
# validate() – UPLOAD_URL must be HTTPS
# ---------------------------------------------------------------------------
class TestValidateHttps:
    def test_exit_when_upload_url_not_https(self, monkeypatch):
        env = _valid_env()
        env["UPLOAD_URL"] = "http://example.com/api/v1/images"
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        cfg = Config()
        with pytest.raises(SystemExit):
            cfg.validate()


# ---------------------------------------------------------------------------
# validate() – CAPTURE_INTERVAL_MIN range (1-10)
# ---------------------------------------------------------------------------
class TestValidateCaptureInterval:
    @pytest.mark.parametrize("bad", ["0", "11", "-1", "100"])
    def test_exit_when_out_of_range(self, monkeypatch, bad):
        env = _valid_env()
        env["CAPTURE_INTERVAL_MIN"] = bad
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        cfg = Config()
        with pytest.raises(SystemExit):
            cfg.validate()

    @pytest.mark.parametrize("good", ["1", "5", "10"])
    def test_validate_passes_at_boundaries(self, monkeypatch, good):
        env = _valid_env()
        env["CAPTURE_INTERVAL_MIN"] = good
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        cfg = Config()
        assert cfg.validate() is True


# ---------------------------------------------------------------------------
# validate() – MAX_RETRY_FILES and MAX_GALLERY_IMAGES must be >= 1
# ---------------------------------------------------------------------------
class TestValidatePositiveLimits:
    @pytest.mark.parametrize("key,bad_value", [
        ("MAX_RETRY_FILES", "0"),
        ("MAX_RETRY_FILES", "-1"),
        ("MAX_GALLERY_IMAGES", "0"),
        ("MAX_GALLERY_IMAGES", "-5"),
    ])
    def test_exit_when_limit_is_zero_or_negative(self, monkeypatch, key, bad_value):
        env = _valid_env()
        env[key] = bad_value
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        cfg = Config()
        with pytest.raises(SystemExit):
            cfg.validate()

    @pytest.mark.parametrize("key", ["MAX_RETRY_FILES", "MAX_GALLERY_IMAGES"])
    def test_validate_passes_when_limit_is_one(self, monkeypatch, key):
        env = _valid_env()
        env[key] = "1"
        for k, v in env.items():
            monkeypatch.setenv(k, v)

        cfg = Config()
        assert cfg.validate() is True


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------
class TestDefaults:
    def test_default_device_id(self, monkeypatch):
        monkeypatch.delenv("DEVICE_ID", raising=False)
        cfg = Config()
        assert cfg.device_id == "kanshi-001"

    def test_default_capture_interval_min(self, monkeypatch):
        monkeypatch.delenv("CAPTURE_INTERVAL_MIN", raising=False)
        cfg = Config()
        assert cfg.capture_interval_min == 1

    def test_default_settings_file(self, monkeypatch):
        monkeypatch.delenv("SETTINGS_FILE", raising=False)
        cfg = Config()
        assert cfg.settings_file == "/var/lib/kanshi/state/settings.json"

    def test_default_max_retry_files(self, monkeypatch):
        monkeypatch.delenv("MAX_RETRY_FILES", raising=False)
        cfg = Config()
        assert cfg.max_retry_files == 100

    def test_default_ustreamer_host(self, monkeypatch):
        monkeypatch.delenv("USTREAMER_HOST", raising=False)
        cfg = Config()
        assert cfg.ustreamer_host == "127.0.0.1"

    def test_default_ustreamer_port(self, monkeypatch):
        monkeypatch.delenv("USTREAMER_PORT", raising=False)
        cfg = Config()
        assert cfg.ustreamer_port == 8080

    def test_default_retry_dir(self, monkeypatch):
        monkeypatch.delenv("RETRY_DIR", raising=False)
        cfg = Config()
        assert cfg.retry_dir == "/var/lib/kanshi/retry"


# ---------------------------------------------------------------------------
# snapshot_url property
# ---------------------------------------------------------------------------
class TestSnapshotUrl:
    def test_snapshot_url_default(self, monkeypatch):
        monkeypatch.delenv("USTREAMER_HOST", raising=False)
        monkeypatch.delenv("USTREAMER_PORT", raising=False)
        cfg = Config()
        assert cfg.snapshot_url == "http://127.0.0.1:8080/?action=snapshot"

    def test_snapshot_url_custom(self, monkeypatch):
        monkeypatch.setenv("USTREAMER_HOST", "192.168.1.10")
        monkeypatch.setenv("USTREAMER_PORT", "9090")
        cfg = Config()
        assert cfg.snapshot_url == "http://192.168.1.10:9090/?action=snapshot"
