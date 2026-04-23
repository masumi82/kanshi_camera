"""Tests for src/settings_store.py."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from settings_store import (  # noqa: E402
    MAX_INTERVAL_MIN,
    MIN_INTERVAL_MIN,
    SettingsStore,
)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "state" / "settings.json"
    return SettingsStore(str(path), default_interval_min=3)


class TestDefault:
    def test_returns_default_when_file_missing(self, store):
        assert store.get_interval_min() == 3

    def test_default_clamped_to_upper(self, tmp_path):
        s = SettingsStore(str(tmp_path / "s.json"), default_interval_min=50)
        assert s.get_interval_min() == MAX_INTERVAL_MIN

    def test_default_clamped_to_lower(self, tmp_path):
        s = SettingsStore(str(tmp_path / "s.json"), default_interval_min=0)
        assert s.get_interval_min() == MIN_INTERVAL_MIN


class TestSetGet:
    def test_set_then_get_roundtrip(self, store):
        store.set_interval_min(5)
        assert store.get_interval_min() == 5

    def test_set_persists_across_instances(self, tmp_path):
        path = str(tmp_path / "settings.json")
        a = SettingsStore(path, default_interval_min=1)
        a.set_interval_min(7)
        b = SettingsStore(path, default_interval_min=1)
        assert b.get_interval_min() == 7

    @pytest.mark.parametrize("bad", [0, 11, -1, 100, 1.5, "5", True, False, None])
    def test_set_rejects_out_of_range(self, store, bad):
        with pytest.raises(ValueError):
            store.set_interval_min(bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("good", [1, 5, 10])
    def test_set_accepts_boundaries(self, store, good):
        store.set_interval_min(good)
        assert store.get_interval_min() == good


class TestCorruption:
    def test_corrupt_json_falls_back_to_default(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("not valid json", encoding="utf-8")
        s = SettingsStore(str(path), default_interval_min=4)
        assert s.get_interval_min() == 4

    def test_non_object_json_falls_back_to_default(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        s = SettingsStore(str(path), default_interval_min=2)
        assert s.get_interval_min() == 2

    def test_invalid_interval_in_file_falls_back(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"interval_min": 99}), encoding="utf-8")
        s = SettingsStore(str(path), default_interval_min=6)
        assert s.get_interval_min() == 6


class TestAtomicWrite:
    def test_write_is_atomic(self, store, tmp_path):
        store.set_interval_min(4)
        # No residual temp files left behind
        leftovers = list((tmp_path / "state").glob(".settings-*.tmp"))
        assert leftovers == []
