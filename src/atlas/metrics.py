"""In-process metrics — counters plus one structured summary line per CLI run.

Counters accumulate while a command runs and are emitted as a single JSON log
line on the ``atlas.metrics`` logger at INFO, so ``ATLAS_LOG_LEVEL=INFO
ATLAS_LOG_FORMAT=json`` yields machine-readable metrics with the run id
attached by the formatter.
"""

from __future__ import annotations

import json

from atlas.log import get_logger

logger = get_logger("atlas.metrics")

_counters: dict[str, int] = {}


def increment(name: str, value: int = 1) -> None:
    """Add ``value`` to the named counter."""
    _counters[name] = _counters.get(name, 0) + value


def snapshot() -> dict[str, int]:
    """Return a copy of the current counters."""
    return dict(_counters)


def reset() -> None:
    """Clear all counters; the CLI calls this at the start of every invocation."""
    _counters.clear()


def emit(command: str, duration_ms: float, outcome: str) -> None:
    """Log one JSON metrics line for the finished command.

    The message omits RUN_ID: RedactionFilter masks long alphanumeric runs,
    and JsonFormatter re-attaches ``run_id`` after filtering.
    """
    payload = {
        "event": "metrics",
        "command": command,
        "duration_ms": round(duration_ms, 1),
        "outcome": outcome,
        "counters": snapshot(),
    }
    logger.info(json.dumps(payload))
