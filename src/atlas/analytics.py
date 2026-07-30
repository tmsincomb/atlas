"""Opt-in local usage analytics — one JSONL event per CLI run.

Off by default; enable with ``ATLAS_FLAG_ANALYTICS=1``. Events append to
``ATLAS_ANALYTICS_FILE`` (default ``~/.atlas/analytics.jsonl``) and never
leave the machine.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from atlas import flags
from atlas.log import RUN_ID, get_logger

logger = get_logger("atlas.analytics")


def analytics_path() -> Path:
    """Return the JSONL events file path."""
    override = os.environ.get("ATLAS_ANALYTICS_FILE")
    if override:
        return Path(override)
    return Path.home() / ".atlas" / "analytics.jsonl"


def record_event(command: str, duration_ms: float, outcome: str) -> None:
    """Append one usage event if the analytics flag is enabled."""
    if not flags.is_enabled("analytics"):
        return
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": RUN_ID,
        "command": command,
        "duration_ms": round(duration_ms, 1),
        "outcome": outcome,
    }
    path = analytics_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError as exc:
        # Analytics must never break the CLI.
        logger.debug("analytics write to %s failed: %s", path, exc)
