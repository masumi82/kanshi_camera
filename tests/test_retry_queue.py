"""Tests for src/retry_queue.py."""

import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Allow imports from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from retry_queue import RetryQueue

DUMMY_IMAGE = b"\xff\xd8\xff\xe0" + b"\x00" * 50


# ---------------------------------------------------------------------------
# save() creates a file
# ---------------------------------------------------------------------------
class TestSave:
    def test_save_creates_file(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)
        with patch("retry_queue.shutil.disk_usage") as mock_du:
            mock_du.return_value = type("Usage", (), {"free": 500 * 1024 * 1024})()
            result = queue.save(DUMMY_IMAGE, "original.jpg")

        assert result is True
        jpg_files = list(tmp_path.glob("*.jpg"))
        assert len(jpg_files) == 1
        assert jpg_files[0].read_bytes() == DUMMY_IMAGE

    def test_save_returns_false_when_disk_low(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)
        with patch("retry_queue.shutil.disk_usage") as mock_du:
            # Less than 100 MB free
            mock_du.return_value = type("Usage", (), {"free": 50 * 1024 * 1024})()
            result = queue.save(DUMMY_IMAGE, "original.jpg")

        assert result is False
        assert len(list(tmp_path.iterdir())) == 0


# ---------------------------------------------------------------------------
# get_pending() returns oldest first
# ---------------------------------------------------------------------------
class TestGetPending:
    def test_returns_files_oldest_first(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)

        # Create files with known ordering via timestamp-based names
        (tmp_path / "20260101_100000_000.jpg").write_bytes(b"old")
        (tmp_path / "20260101_100001_000.jpg").write_bytes(b"mid")
        (tmp_path / "20260101_100002_000.jpg").write_bytes(b"new")

        pending = queue.get_pending(limit=10)
        assert len(pending) == 3
        # Verify oldest first
        assert pending[0][0].endswith("20260101_100000_000.jpg")
        assert pending[1][0].endswith("20260101_100001_000.jpg")
        assert pending[2][0].endswith("20260101_100002_000.jpg")

    def test_limit_parameter(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)

        for i in range(5):
            (tmp_path / f"20260101_10000{i}_000.jpg").write_bytes(b"data")

        pending = queue.get_pending(limit=2)
        assert len(pending) == 2

    def test_default_limit(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)

        for i in range(10):
            (tmp_path / f"20260101_10000{i}_000.jpg").write_bytes(b"data")

        # Default limit is 5 based on source code
        pending = queue.get_pending()
        assert len(pending) == 5


# ---------------------------------------------------------------------------
# remove() deletes the file
# ---------------------------------------------------------------------------
class TestRemove:
    def test_remove_deletes_file(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)

        filepath = tmp_path / "20260101_100000_000.jpg"
        filepath.write_bytes(b"data")

        queue.remove(str(filepath))
        assert not filepath.exists()

    def test_remove_nonexistent_file_does_not_raise(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)
        # Should not raise
        queue.remove(str(tmp_path / "nonexistent.jpg"))


# ---------------------------------------------------------------------------
# count() returns correct number
# ---------------------------------------------------------------------------
class TestCount:
    def test_count_returns_file_count(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)
        assert queue.count() == 0

        (tmp_path / "20260101_100000_000.jpg").write_bytes(b"data")
        assert queue.count() == 1

        (tmp_path / "20260101_100001_000.jpg").write_bytes(b"data")
        assert queue.count() == 2

    def test_count_after_remove(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)

        f1 = tmp_path / "20260101_100000_000.jpg"
        f1.write_bytes(b"data")
        f2 = tmp_path / "20260101_100001_000.jpg"
        f2.write_bytes(b"data")

        assert queue.count() == 2
        queue.remove(str(f1))
        assert queue.count() == 1


# ---------------------------------------------------------------------------
# max_files eviction
# ---------------------------------------------------------------------------
class TestMaxFilesEviction:
    def test_evicts_oldest_when_at_max(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=3)

        # Pre-populate with max_files files
        (tmp_path / "20260101_100000_000.jpg").write_bytes(b"oldest")
        (tmp_path / "20260101_100001_000.jpg").write_bytes(b"middle")
        (tmp_path / "20260101_100002_000.jpg").write_bytes(b"newest")

        # Save a new file – should evict the oldest one first
        with patch("retry_queue.shutil.disk_usage") as mock_du:
            mock_du.return_value = type("Usage", (), {"free": 500 * 1024 * 1024})()
            queue.save(DUMMY_IMAGE, "new.jpg")

        jpg_files = sorted(f.name for f in tmp_path.glob("*.jpg"))
        # The oldest file should have been evicted
        assert "20260101_100000_000.jpg" not in jpg_files
        # Total jpg files should not exceed max_files
        assert len(jpg_files) <= 3

    def test_multiple_evictions_when_needed(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=2)

        (tmp_path / "20260101_100000_000.jpg").write_bytes(b"a")
        (tmp_path / "20260101_100001_000.jpg").write_bytes(b"b")

        with patch("retry_queue.shutil.disk_usage") as mock_du:
            mock_du.return_value = type("Usage", (), {"free": 500 * 1024 * 1024})()
            queue.save(DUMMY_IMAGE, "new.jpg")

        remaining = sorted(f.name for f in tmp_path.glob("*.jpg"))
        assert len(remaining) <= 2
        assert "20260101_100000_000.jpg" not in remaining


