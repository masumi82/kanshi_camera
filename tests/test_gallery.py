"""Tests for src/gallery.py."""

import os
import sys
import time
from pathlib import Path

import pytest

# Allow imports from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gallery import Gallery

DUMMY_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # fake JPEG header + padding


# ---------------------------------------------------------------------------
# save() creates a file and returns True
# ---------------------------------------------------------------------------
class TestSave:
    def test_save_creates_file(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        gallery.save(DUMMY_IMAGE, "20260322_143015.jpg")

        files = list(tmp_path.glob("*.jpg"))
        assert len(files) == 1
        assert files[0].name == "20260322_143015.jpg"
        assert files[0].read_bytes() == DUMMY_IMAGE

    def test_save_returns_true_on_success(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        result = gallery.save(DUMMY_IMAGE, "20260322_143015.jpg")
        assert result is True

    def test_save_evicts_oldest_at_max(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=3)

        # Pre-populate with max_images files
        (tmp_path / "20260101_100000.jpg").write_bytes(b"oldest")
        (tmp_path / "20260101_100001.jpg").write_bytes(b"middle")
        (tmp_path / "20260101_100002.jpg").write_bytes(b"newest")

        # Save a new file - should evict oldest first
        gallery.save(DUMMY_IMAGE, "20260101_100003.jpg")

        remaining = sorted(f.name for f in tmp_path.glob("*.jpg"))
        assert "20260101_100000.jpg" not in remaining
        assert "20260101_100003.jpg" in remaining
        assert len(remaining) <= 3


# ---------------------------------------------------------------------------
# list_images()
# ---------------------------------------------------------------------------
class TestListImages:
    def test_list_images_empty(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        images, total = gallery.list_images()
        assert images == []
        assert total == 0

    def test_list_images_newest_first(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)

        (tmp_path / "20260101_100000.jpg").write_bytes(b"old")
        (tmp_path / "20260101_100001.jpg").write_bytes(b"mid")
        (tmp_path / "20260101_100002.jpg").write_bytes(b"new")

        images, total = gallery.list_images()
        assert total == 3
        assert len(images) == 3
        # Newest first
        assert images[0]["filename"] == "20260101_100002.jpg"
        assert images[1]["filename"] == "20260101_100001.jpg"
        assert images[2]["filename"] == "20260101_100000.jpg"

    def test_list_images_with_date_filter(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)

        (tmp_path / "20260101_100000.jpg").write_bytes(b"jan")
        (tmp_path / "20260102_100000.jpg").write_bytes(b"jan2")
        (tmp_path / "20260201_100000.jpg").write_bytes(b"feb")

        images, total = gallery.list_images(date_filter="20260101")
        assert total == 1
        assert len(images) == 1
        assert images[0]["filename"] == "20260101_100000.jpg"

    def test_list_images_pagination(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)

        for i in range(10):
            (tmp_path / f"20260101_10000{i}.jpg").write_bytes(b"data")

        # Get first page
        images, total = gallery.list_images(limit=3, offset=0)
        assert total == 10
        assert len(images) == 3
        # Newest first, so first page starts with the highest numbered file
        assert images[0]["filename"] == "20260101_100009.jpg"

        # Get second page
        images2, total2 = gallery.list_images(limit=3, offset=3)
        assert total2 == 10
        assert len(images2) == 3
        # No overlap between pages
        page1_names = {img["filename"] for img in images}
        page2_names = {img["filename"] for img in images2}
        assert page1_names.isdisjoint(page2_names)


# ---------------------------------------------------------------------------
# get_image_path()
# ---------------------------------------------------------------------------
class TestGetImagePath:
    def test_get_image_path_existing(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        (tmp_path / "20260322_143015.jpg").write_bytes(DUMMY_IMAGE)

        result = gallery.get_image_path("20260322_143015.jpg")
        assert result is not None
        assert isinstance(result, Path)
        assert result.name == "20260322_143015.jpg"

    def test_get_image_path_nonexistent(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        result = gallery.get_image_path("nonexistent.jpg")
        assert result is None

    def test_get_image_path_rejects_traversal(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)

        # Reject "../" in filename
        assert gallery.get_image_path("../etc/passwd") is None
        # Reject "/" in filename
        assert gallery.get_image_path("/etc/passwd") is None
        # Reject ".." in filename
        assert gallery.get_image_path("..") is None
        # Reject backslash traversal
        assert gallery.get_image_path("..\\etc\\passwd") is None


# ---------------------------------------------------------------------------
# get_dates()
# ---------------------------------------------------------------------------
class TestGetDates:
    def test_get_dates(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)

        (tmp_path / "20260101_100000.jpg").write_bytes(b"a")
        (tmp_path / "20260101_100001.jpg").write_bytes(b"b")
        (tmp_path / "20260201_100000.jpg").write_bytes(b"c")
        (tmp_path / "20260301_100000.jpg").write_bytes(b"d")

        dates = gallery.get_dates()
        # Newest first, unique dates only
        assert dates == ["20260301", "20260201", "20260101"]

    def test_get_dates_empty(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        assert gallery.get_dates() == []


# ---------------------------------------------------------------------------
# count()
# ---------------------------------------------------------------------------
class TestCount:
    def test_count(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        assert gallery.count() == 0

        (tmp_path / "20260101_100000.jpg").write_bytes(b"a")
        assert gallery.count() == 1

        (tmp_path / "20260101_100001.jpg").write_bytes(b"b")
        assert gallery.count() == 2


# ---------------------------------------------------------------------------
# _filename_to_iso()
# ---------------------------------------------------------------------------
class TestFilenameToIso:
    def test_filename_to_iso(self):
        result = Gallery._filename_to_iso("20260322_143015.jpg")
        assert result == "2026-03-22T14:30:15+00:00"

    def test_filename_to_iso_different_date(self):
        result = Gallery._filename_to_iso("20250101_000000.jpg")
        assert result == "2025-01-01T00:00:00+00:00"

    def test_filename_to_iso_invalid(self):
        result = Gallery._filename_to_iso("bad.jpg")
        # Should return empty string or not crash
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Time-based eviction (max_days)
# ---------------------------------------------------------------------------
class TestExpiryEviction:
    def _touch(self, d, name):
        p = d / name
        p.write_bytes(b"x")
        return p

    def test_expired_files_removed_on_save(self, tmp_path):
        # max_days=1 so anything with filename stem older than "yesterday" is expired
        gallery = Gallery(str(tmp_path), max_images=1000, max_days=1)
        # Old frame (well over a day ago)
        self._touch(tmp_path, "20200101_120000.jpg")
        self._touch(tmp_path, "20200101_120100.jpg")
        # Fresh "now" frame
        from datetime import datetime, timezone
        now_name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + ".jpg"
        self._touch(tmp_path, now_name)

        gallery.save(DUMMY_IMAGE, "20260101_000000.jpg")
        names = {f.name for f in tmp_path.glob("*.jpg")}
        assert "20200101_120000.jpg" not in names
        assert "20200101_120100.jpg" not in names
        assert now_name in names

    def test_zero_max_days_disables_expiry(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=1000, max_days=0)
        self._touch(tmp_path, "20200101_120000.jpg")
        gallery.save(DUMMY_IMAGE, "20260101_000000.jpg")
        names = {f.name for f in tmp_path.glob("*.jpg")}
        assert "20200101_120000.jpg" in names

    def test_expired_removed_even_if_under_count_cap(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=1000, max_days=1)
        self._touch(tmp_path, "20200101_120000.jpg")
        # Trigger save (which runs expiry unconditionally)
        gallery.save(DUMMY_IMAGE, "20260101_000000.jpg")
        names = {f.name for f in tmp_path.glob("*.jpg")}
        assert "20200101_120000.jpg" not in names


# ---------------------------------------------------------------------------
# list_images order parameter
# ---------------------------------------------------------------------------
class TestOrder:
    def test_default_order_desc_newest_first(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        (tmp_path / "20260101_100000.jpg").write_bytes(b"a")
        (tmp_path / "20260101_100100.jpg").write_bytes(b"b")
        (tmp_path / "20260101_100200.jpg").write_bytes(b"c")
        images, total = gallery.list_images()
        assert total == 3
        assert [i["filename"] for i in images] == [
            "20260101_100200.jpg",
            "20260101_100100.jpg",
            "20260101_100000.jpg",
        ]

    def test_asc_order_oldest_first(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        (tmp_path / "20260101_100000.jpg").write_bytes(b"a")
        (tmp_path / "20260101_100100.jpg").write_bytes(b"b")
        (tmp_path / "20260101_100200.jpg").write_bytes(b"c")
        images, total = gallery.list_images(order="asc")
        assert total == 3
        assert [i["filename"] for i in images] == [
            "20260101_100000.jpg",
            "20260101_100100.jpg",
            "20260101_100200.jpg",
        ]

    def test_order_combined_with_date_filter(self, tmp_path):
        gallery = Gallery(str(tmp_path), max_images=100)
        (tmp_path / "20260101_100000.jpg").write_bytes(b"a")
        (tmp_path / "20260101_100100.jpg").write_bytes(b"b")
        (tmp_path / "20260102_100000.jpg").write_bytes(b"c")
        images, total = gallery.list_images(date_filter="20260101", order="asc")
        assert total == 2
        assert [i["filename"] for i in images] == [
            "20260101_100000.jpg",
            "20260101_100100.jpg",
        ]
