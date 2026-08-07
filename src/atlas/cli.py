"""atlas CLI — standalone schema inspection, detection, and validation.

The CLI exposes the package's library functions for humans and scripts while
keeping atlas independent of any data-management engine.
"""

from __future__ import annotations

import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import click
import yaml

from atlas import __version__, analytics, metrics
from atlas.detect import detect as detect_types
from atlas.log import configure_logging, get_logger
from atlas.schema import Schema, SchemaError, discover_schemas, load_all_schemas, resolve_schema
from atlas.survey import gather_survey, render_report
from atlas.track import init_error_tracking
from atlas.validate import RuleResult, validate_data_unit

logger = get_logger(__name__)


class InstrumentedGroup(click.Group):
    """Emit metrics and usage analytics for every command, success or failure."""

    def invoke(self, ctx: click.Context) -> Any:
        metrics.reset()
        start = time.perf_counter()
        outcome = "failure"
        try:
            result = super().invoke(ctx)
            outcome = "success"
            return result
        except click.exceptions.Exit as exc:
            if exc.exit_code == 0:  # --help and friends
                outcome = "success"
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            command = ctx.invoked_subcommand or "main"
            metrics.emit(command, duration_ms, outcome)
            analytics.record_event(command, duration_ms, outcome)


@click.group(cls=InstrumentedGroup)
@click.version_option(version=__version__, prog_name="atlas")
def main() -> None:
    """Map and validate data trees against atlas schemas."""
    configure_logging()
    init_error_tracking()


@main.command("schemas")
def schemas_cmd() -> None:
    """List the built-in schemas."""
    schemas = sorted(load_all_schemas(), key=lambda s: s.name)
    if not schemas:
        click.echo("No built-in schemas found.")
        return
    width = max(len(s.name) for s in schemas)
    for schema in schemas:
        click.echo(f"{schema.name:<{width}}  {schema.version:<6}  {schema.description}")


def _echo_header(schema: Schema) -> None:
    click.echo(f"name:        {schema.name}")
    click.echo(f"version:     {schema.version}")
    if schema.description:
        click.echo(f"description: {schema.description}")


def _echo_detection(schema: Schema) -> None:
    click.echo("detection.markers:")
    for marker in schema.detection.markers:
        click.echo(f"  - {marker}")


def _echo_sync(schema: Schema) -> None:
    click.echo("sync.include:")
    for pattern in schema.sync.include:
        click.echo(f"  - {pattern}")
    if schema.sync.exclude:
        click.echo("sync.exclude:")
        for pattern in schema.sync.exclude:
            click.echo(f"  - {pattern}")


def _echo_validate(schema: Schema) -> None:
    v = schema.validation
    if v is None:
        return
    click.echo("validate:")
    if v.required:
        click.echo(f"  required: {v.required}")
    if v.required_any:
        click.echo(f"  required_any: {v.required_any}")
    if v.required_dirs:
        click.echo(f"  required_dirs: {v.required_dirs}")
    if v.warn_if_missing:
        click.echo(f"  warn_if_missing: {v.warn_if_missing}")
    if v.fail_on:
        click.echo(f"  fail_on: {v.fail_on}")
    if v.min_size_mb is not None or v.max_size_gb is not None:
        click.echo(f"  size: min {v.min_size_mb} MB, max {v.max_size_gb} GB")
    if v.min_file_count is not None or v.max_file_count is not None:
        click.echo(f"  file_count: min {v.min_file_count}, max {v.max_file_count}")
    if v.filename_pattern is not None:
        click.echo(f"  filename_pattern: {v.filename_pattern}")


def _echo_key_outputs(schema: Schema) -> None:
    if not schema.key_outputs:
        return
    click.echo("key_outputs:")
    for out_name, out_path in sorted(schema.key_outputs.items()):
        click.echo(f"  {out_name}: {out_path}")


def _echo_manifest(schema: Schema) -> None:
    if not schema.manifest.records and not schema.manifest.tables:
        return
    click.echo("manifest:")
    for record in schema.manifest.records:
        click.echo(f"  record {record.name}: {record.glob}")
    for table in schema.manifest.tables:
        suffix = " (optional)" if table.optional else ""
        click.echo(f"  table {table.name}: {table.glob}{suffix}")


@main.command("show")
@click.argument("name")
@click.option("--yaml", "as_yaml", is_flag=True, help="Emit normalized, copyable schema YAML.")
def show_cmd(name: str, as_yaml: bool) -> None:
    """Show a schema's anatomy, including metadata manifest rules."""
    try:
        schema = resolve_schema(name, Path.cwd())
    except SchemaError as exc:
        raise click.ClickException(str(exc)) from None

    if as_yaml:
        data = schema.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True)
        click.echo(yaml.safe_dump(data, sort_keys=False), nl=False)
        return

    _echo_header(schema)
    _echo_detection(schema)
    _echo_sync(schema)
    _echo_validate(schema)
    _echo_key_outputs(schema)
    _echo_manifest(schema)


