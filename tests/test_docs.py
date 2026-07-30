"""Docs guard: AGENTS.md at the repo root must document every CLI command."""

from __future__ import annotations

from pathlib import Path

from atlas.cli import main

AGENTS_MD = Path(__file__).resolve().parent.parent / "AGENTS.md"


def test_agents_md_exists():
    assert AGENTS_MD.is_file(), "AGENTS.md missing at repo root"


def test_agents_md_mentions_every_command():
    text = AGENTS_MD.read_text()
    for command in main.commands:
        assert command in text, f"AGENTS.md does not mention command '{command}'"
