"""Tests for atlas.metrics."""

from __future__ import annotations

import json
import logging

from atlas import metrics
from atlas.log import RedactionFilter


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture_emit(command="detect", duration_ms=12.34, outcome="success"):
    # Attach a handler directly: configure_logging() may have set the "atlas"
    # logger to propagate=False, so caplog's root handler never sees records.
    logger = logging.getLogger("atlas.metrics")
    handler = _ListHandler()
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        metrics.emit(command, duration_ms, outcome)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    assert len(handler.records) == 1
    return handler.records[0]


def test_counter_round_trip():
    metrics.reset()
    metrics.increment("things")
    metrics.increment("things", 2)
    assert metrics.snapshot() == {"things": 3}
    metrics.reset()
    assert metrics.snapshot() == {}


def test_emit_json_shape():
    metrics.reset()
    metrics.increment("detections", 5)
    record = _capture_emit()
    payload = json.loads(record.getMessage())
    assert payload["event"] == "metrics"
    assert payload["command"] == "detect"
    assert payload["duration_ms"] == 12.3
    assert payload["outcome"] == "success"
    assert payload["counters"] == {"detections": 5}


def test_emit_survives_redaction():
    metrics.reset()
    metrics.increment("errors", 2)
    record = _capture_emit(command="validate", outcome="failure")
    assert RedactionFilter().filter(record) is True
    msg = record.getMessage()
    assert "***" not in msg
    assert json.loads(msg)["counters"] == {"errors": 2}
