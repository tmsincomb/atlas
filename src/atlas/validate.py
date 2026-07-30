"""Validation checks for atlas data units against schema rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from atlas.examples import glob_examples, regex_examples
from atlas.schema import Schema, ValidateConfig, _is_within, get_sync_files

Severity = Literal["ok", "warn", "fail"]


class RuleResult(BaseModel):
    """Outcome of one schema validation rule applied to a data unit.

    Every declared rule emits at least one result — passing rules included —
    so consumers can render a complete checklist, not just the failures.
    ``rule_id`` is a stable join key (e.g. ``"required:report.pdf"``,
    ``"size:min"``); rules that report per-file findings (``filename_pattern``)
    share one ``rule_id`` across their results.
    """

    rule: Literal[
        "required",
        "required_any",
        "required_dirs",
        "fail_on",
        "size",
        "file_count",
        "warn_if_missing",
        "filename_pattern",
    ]
    rule_id: str
    severity: Severity
    expected: str
    actual: str
    examples: list[str] = Field(default_factory=list)
    """Small valid witnesses generated from the rule and verified where applicable."""
    message: str = ""
    """Human-readable finding; exactly the legacy string for warn/fail, empty for ok."""


class ValidationResult(BaseModel):
    """Result of validating a data unit against a schema."""

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sync_files: list[Path] = Field(default_factory=list)
    checks: list[RuleResult] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


def _presence_results(
    unit_path: Path,
    entries: list[str],
    rule: Literal["required", "required_dirs", "warn_if_missing"],
    kind: Literal["file", "dir", "any"],
    severity: Severity,
    missing_message: str,
) -> list[RuleResult]:
    """Shared shape for the three exists-on-disk rules."""
    checks = {"file": Path.is_file, "dir": Path.is_dir, "any": Path.exists}
    results: list[RuleResult] = []
    for entry in entries:
        target = unit_path / entry
        missing = not checks[kind](target) or not _is_within(target, unit_path)
        results.append(
            RuleResult(
                rule=rule,
                rule_id=f"{rule}:{entry}",
                severity=severity if missing else "ok",
                expected=entry,
                actual="missing" if missing else "present",
                examples=[entry],
                message=missing_message.format(entry) if missing else "",
            )
        )
    return results


def _check_required_files(unit_path: Path, cfg: ValidateConfig) -> list[RuleResult]:
    return _presence_results(unit_path, cfg.required, "required", "file", "fail", "Missing required file: {}")


def _check_required_dirs(unit_path: Path, cfg: ValidateConfig) -> list[RuleResult]:
    return _presence_results(
        unit_path, cfg.required_dirs, "required_dirs", "dir", "fail", "Missing required directory: {}"
    )


def _check_required_any(unit_path: Path, cfg: ValidateConfig) -> list[RuleResult]:
    """Require at least one file across a group of alternative globs."""
    if not cfg.required_any:
        return []

    matches = sorted(
        {
            match.relative_to(unit_path).as_posix()
            for pattern in cfg.required_any
            for match in unit_path.glob(pattern)
            if match.is_file() and _is_within(match, unit_path)
        }
    )
    expected = ", ".join(cfg.required_any)
    return [
        RuleResult(
            rule="required_any",
            rule_id="required_any",
            severity="ok" if matches else "fail",
            expected=f"at least one of: {expected}",
            actual=f"matched: {', '.join(matches)}" if matches else "no matches",
            examples=[example for pattern in cfg.required_any for example in glob_examples(pattern, 1)][:3],
            message=f"Missing required alternative (expected at least one of: {expected})" if not matches else "",
        )
    ]


def _check_warn_if_missing(unit_path: Path, cfg: ValidateConfig) -> list[RuleResult]:
    return _presence_results(
        unit_path, cfg.warn_if_missing, "warn_if_missing", "any", "warn", "Optional file missing: {}"
    )


def _check_fail_on(unit_path: Path, cfg: ValidateConfig) -> list[RuleResult]:
    results: list[RuleResult] = []
    for pattern in cfg.fail_on:
        matches = sorted(match for match in unit_path.glob(pattern) if _is_within(match, unit_path))
        matched_names = ", ".join(m.name for m in matches)
        results.append(
            RuleResult(
                rule="fail_on",
                rule_id=f"fail_on:{pattern}",
                severity="fail" if matches else "ok",
                expected="no matches",
                actual=f"matched: {matched_names}" if matches else "no matches",
                examples=[f"no path matching {pattern!r}"],
                message=(
                    f"Pipeline failure marker detected: {matched_names} "
                    f"matched pattern '{pattern}' — pipeline may not have completed successfully"
                    if matches
                    else ""
                ),
            )
        )
    return results


def _bound_result(
    rule: Literal["size", "file_count"],
    bound: Literal["min", "max"],
    violated: bool,
    expected: str,
    actual: str,
    examples: list[str],
    message: str,
) -> RuleResult:
    return RuleResult(
        rule=rule,
        rule_id=f"{rule}:{bound}",
        severity="fail" if violated else "ok",
        expected=expected,
        actual=actual,
        examples=examples,
        message=message if violated else "",
    )


def _check_size(sync_files: list[Path], cfg: ValidateConfig) -> list[RuleResult]:
    total_bytes = 0
    for f in sync_files:
        try:
            total_bytes += f.stat().st_size
        except OSError:
            # File vanished between listing and sizing (TOCTOU); skip it
            # rather than crash, consistent with the collect-all model.
            continue
    total_mb = total_bytes / (1024 * 1024)
    total_gb = total_bytes / (1024 * 1024 * 1024)
    results: list[RuleResult] = []
    if cfg.min_size_mb is not None:
        results.append(
            _bound_result(
                "size",
                "min",
                total_mb < cfg.min_size_mb,
                f">= {cfg.min_size_mb} MB",
                f"{total_mb:.2f} MB",
                [f"{cfg.min_size_mb} MB"],
                f"Total size {total_mb:.2f} MB is below minimum of {cfg.min_size_mb} MB",
            )
        )
    if cfg.max_size_gb is not None:
        results.append(
            _bound_result(
                "size",
                "max",
                total_gb > cfg.max_size_gb,
                f"<= {cfg.max_size_gb} GB",
                f"{total_gb:.4f} GB",
                [f"{cfg.max_size_gb} GB"],
                f"Total size {total_gb:.4f} GB exceeds maximum of {cfg.max_size_gb} GB",
            )
        )
    return results


def _check_file_count(sync_files: list[Path], cfg: ValidateConfig) -> list[RuleResult]:
    count = len(sync_files)
    results: list[RuleResult] = []
    if cfg.min_file_count is not None:
        results.append(
            _bound_result(
                "file_count",
                "min",
                count < cfg.min_file_count,
                f">= {cfg.min_file_count} files",
                f"{count} files",
                [f"{cfg.min_file_count} files"],
                f"File count {count} is below minimum of {cfg.min_file_count}",
            )
        )
    if cfg.max_file_count is not None:
        results.append(
            _bound_result(
                "file_count",
                "max",
                count > cfg.max_file_count,
                f"<= {cfg.max_file_count} files",
                f"{count} files",
                [f"{cfg.max_file_count} files"],
                f"File count {count} exceeds maximum of {cfg.max_file_count}",
            )
        )
    return results


def _check_filename_pattern(sync_files: list[Path], cfg: ValidateConfig) -> list[RuleResult]:
    if cfg.filename_pattern is None:
        return []
    compiled = re.compile(cfg.filename_pattern)
    violations = [f for f in sync_files if not compiled.search(f.name)]
    existing_examples = sorted({f.name for f in sync_files if compiled.search(f.name)})
    examples = list(dict.fromkeys(existing_examples + regex_examples(cfg.filename_pattern)))[:3]
    if not violations:
        return [
            RuleResult(
                rule="filename_pattern",
                rule_id="filename_pattern",
                severity="ok",
                expected=cfg.filename_pattern,
                actual=f"all {len(sync_files)} filenames match",
                examples=examples,
            )
        ]
    return [
        RuleResult(
            rule="filename_pattern",
            rule_id="filename_pattern",
            severity="warn",
            expected=cfg.filename_pattern,
            actual=f.name,
            examples=examples,
            message=f"Filename '{f.name}' does not match expected pattern '{cfg.filename_pattern}'",
        )
        for f in violations
    ]


def validate_data_unit(
    unit_path: str | Path,
    schema: Schema | None,
    project_root: str | Path | None = None,
) -> ValidationResult:
    """Validate a data unit directory against a schema's validation rules.

    Runs checks in order:
      1. required files
      2. required alternatives (at least one glob must match a file)
      3. required dirs
      4. fail_on pattern matching
      5. size bounds (min_size_mb / max_size_gb on post-filter files)
      6. file count bounds (min / max on post-filter files)
      7. warn_if_missing
      8. filename_pattern regex

    All checks are collected (not fail-fast) into ``checks``, one
    :class:`RuleResult` per declared rule item — passing rules included.
    ``errors`` and ``warnings`` are the fail/warn messages in the same
    order, unchanged from before structured results existed.  Size and
    file-count checks use :func:`get_sync_files` for the post-filter list.

    Returns a :class:`ValidationResult` that always passes when
    *schema* is ``None`` or the schema has no ``validate`` section.
    """
    if schema is None:
        return ValidationResult()

    unit_path = Path(unit_path)
    try:
        unit_path = unit_path.resolve()
    except (OSError, RuntimeError):
        # Keep validation structured even for broken paths such as symlink
        # loops.  The lexical absolute path cannot make an unsafe target pass:
        # get_sync_files() returns an empty inventory and _is_within() rejects
        # paths that cannot be resolved.
        unit_path = unit_path.absolute()
    sync_files = get_sync_files(unit_path, schema)
    validate_cfg = schema.validation
    if validate_cfg is None:
        return ValidationResult(sync_files=sync_files)

    checks = (
        _check_required_files(unit_path, validate_cfg)
        + _check_required_any(unit_path, validate_cfg)
        + _check_required_dirs(unit_path, validate_cfg)
        + _check_fail_on(unit_path, validate_cfg)
        + _check_size(sync_files, validate_cfg)
        + _check_file_count(sync_files, validate_cfg)
        + _check_warn_if_missing(unit_path, validate_cfg)
        + _check_filename_pattern(sync_files, validate_cfg)
    )

    return ValidationResult(
        errors=[c.message for c in checks if c.severity == "fail"],
        warnings=[c.message for c in checks if c.severity == "warn"],
        sync_files=sync_files,
        checks=checks,
    )
