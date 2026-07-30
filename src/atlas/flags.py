"""Feature flags — env-driven overrides over a registry of known flags.

Enable a flag with ``ATLAS_FLAG_<NAME>=1`` (also: true/yes/on, case-insensitive).
"""

from __future__ import annotations

import os

# name -> default. Register new flags here so unknown names fail loudly.
FLAGS: dict[str, bool] = {
    "analytics": False,
}

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_enabled(name: str) -> bool:
    """Return the flag value: ``ATLAS_FLAG_<NAME>`` env override, else the registry default."""
    default = FLAGS[name]
    raw = os.environ.get(f"ATLAS_FLAG_{name.upper()}")
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY
