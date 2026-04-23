from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum free disk space required to save a retry file (100 MB)
MIN_FREE_DISK_BYTES = 100 * 1024 * 1024


class RetryQueue:
    """File-based retry queue for failed image uploads."""

    def __init__(self, retry_dir: str, max_files: int = 100) -> None:
        self.retry_dir = Path(retry_dir)
        self.max_files = max_files
        self.retry_dir.mkdir(parents=True, exist_ok=True)

    def _generate_filename(self) -> str:
        """Generate a unique timestamp-based filename."""
        now = datetime.now(timezone.utc)
        return now.strftime("%Y%m%d_%H%M%S_") + f"{now.microsecond // 1000:03d}.jpg"

    def _has_sufficient_disk_space(self) -> bool:
        """Check if free disk space is at least 100 MB."""
        try:
            usage = shutil.disk_usage(self.retry_dir)
            return usage.free >= MIN_FREE_DISK_BYTES
        except OSError as exc:
            logger.error("Failed to check disk usage: %s", exc)
            return False

    def _sorted_files(self) -> list[Path]:
        """Return retry files sorted by name (oldest first)."""
        try:
            files = sorted(self.retry_dir.glob("*.jpg"))
            return files
        except OSError as exc:
            logger.error("Failed to list retry directory: %s", exc)
            return []

    def _evict_oldest(self) -> None:
        """Remove the oldest file when max_files is exceeded."""
        files = self._sorted_files()
        while files and len(files) >= self.max_files:
            oldest = files.pop(0)
            try:
                oldest.unlink()
                oldest.with_suffix(".json").unlink(missing_ok=True)
                logger.warning("Evicted oldest retry file: %s", oldest.name)
            except OSError as exc:
                logger.error("Failed to evict %s: %s", oldest.name, exc)

    def _read_captured_at(self, filepath: Path) -> str:
        """Read captured_at from sidecar JSON; fall back to filename parsing."""
        meta_path = filepath.with_suffix(".json")
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                return meta.get("captured_at", "")
        except (OSError, ValueError):
            pass
        # Fall back: parse timestamp from filename (YYYYMMDD_HHMMSS_mmm.jpg)
        stem = filepath.stem
        try:
            if len(stem) >= 15:
                dt = datetime.strptime(stem[:15], "%Y%m%d_%H%M%S")
                return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
        return ""

    def save(self, image_data: bytes, filename: str, captured_at: str = "") -> bool:
        """Save a JPEG image to the retry directory.

        Args:
            image_data: Raw JPEG bytes.
            filename: Original filename (used for logging and metadata).
            captured_at: ISO 8601 UTC timestamp of when the image was captured.

        Returns:
            True if saved successfully, False otherwise.
        """
        if not self._has_sufficient_disk_space():
            logger.error(
                "Insufficient disk space (< 100 MB free); "
                "skipping retry save for %s",
                filename,
            )
            return False

        self._evict_oldest()

        retry_filename = self._generate_filename()
        filepath = self.retry_dir / retry_filename

        try:
            filepath.write_bytes(image_data)
            meta_path = filepath.with_suffix(".json")
            try:
                meta_path.write_text(
                    json.dumps({"original_filename": filename, "captured_at": captured_at})
                )
            except OSError as exc:
                logger.warning("Failed to save metadata for %s: %s", retry_filename, exc)
            logger.info(
                "Saved to retry queue: %s (original: %s)", retry_filename, filename
            )
            return True
        except OSError as exc:
            logger.error("Failed to save retry file %s: %s", retry_filename, exc)
            return False

    def get_pending(self, limit: int = 5) -> list[tuple[str, bytes, str]]:
        """Retrieve pending retry files, oldest first.

        Args:
            limit: Maximum number of files to return.

        Returns:
            List of (filepath_str, image_data, captured_at_iso) tuples.
            captured_at_iso is the original capture time (empty string if unknown).
        """
        files = self._sorted_files()[:limit]
        result: list[tuple[str, bytes, str]] = []

        for filepath in files:
            try:
                data = filepath.read_bytes()
                captured_at = self._read_captured_at(filepath)
                result.append((str(filepath), data, captured_at))
            except OSError as exc:
                logger.error("Failed to read retry file %s: %s", filepath, exc)

        return result

    def remove(self, filepath: str) -> None:
        """Remove a successfully uploaded retry file and its sidecar JSON.

        Args:
            filepath: Absolute path to the file to remove.
        """
        p = Path(filepath)
        try:
            p.unlink()
            logger.info("Removed retry file: %s", filepath)
        except OSError as exc:
            logger.error("Failed to remove retry file %s: %s", filepath, exc)
        try:
            p.with_suffix(".json").unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove retry metadata %s: %s", filepath, exc)

    def count(self) -> int:
        """Return the number of pending retry files."""
        return len(self._sorted_files())
