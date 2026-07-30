"""Tests for atlas.flags."""

from __future__ import annotations

import pytest

from atlas.flags import FLAGS, is_enabled


def test_default_off(monkeypatch):
    monkeypatch.delenv("ATLAS_FLAG_ANALYTICS", raising=False)
    assert is_enabled("analytics") is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on", " True "])
def test_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ATLAS_FLAG_ANALYTICS", value)
    assert is_enabled("analytics") is True


@pytest.mark.parametrize("value", ["0", "false", "", "banana"])
def test_falsy_values(monkeypatch, value):
    monkeypatch.setenv("ATLAS_FLAG_ANALYTICS", value)
    assert is_enabled("analytics") is False


def test_unknown_flag_raises():
    with pytest.raises(KeyError):
        is_enabled("nope")


def test_registry_defaults_are_bools():
    assert all(isinstance(default, bool) for default in FLAGS.values())
