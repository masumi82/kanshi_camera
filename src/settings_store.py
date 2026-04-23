"""Persistent runtime settings shared between capture_uploader and web_server.

Currently stores only the capture interval (minutes). Written atomically via
tempfile + rename so a concurrent reader never observes a half-written file.
A corrupt or missing file is treated as "no override" and the default is used.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIN_INTERVAL_MIN = 1
MAX_INTERVAL_MIN = 10


class SettingsStore:
    """JSON-backed settings store."""

    def __init__(self, settings_file: str, default_interval_min: int) -> None:
        self.path = Path(settings_file)
        self.default_interval_min = self._clamp(default_interval_min)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create settings dir %s: %s", self.path.parent, exc)

    @staticmethod
    def _clamp(value: int) -> int:
        return max(MIN_INTERVAL_MIN, min(MAX_INTERVAL_MIN, value))

    @staticmethod
    def is_valid_interval(value: Any) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_INTERVAL_MIN <= value <= MAX_INTERVAL_MIN
        )

    def _load(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            logger.error("Failed to read settings %s: %s", self.path, exc)
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Corrupt settings file %s: %s; using defaults", self.path, exc)
            return {}
        if not isinstance(data, dict):
            logger.warning("Settings file %s not an object; using defaults", self.path)
            return {}
        return data

    def get_interval_min(self) -> int:
        """Return the current interval in minutes, clamped to the valid range."""
        data = self._load()
        value = data.get("interval_min")
        if self.is_valid_interval(value):
            return int(value)  # type: ignore[arg-type]
        return self.default_interval_min

    def set_interval_min(self, value: int) -> int:
        """Persist a new interval. Raises ValueError if out of range."""
        if not self.is_valid_interval(value):
            raise ValueError(
                f"interval_min must be an integer in "
                f"[{MIN_INTERVAL_MIN}, {MAX_INTERVAL_MIN}]"
            )
        data = self._load()
        data["interval_min"] = int(value)
        self._atomic_write(data)
        return int(value)

    def _atomic_write(self, data: dict) -> None:
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".settings-", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except OSError as exc:
            logger.error("Failed to write settings %s: %s", self.path, exc)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
