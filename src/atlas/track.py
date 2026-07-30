"""Optional Sentry error tracking — active only when ``SENTRY_DSN`` is set.

Install the extra to use it: ``pip install "atlas-manifest[sentry]"``. Without a DSN
this module is a no-op, so the base install stays dependency-free.
"""

from __future__ import annotations

import os

from atlas import __version__
from atlas.log import RUN_ID, get_logger

logger = get_logger("atlas.track")


def init_error_tracking() -> None:
    """Initialize Sentry if ``SENTRY_DSN`` is set and sentry-sdk is installed."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; run: pip install 'atlas-manifest[sentry]'")
        return
    sentry_sdk.init(
        dsn=dsn,
        release=f"atlas@{__version__}",
        environment=os.environ.get("SENTRY_ENVIRONMENT", "local"),
    )
    sentry_sdk.set_tag("run_id", RUN_ID)
