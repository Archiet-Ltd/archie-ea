"""``flask reference-packs load`` — the one CLI entry point onto pack_loader.py.

No ``publish``, ``draft`` or ``expire-drafts`` subcommand here: this task only
loads the thirteen folded packs as published records. The curator workflow is
a later task's addition.
"""
import click
from flask.cli import with_appcontext


@click.group("reference-packs")
def reference_packs_group():
    """Manage global reference pack content."""


@reference_packs_group.command("load")
@click.option("--dry-run", is_flag=True, help="Validate and report without writing.")
@click.option("--root", "root", default=None, help="Override the pack file root directory.")
@with_appcontext
def load_command(dry_run, root):
    """Load every pack file into the reference_packs table and refresh the projection."""
    from pathlib import Path

    from app.modules.reference_packs.services.pack_loader import load_packs

    report = load_packs(Path(root) if root else None, dry_run=dry_run)

    click.echo(f"packs loaded: {report.packs_loaded}")
    click.echo(f"packs unchanged: {report.packs_skipped_unchanged}")
    click.echo(f"sources: {report.sources_counted}")
    click.echo(
        f"projection rows: {report.projection_rows_written} written, "
        f"{report.projection_rows_removed} removed"
    )
    click.echo(f"errors: {len(report.errors)}")
    for err in report.errors:
        click.echo(f"  - {err}", err=True)


def init_app(app):
    """Register the reference-packs CLI command group."""
    app.cli.add_command(reference_packs_group)