@main.command("detect")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
def detect_cmd(path: Path) -> None:
    """Report which schema(s) match data under PATH, with unit IDs.

    Detects against built-in schemas plus any project-local (``./schemas``)
    or user (``~/.atlas/schemas``) schemas, so a new data type needs only a
    new YAML — no code change.
    """
    try:
        schemas = discover_schemas(project_root=Path.cwd())
    except SchemaError as exc:
        raise click.ClickException(str(exc)) from None
    detections = detect_types(path, schemas=schemas)
    metrics.increment("detections", len(detections))
    logger.debug("detect(%s) found %d detection(s)", path, len(detections))
    if not detections:
        click.echo("No known data types detected.")
        return
    for det in detections:
        rel = det.stage_path
        with suppress(ValueError):
            rel = det.stage_path.relative_to(path.resolve())
        units = ", ".join(det.unit_ids) if det.unit_ids else "(none)"
        click.echo(f"{det.schema_name}\t{rel}\tunits: {units}")


def _echo_validation_finding(result: RuleResult) -> None:
    """Print one legacy finding followed by its structured explanation."""
    prefix = "error" if result.severity == "fail" else "warning"
    click.echo(f"{prefix}: {result.message}", err=True)
    click.echo(f"  received: {result.actual}", err=True)
    click.echo(f"  expected: {result.expected}", err=True)
    click.echo("  generated examples:", err=True)
    if result.examples:
        for example in result.examples:
            click.echo(f"    - {example}", err=True)
    else:
        click.echo("    - (none could be generated)", err=True)


@main.command("validate")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--schema", "schema_name", required=True, help="Schema name or path to validate against.")
def validate_cmd(path: Path, schema_name: str) -> None:
    """Validate a data unit directory against a schema."""
    try:
        schema = resolve_schema(schema_name, Path.cwd())
    except SchemaError as exc:
        raise click.ClickException(str(exc)) from None

    logger.debug("resolved schema '%s' -> '%s' for validating %s", schema_name, schema.name, path)
    result = validate_data_unit(path, schema)
    metrics.increment("errors", len(result.errors))
    metrics.increment("warnings", len(result.warnings))
    for check in result.checks:
        if check.severity == "warn":
            _echo_validation_finding(check)
    for check in result.checks:
        if check.severity == "fail":
            _echo_validation_finding(check)

    if result.passed:
        click.echo(f"OK: {path} is valid against '{schema.name}'.")
    else:
        raise click.ClickException(f"{path} failed validation against '{schema.name}' ({len(result.errors)} error(s)).")


def _stdio_is_tty() -> bool:
    """Whether both ends of the session are terminals (interactive TUI viable)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


@main.command("tui")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path), required=False)
@click.option("--schema", "schema_name", default=None, help="Schema name or path to survey/validate against.")
@click.option("--once", is_flag=True, help="Render a single static report and exit.")
def tui_cmd(path: Path | None, schema_name: str | None, once: bool) -> None:
    """Interactive schema survey: what a schema expects, and what the input got wrong.

    Three modes: `atlas tui --schema NAME` browses the schema's rules with
    no input data (learn mode); `atlas tui PATH --schema NAME` validates one
    data unit and marks each rule pass/warn/fail; `atlas tui PATH` detects
    units under PATH and validates each against the schema that matched it.

    Keys: up/down (or j/k) walk the rule tree, Enter opens the finding
    drill-down for the selected rule, n jumps to the next failing rule,
    r re-validates from disk, q quits.  Renders one static report with
    --once or when stdio is not a terminal.  This is a viewer — for a
    scriptable pass/fail gate use `atlas validate`.
    """
    if path is None and schema_name is None:
        raise click.UsageError("provide a data PATH, --schema NAME, or both")

    try:
        state = gather_survey(path=path, schema_name=schema_name)
    except SchemaError as exc:
        raise click.ClickException(str(exc)) from None

    metrics.increment("units", len(state.units))
    metrics.increment("errors", sum(u.error_count for u in state.units))
    metrics.increment("warnings", sum(u.warning_count for u in state.units))
    logger.debug("tui survey: %d unit(s) under %s", len(state.units), path)

    if once or not _stdio_is_tty():
        click.echo(render_report(state), nl=False)
        return

    try:
        from atlas.tui_app import run_app  # Deferred: textual only loads for interactive use.
    except ImportError:
        raise click.ClickException(
            "interactive mode requires the TUI extra: pip install 'atlas-manifest[tui]'"
        ) from None

    run_app(state, path=path, schema_name=schema_name)


if __name__ == "__main__":
    main()
