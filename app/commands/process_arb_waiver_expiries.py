"""CLI entry point for bounded ARB condition waiver expiry."""

from __future__ import annotations

import json

import click
from flask.cli import with_appcontext

from app.jobs.tenant_safe_job import platform_scope

from app.modules.transformation_room.arb_waiver_expiry_batch_service import (
    ARBWaiverExpiryBatchService,
)


@click.command("process-arb-waiver-expiries")
@click.option(
    "--organization-id",
    "organization_ids",
    type=click.IntRange(min=1),
    multiple=True,
    required=True,
    help="Organization to process; repeat for each explicitly authorized tenant.",
)
@click.option(
    "--batch-size",
    type=click.IntRange(min=1, max=ARBWaiverExpiryBatchService.MAX_BATCH_SIZE),
    default=ARBWaiverExpiryBatchService.DEFAULT_BATCH_SIZE,
    show_default=True,
)
@with_appcontext
def process_arb_waiver_expiries(organization_ids, batch_size):
    """Expire due typed ARB condition waivers for explicit organizations."""
    # One locked batch over the named organisations; every statement in it names
    # its organization_id. Without this scope the row-level-security policies
    # hide every due waiver from the runtime role (as the scheduler path does).
    with platform_scope("typed ARB waiver expiry: the named organisations in one locked batch"):
        result = ARBWaiverExpiryBatchService.run(
            organization_ids=organization_ids, batch_size=batch_size
        )
    click.echo(json.dumps(result.as_dict(), sort_keys=True))
    if result.failed_count:
        raise click.exceptions.Exit(1)


def init_app(app):
    app.cli.add_command(process_arb_waiver_expiries)


__all__ = ["init_app", "process_arb_waiver_expiries"]
