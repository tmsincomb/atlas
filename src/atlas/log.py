"""Structured logging for atlas — stdlib only, JSON or text, secret-redacting.

A per-process ``RUN_ID`` ties every line of one invocation together.  Format
and level are read from the environment so scripts and agents can turn on JSON
logs without code changes:

    ATLAS_LOG_LEVEL=DEBUG ATLAS_LOG_FORMAT=json atlas detect .
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid

RUN_ID = uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    """Emit each record as a one-line JSON object with the run id."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": RUN_ID,
        }
        return json.dumps(payload)


class RedactionFilter(logging.Filter):
    """Mask secret-looking values before they reach a handler."""

    # key=value / key: value where the key names a credential.
    _KV = re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|key)(\s*[=:]\s*)(\S+)")
    # ponytail: long hex/base64 run heuristic; tighten the length if it over-masks.
    _BLOB = re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = self._KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***", record.getMessage())
        record.msg = self._BLOB.sub("***", msg)
        record.args = ()
        return True


def configure_logging() -> None:
    """Configure the ``atlas`` logger from ATLAS_LOG_LEVEL / ATLAS_LOG_FORMAT."""
    level = os.environ.get("ATLAS_LOG_LEVEL", "WARNING").upper()
    fmt = os.environ.get("ATLAS_LOG_FORMAT", "text").lower()

    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger("atlas")
    root.setLevel(getattr(logging, level, logging.WARNING))
    root.handlers = [handler]
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``atlas`` namespace."""
    return logging.getLogger(name)
