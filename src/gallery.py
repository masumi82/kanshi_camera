"""Local image gallery management for the web viewer."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class Gallery:
    """Manage locally stored camera images for web viewing."""

    def __init__(
        self,
        gallery_dir: str,
        max_images: int = 50000,
        max_days: int = 30,
    ) -> None:
        self.gallery_dir = Path(gallery_dir)
        self.max_images = max_images
        self.max_days = max_days
        self.gallery_dir.mkdir(parents=True, exist_ok=True)

    def _sorted_files(self) -> list[Path]:
        """Return gallery JPEG files sorted by name (oldest first)."""
        try:
            return sorted(self.gallery_dir.glob("*.jpg"))
        except OSError as exc:
            logger.error("Failed to list gallery directory: %s", exc)
            return []

    def _evict_oldest(self) -> None:
        """Remove the oldest files when at or over max_images."""
        files = self._sorted_files()
        while files and len(files) >= self.max_images:
            oldest = files.pop(0)
            try:
                oldest.unlink()
                logger.warning("Evicted oldest gallery image: %s", oldest.name)
            except OSError as exc:
                logger.error("Failed to evict %s: %s", oldest.name, exc)

    def _evict_expired(self) -> None:
        """Remove files older than max_days.

        Filenames are ``YYYYMMDD_HHMMSS.jpg`` so lexicographic comparison with
        a cutoff prefix is equivalent to chronological comparison.
        """
        if self.max_days <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_days)
        cutoff_prefix = cutoff.strftime("%Y%m%d_%H%M%S")
        for f in self._sorted_files():
            if f.stem < cutoff_prefix:
                try:
                    f.unlink()
                    logger.info("Evicted expired gallery image: %s", f.name)
                except OSError as exc:
                    logger.error("Failed to evict expired %s: %s", f.name, exc)
            else:
                break  # sorted, no older files beyond this point

    @staticmethod
    def _filename_to_iso(filename: str) -> str:
        """Convert a YYYYMMDD_HHMMSS.jpg filename to ISO 8601 UTC string."""
        stem = Path(filename).stem  # e.g. '20260322_143015'
        if len(stem) < 15:
            return ""
        try:
            date_part = stem[:8]
            time_part = stem[9:15]
            return (
                f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}"
                f"T{time_part[:2]}:{time_part[2:4]}:{time_part[4:6]}+00:00"
            )
        except (IndexError, ValueError):
            return ""

    def save(self, image_data: bytes, filename: str) -> bool:
        """Save a JPEG image to the gallery directory."""
        if "/" in filename or "\\" in filename or ".." in filename:
            logger.warning("Rejected unsafe filename for save: %r", filename)
            return False

        self._evict_expired()
        self._evict_oldest()

        filepath = self.gallery_dir / filename
        try:
            filepath.write_bytes(image_data)
            logger.info("Saved to gallery: %s (%d bytes)", filename, len(image_data))
            return True
        except OSError as exc:
            logger.error("Failed to save gallery image %s: %s", filename, exc)
            return False

    def list_images(
        self,
        date_filter: str = "",
        limit: int = 50,
        offset: int = 0,
        order: str = "desc",
    ) -> tuple[list[dict], int]:
        """List gallery images with optional filtering and pagination.

        Args:
            date_filter: Filter by date prefix (e.g. '20260322').
            limit: Maximum number of images to return.
            offset: Number of images to skip.
            order: 'desc' (newest first, default) or 'asc' (oldest first).

        Returns:
            Tuple of (images_list, total_count).
        """
        files = self._sorted_files()  # oldest first
        if order != "asc":
            files.reverse()

        if date_filter:
            files = [f for f in files if f.name.startswith(date_filter)]

        total_count = len(files)
        page = files[offset : offset + limit]

        images: list[dict] = []
        for filepath in page:
            try:
                size = filepath.stat().st_size
            except OSError:
                size = 0

            images.append(
                {
                    "filename": filepath.name,
                    "url": f"/gallery/{filepath.name}",
                    "captured_at": self._filename_to_iso(filepath.name),
                    "size_bytes": size,
                }
            )

        return images, total_count

    def get_image_path(self, filename: str) -> Path | None:
        """Return the full path to a gallery image if it exists."""
        if "/" in filename or "\\" in filename or ".." in filename:
            logger.warning("Rejected unsafe filename: %r", filename)
            return None

        filepath = self.gallery_dir / filename
        if filepath.is_file():
            return filepath
        return None

    def get_dates(self) -> list[str]:
        """Return unique dates (YYYYMMDD) extracted from filenames, newest first."""
        dates: set[str] = set()
        for filepath in self._sorted_files():
            name = filepath.name
            if len(name) >= 8:
                dates.add(name[:8])

        return sorted(dates, reverse=True)

    def count(self) -> int:
        """Return the number of JPEG files in the gallery."""
        return len(self._sorted_files())