# ---------------------------------------------------------------------------
# captured_at metadata: save, restore, fallback, and sidecar cleanup
# ---------------------------------------------------------------------------
class TestCapturedAtMetadata:
    def test_get_pending_returns_captured_at_from_sidecar(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)
        with patch("retry_queue.shutil.disk_usage") as mock_du:
            mock_du.return_value = type("Usage", (), {"free": 500 * 1024 * 1024})()
            queue.save(DUMMY_IMAGE, "original.jpg", captured_at="2026-01-01T10:00:00+00:00")

        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0][2] == "2026-01-01T10:00:00+00:00"

    def test_get_pending_falls_back_to_filename_when_no_sidecar(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)
        (tmp_path / "20260101_100000_000.jpg").write_bytes(DUMMY_IMAGE)
        # No .json sidecar

        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0][2] == "2026-01-01T10:00:00+00:00"

    def test_get_pending_returns_empty_captured_at_for_unrecognised_filename(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)
        (tmp_path / "unknown_file.jpg").write_bytes(DUMMY_IMAGE)

        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0][2] == ""

    def test_remove_also_deletes_sidecar_json(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)
        jpg = tmp_path / "20260101_100000_000.jpg"
        meta = tmp_path / "20260101_100000_000.json"
        jpg.write_bytes(DUMMY_IMAGE)
        meta.write_text('{"captured_at": "2026-01-01T10:00:00+00:00"}')

        queue.remove(str(jpg))
        assert not jpg.exists()
        assert not meta.exists()


# ---------------------------------------------------------------------------
# max_files=0 defensive guard
# ---------------------------------------------------------------------------
class TestMaxFilesZeroGuard:
    def test_evict_oldest_does_not_crash_when_max_files_zero(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=0)
        (tmp_path / "20260101_100000_000.jpg").write_bytes(b"data")
        # Must not raise IndexError
        queue._evict_oldest()

    def test_save_skips_evict_loop_when_max_files_zero(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=0)
        with patch("retry_queue.shutil.disk_usage") as mock_du:
            mock_du.return_value = type("Usage", (), {"free": 500 * 1024 * 1024})()
            result = queue.save(DUMMY_IMAGE, "test.jpg")
        # save itself should still succeed (file is written after evict)
        assert result is True


# ---------------------------------------------------------------------------
# Filename is timestamp-based
# ---------------------------------------------------------------------------
class TestFilenameFormat:
    def test_filename_is_timestamp_based(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)

        with patch("retry_queue.shutil.disk_usage") as mock_du:
            mock_du.return_value = type("Usage", (), {"free": 500 * 1024 * 1024})()
            queue.save(DUMMY_IMAGE, "original.jpg")

        jpg_files = list(tmp_path.glob("*.jpg"))
        assert len(jpg_files) == 1
        # Expected pattern: YYYYMMDD_HHMMSS_mmm.jpg
        pattern = r"^\d{8}_\d{6}_\d{3}\.jpg$"
        assert re.match(pattern, jpg_files[0].name), (
            f"Filename '{jpg_files[0].name}' does not match timestamp pattern"
        )

    def test_unique_filenames_for_rapid_saves(self, tmp_path):
        queue = RetryQueue(str(tmp_path), max_files=100)

        with patch("retry_queue.shutil.disk_usage") as mock_du:
            mock_du.return_value = type("Usage", (), {"free": 500 * 1024 * 1024})()
            # Save with slightly different mock times to get unique names
            with patch("retry_queue.datetime") as mock_dt:
                from datetime import datetime, timezone

                times = [
                    datetime(2026, 1, 1, 10, 0, 0, 0, tzinfo=timezone.utc),
                    datetime(2026, 1, 1, 10, 0, 0, 1000, tzinfo=timezone.utc),
                    datetime(2026, 1, 1, 10, 0, 0, 2000, tzinfo=timezone.utc),
                ]
                mock_dt.now.side_effect = times
                for _t in times:
                    pass  # datetime.now returns actual datetime objects above

                queue.save(DUMMY_IMAGE, "a.jpg")
                queue.save(DUMMY_IMAGE, "b.jpg")
                queue.save(DUMMY_IMAGE, "c.jpg")

        jpg_files = list(tmp_path.glob("*.jpg"))
        names = {f.name for f in jpg_files}
        assert len(names) == 3, f"Expected 3 unique jpg filenames, got {names}"
