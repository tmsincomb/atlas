"""Privacy guards: the pubID controls must exist, block real values, and pass synthetic ones.

Forbidden values are assembled from fragments at runtime so no real-shaped
participant identifier is ever committed to this repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"

SKIPPED_TREE_DIRS = {
    ".git",
    ".venv",
    "site",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    # Holds parametrized node IDs, which embed this module's assembled test values.
    ".pytest_cache",
}

# Assembled, never written out whole: a real-shaped pubID and its visit variant.
FORBIDDEN = ["G00" + "2-" + "1" + "23", "G00" + "3-" + "7" + "4-002"]
# Fixture namespace: a digit 0 directly after the study code marks a synthetic value.
ALLOWED = ["G002-091", "G003-08-004", "G002", "/data/G003/sorting"]


def _pre_commit_config() -> dict[str, Any]:
    return yaml.safe_load(PRE_COMMIT_CONFIG.read_text())


def _pubid_hook() -> dict[str, Any]:
    for repo in _pre_commit_config()["repos"]:
        for hook in repo["hooks"]:
            if hook["id"] == "no-real-pubids":
                return hook
    raise AssertionError("no-real-pubids hook missing from .pre-commit-config.yaml")


def test_pubid_hook_runs_on_staged_files_and_commit_messages():
    assert _pre_commit_config()["default_install_hook_types"] == ["pre-commit", "commit-msg"]
    assert _pubid_hook()["stages"] == ["pre-commit", "commit-msg"]


@pytest.mark.parametrize("value", FORBIDDEN, ids=["pubid", "pubid-with-visit"])
def test_pubid_hook_rejects_real_shaped_values(value: str):
    assert re.search(_pubid_hook()["entry"], value)


@pytest.mark.parametrize("value", ALLOWED)
def test_pubid_hook_accepts_synthetic_values(value: str):
    assert re.search(_pubid_hook()["entry"], value) is None


def test_gitleaks_config_declares_a_participant_rule():
    tomllib = pytest.importorskip("tomllib")
    config = tomllib.loads(GITLEAKS_CONFIG.read_text())
    assert config["extend"]["useDefault"] is True
    rules = {rule["id"]: rule["regex"] for rule in config["rules"]}
    pattern = rules["participant-pubid"]
    assert all(re.search(pattern, value) for value in FORBIDDEN)
    assert not any(re.search(pattern, value) for value in ALLOWED)


def test_gitleaks_config_holds_no_literal_identifier():
    assert re.search(r"G\d{3}-[1-9]", GITLEAKS_CONFIG.read_text()) is None


def test_working_tree_holds_no_real_shaped_pubids():
    offenders = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or SKIPPED_TREE_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(r"G\d{3}-[1-9]", text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"participant-shaped identifiers in: {offenders}"
