"""Tests for atlas.track."""

from __future__ import annotations

import sys
import types

from atlas import __version__
from atlas.log import RUN_ID
from atlas.track import init_error_tracking


def test_no_dsn_is_noop(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    init_error_tracking()


def test_dsn_initializes_sentry(monkeypatch):
    calls = {}
    stub = types.SimpleNamespace(
        init=lambda **kwargs: calls.setdefault("init", kwargs),
        set_tag=lambda key, value: calls.setdefault("tag", (key, value)),
    )
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", stub)
    init_error_tracking()
    assert calls["init"]["dsn"] == "https://public@example.invalid/1"
    assert calls["init"]["release"] == f"atlas@{__version__}"
    assert calls["tag"] == ("run_id", RUN_ID)


def test_missing_sdk_warns_not_raises(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    # None in sys.modules forces ImportError on `import sentry_sdk`.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    init_error_tracking()
