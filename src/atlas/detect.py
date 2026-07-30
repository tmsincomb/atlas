"""On-disk detection of known data types by schema markers.

Given a directory tree, match it against schema ``detection`` blocks and
report which data type each stage directory is, plus its unit IDs.  All the
layout knowledge (instrument/assay landmarks, unit depth, disambiguation
guards) lives in the schema YAML — this module is a single generic engine
that reads those declarative rules, so a new data type needs only a new
schema, no Python change.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from atlas.schema import CmdlineSubcommandGuard, DetectionConfig, Schema, _is_within, load_all_schemas


class Detection(BaseModel):
    """One detected stage: its type, on-disk location, and unit IDs.

    ``stage_path`` is absolute (resolved).  Forest maps this into its own
    config shape (relative paths, remote layout); atlas only reports facts.
    """

    schema_name: str
    stage_path: Path
    unit_ids: list[str] = Field(default_factory=list)
    sync_by: str = "subdirectory"
    unit_is_directory_stage: bool = False
    """True when each unit is itself a complete directory dataset, so a tool may
    point directly at a unit and bind it as a ``directory``-synced stage."""


def _landmark_type_ok(hit: Path, landmark_type: str) -> bool:
    """Whether *hit* satisfies the configured ``landmark_type``."""
    if landmark_type == "file":
        return hit.is_file()
    if landmark_type == "dir":
        return hit.is_dir()
    return True


def _check_all_markers(unit_dir: Path, markers: list[str]) -> bool:
    """Verify ALL detection markers exist under *unit_dir*."""
    return all((unit_dir / marker).exists() and _is_within(unit_dir / marker, unit_dir) for marker in markers)


def _any_glob_matches(unit_dir: Path, patterns: list[str]) -> bool:
    """Whether at least one *patterns* glob matches a file under *unit_dir*."""
    if not patterns:
        return True
    return any(
        match.is_file() and _is_within(match, unit_dir) for pattern in patterns for match in unit_dir.glob(pattern)
    )


def _has_any_marker(unit_dir: Path, markers: list[str]) -> bool:
    """Whether ANY of *markers* exists under *unit_dir*."""
    return any((unit_dir / marker).exists() and _is_within(unit_dir / marker, unit_dir) for marker in markers)


def _cmdline_subcommand(unit_dir: Path, guard: CmdlineSubcommandGuard) -> str | None:
    """Return the subcommand recorded in *guard.file*, or ``None``.

    Tokenises the file, finds the token whose basename equals ``guard.tool``,
    and returns the following token.  Returns ``None`` when the file is
    missing/unreadable or the tool is not named.  This makes
    ``bundler build --out workspace-dir`` resolve to ``"build"`` (the token
    after ``bundler``), never ``"workspace"`` from an unrelated argument.
    """
    cmdline = unit_dir / guard.file
    if not cmdline.is_file() or not _is_within(cmdline, unit_dir):
        return None
    try:
        tokens = cmdline.read_text().split()
    except OSError:
        return None
    for i, token in enumerate(tokens):
        if Path(token).name == guard.tool and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def _excluded_by_cmdline(unit_dir: Path, guard: CmdlineSubcommandGuard | None) -> bool:
    """Whether the cmdline guard rules this unit out."""
    if guard is None:
        return False
    return _cmdline_subcommand(unit_dir, guard) == guard.subcommand


def _unit_dir_for_hit(root: Path, hit: Path, depth: int) -> Path | None:
    """Return a directory-valued unit inside *root*, or ``None``."""
    unit_dir = hit
    for _ in range(depth):
        unit_dir = unit_dir.parent
    try:
        if not unit_dir.is_dir():
            return None
        unit_dir.resolve().relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return unit_dir


def _detect_schema(root: Path, schema: Schema) -> list[tuple[Path, list[str]]]:
    """Run one schema's declarative detection block over *root*.

    Returns ``(stage_path, unit_ids)`` pairs with unit IDs aggregated per
    stage directory.
    """
    cfg: DetectionConfig = schema.detection
    if cfg.landmark is None:
        return []

    matched: dict[Path, list[str]] = defaultdict(list)
    for hit in root.rglob(cfg.landmark):
        if not _is_within(hit, root) or not _landmark_type_ok(hit, cfg.landmark_type):
            continue
        if cfg.landmark_parent is not None and hit.parent.name != cfg.landmark_parent:
            continue

        # A schema must never turn a landmark into a file-valued unit or walk
        # above the root the caller asked us to inspect.
        unit_dir = _unit_dir_for_hit(root, hit, cfg.unit_depth)
        if unit_dir is None:
            continue

        if _has_any_marker(unit_dir, cfg.exclude_if_markers):
            continue
        if _excluded_by_cmdline(unit_dir, cfg.exclude_if_cmdline_subcommand):
            continue
        if not _check_all_markers(unit_dir, cfg.markers):
            continue
        if not _any_glob_matches(unit_dir, cfg.require_any_glob):
            continue

        matched[unit_dir.parent].append(unit_dir.name)

    return [(stage, sorted(set(ids))) for stage, ids in sorted(matched.items(), key=lambda item: item[0].as_posix())]


def detect(root: Path | str, schemas: list[Schema] | None = None) -> list[Detection]:
    """Detect known data types under *root*.

    Runs every schema's declarative detection block over *root* and returns
    one :class:`Detection` per (schema, stage directory) found, with absolute
    ``stage_path``.  *schemas* defaults to the built-ins (sorted by name for
    deterministic output); pass a list to detect against custom schemas.
    Deduplication and relative/remote path layout are the caller's concern
    (forest).  Returns ``[]`` when *root* is not a directory.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        return []

    if schemas is None:
        schemas = sorted(load_all_schemas(), key=lambda s: s.name)

    out: list[Detection] = []
    for schema in schemas:
        for stage_path, unit_ids in _detect_schema(root, schema):
            out.append(
                Detection(
                    schema_name=schema.name,
                    stage_path=stage_path,
                    unit_ids=unit_ids,
                    sync_by=schema.detection.sync_by,
                    unit_is_directory_stage=schema.detection.unit_is_directory_stage,
                )
            )
    return out


def extract_unit_ids(path: Path | str) -> list[str]:
    """Extract unit IDs from a stage directory.

    For subdirectory-based schemas, unit IDs are child directory names.
    Returns a sorted list of directory names found at *path*.
    """
    path = Path(path)
    if not path.is_dir():
        return []
    return sorted(d.name for d in path.iterdir() if d.is_dir())
