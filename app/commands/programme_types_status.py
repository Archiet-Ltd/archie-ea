"""`flask programme-types status` -- reads template files only, never tenant
data (security.md 5.4), and prints M1 (valid-and-reviewed count against 12)
and M10 (12 minus the reviewed count, listing which keys are stale or
missing a review).
"""

from __future__ import annotations

import click
from flask.cli import with_appcontext


@click.command("programme-types")
@click.argument("action", type=click.Choice(["status"]))
@with_appcontext
def programme_types(action):
    """Report the review state of every programme type template file."""
    from app.modules.transformation_room.programme_types.loader import ProgrammeTypeCatalogue
    from app.modules.transformation_room.programme_types.validator import MANIFEST_SIZE

    results = ProgrammeTypeCatalogue().load_all()
    reviewed = [r for r in results if r.valid and r.reviewed]
    outstanding = [r for r in results if not (r.valid and r.reviewed)]

    click.echo(f"M1 (offered): {len(reviewed)} of {MANIFEST_SIZE} valid and reviewed")
    click.echo(f"M10 (outstanding): {MANIFEST_SIZE - len(reviewed)}")
    for result in outstanding:
        if not result.valid:
            state = f"invalid ({len(result.errors)} error(s))"
        else:
            state = "not reviewed (absent or stale)"
        click.echo(f"  - {result.key}: {state}")

    offered = len(reviewed) == MANIFEST_SIZE and len(results) == MANIFEST_SIZE
    click.echo(f"offered_types() would return: {'12' if offered else '0 (all-or-nothing)'}")


def init_app(app):
    """Register the programme-types CLI command group."""
    app.cli.add_command(programme_types)
