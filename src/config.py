from __future__ import annotations

import os
import sys
import logging

logger = logging.getLogger(__name__)

MIN_INTERVAL_MIN = 1
MAX_INTERVAL_MIN = 10


class Config:
    """Environment-based configuration."""

    def __init__(self) -> None:
        # Required
        self.upload_url: str = os.environ.get("UPLOAD_URL", "")
        self.api_key: str = os.environ.get("API_KEY", "")

        # Optional with defaults
        self.device_id: str = os.environ.get("DEVICE_ID", "kanshi-001")
        self.capture_interval_min: int = self._int_env(
            "CAPTURE_INTERVAL_MIN", 1
        )
        self.max_retry_files: int = self._int_env("MAX_RETRY_FILES", 100)
        self.ustreamer_host: str = os.environ.get("USTREAMER_HOST", "127.0.0.1")
        self.ustreamer_port: int = self._int_env("USTREAMER_PORT", 8080)
        # Override for stream URLs sent to browsers (e.g. device LAN IP)
        self.ustreamer_external_host: str = os.environ.get("USTREAMER_EXTERNAL_HOST", "")
        self.retry_dir: str = os.environ.get("RETRY_DIR", "/var/lib/kanshi/retry")

        # Web server settings
        self.web_port: int = self._int_env("WEB_PORT", 8888)
        self.web_bind: str = os.environ.get("WEB_BIND", "0.0.0.0")
        self.gallery_dir: str = os.environ.get(
            "GALLERY_DIR", "/var/lib/kanshi/gallery"
        )
        self.max_gallery_images: int = self._int_env("MAX_GALLERY_IMAGES", 50000)
        self.max_gallery_days: int = self._int_env("MAX_GALLERY_DAYS", 30)

        # Runtime settings persistence
        self.settings_file: str = os.environ.get(
            "SETTINGS_FILE", "/var/lib/kanshi/state/settings.json"
        )

        # Stream auth (optional)
        self.stream_user: str = os.environ.get("STREAM_USER", "")
        self.stream_password: str = os.environ.get("STREAM_PASSWORD", "")

    @staticmethod
    def _int_env(name: str, default: int) -> int:
        """Read an integer environment variable with fallback to default."""
        raw = os.environ.get(name, "")
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "Invalid integer for %s: %r, using default %d", name, raw, default
            )
            return default

    @property
    def snapshot_url(self) -> str:
        return f"http://{self.ustreamer_host}:{self.ustreamer_port}/?action=snapshot"

    def validate(self) -> bool:
        """Validate required configuration. Exit if invalid."""
        errors: list[str] = []
        if not self.upload_url:
            errors.append("UPLOAD_URL is required")
        if not self.api_key:
            errors.append("API_KEY is required")
        if self.upload_url and not self.upload_url.startswith("https://"):
            errors.append("UPLOAD_URL must use HTTPS")
        if not (MIN_INTERVAL_MIN <= self.capture_interval_min <= MAX_INTERVAL_MIN):
            errors.append(
                f"CAPTURE_INTERVAL_MIN must be in "
                f"[{MIN_INTERVAL_MIN}, {MAX_INTERVAL_MIN}]"
            )
        if self.max_retry_files < 1:
            errors.append("MAX_RETRY_FILES must be >= 1")
        if self.max_gallery_images < 1:
            errors.append("MAX_GALLERY_IMAGES must be >= 1")
        if errors:
            for e in errors:
                logger.error(f"Config error: {e}")
            sys.exit(1)
        logger.info("Configuration validated successfully")
        return True
