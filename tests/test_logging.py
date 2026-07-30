"""Tests for atlas.log: JSON formatting, secret redaction, env-driven config."""

from __future__ import annotations

import json
import logging

from atlas.log import RUN_ID, JsonFormatter, RedactionFilter, configure_logging


def _record(msg: str, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord("atlas.test", level, __file__, 1, msg, None, None)


def test_json_formatter_emits_parseable_json_with_run_id():
    payload = json.loads(JsonFormatter().format(_record("hello world")))
    assert payload["message"] == "hello world"
    assert payload["run_id"] == RUN_ID
    assert payload["level"] == "INFO"
    assert payload["logger"] == "atlas.test"
    assert "timestamp" in payload


def test_redaction_filter_masks_key_value_secrets():
    rec = _record("connecting with password=hunter2 token=abc123")
    RedactionFilter().filter(rec)
    masked = rec.getMessage()
    assert "hunter2" not in masked
    assert "abc123" not in masked
    assert "***" in masked


def test_redaction_filter_masks_long_blob():
    secret = "a1b2c3d4" * 5  # 40 chars, looks like a hex/base64 token
    rec = _record(f"leaked {secret} end")
    RedactionFilter().filter(rec)
    assert secret not in rec.getMessage()


def test_configure_logging_respects_env_level(monkeypatch):
    monkeypatch.setenv("ATLAS_LOG_LEVEL", "DEBUG")
    configure_logging()
    assert logging.getLogger("atlas").level == logging.DEBUG

    monkeypatch.setenv("ATLAS_LOG_LEVEL", "ERROR")
    configure_logging()
    assert logging.getLogger("atlas").level == logging.ERROR


def test_configure_logging_json_format(monkeypatch):
    monkeypatch.setenv("ATLAS_LOG_FORMAT", "json")
    configure_logging()
    handler = logging.getLogger("atlas").handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)
