"""Tests for CLI metrics/analytics instrumentation (InstrumentedGroup)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from atlas.cli import main


def _events(target):
    return [json.loads(line) for line in target.read_text().splitlines()]


def _setup(monkeypatch, tmp_path):
    target = tmp_path / "a.jsonl"
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setenv("ATLAS_FLAG_ANALYTICS", "1")
    monkeypatch.setenv("ATLAS_ANALYTICS_FILE", str(target))
    return target


def test_success_event(monkeypatch, tmp_path):
    target = _setup(monkeypatch, tmp_path)
    result = CliRunner().invoke(main, ["schemas"])
    assert result.exit_code == 0
    events = _events(target)
    assert len(events) == 1
    assert events[0]["command"] == "schemas"
    assert events[0]["outcome"] == "success"


def test_failure_event(monkeypatch, tmp_path):
    target = _setup(monkeypatch, tmp_path)
    result = CliRunner().invoke(main, ["show", "no-such-schema"])
    assert result.exit_code != 0
    events = _events(target)
    assert len(events) == 1
    assert events[0]["command"] == "show"
    assert events[0]["outcome"] == "failure"


def test_two_invocations_two_events(monkeypatch, tmp_path):
    target = _setup(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["schemas"]).exit_code == 0
    assert runner.invoke(main, ["schemas"]).exit_code == 0
    events = _events(target)
    assert len(events) == 2
    assert all(event["outcome"] == "success" for event in events)
