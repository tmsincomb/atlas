"""Tests for atlas.analytics."""

from __future__ import annotations

import json

from atlas.analytics import analytics_path, record_event
from atlas.log import RUN_ID


def test_record_event_appends_jsonl(monkeypatch, tmp_path):
    target = tmp_path / "a.jsonl"
    monkeypatch.setenv("ATLAS_FLAG_ANALYTICS", "1")
    monkeypatch.setenv("ATLAS_ANALYTICS_FILE", str(target))
    record_event("detect", 12.34, "success")
    lines = target.read_text().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["run_id"] == RUN_ID
    assert event["command"] == "detect"
    assert event["duration_ms"] == 12.3
    assert event["outcome"] == "success"
    assert "ts" in event


def test_flag_off_writes_nothing(monkeypatch, tmp_path):
    target = tmp_path / "a.jsonl"
    monkeypatch.delenv("ATLAS_FLAG_ANALYTICS", raising=False)
    monkeypatch.setenv("ATLAS_ANALYTICS_FILE", str(target))
    record_event("detect", 1.0, "success")
    assert not target.exists()


def test_unwritable_target_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_FLAG_ANALYTICS", "1")
    # A directory as the target file -> OSError on open; must not propagate.
    monkeypatch.setenv("ATLAS_ANALYTICS_FILE", str(tmp_path))
    record_event("detect", 1.0, "success")


def test_default_path(monkeypatch):
    monkeypatch.delenv("ATLAS_ANALYTICS_FILE", raising=False)
    assert analytics_path().parts[-2:] == (".atlas", "analytics.jsonl")
