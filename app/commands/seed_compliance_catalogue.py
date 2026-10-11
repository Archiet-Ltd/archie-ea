"""Flask CLI command that loads the default global compliance catalogue.

Run with: flask seed-compliance-catalogue

Loads ISO 27001, SOC 2, GDPR, NIS2, DORA and the EU AI Act with their controls.
Global master data only; no organisation's data is read or written. Idempotent:
missing frameworks and controls are added, existing rows are never edited, so it
is safe to run on every deploy.
"""

import click
from flask.cli import with_appcontext


@click.command("seed-compliance-catalogue")
@with_appcontext
def seed_compliance_catalogue_command():
    """Load or top up the default compliance framework catalogue."""
    from app.services.compliance.regulatory_framework_service import (
        RegulatoryFrameworkService,
    )

    result = RegulatoryFrameworkService.seed_default_catalogue()
    click.echo(
        "Compliance catalogue {catalogue_version}: {frameworks_added} frameworks added, "
        "{controls_added} controls added".format(**result)
    )


def init_app(app):
    """Register CLI command with app."""
    app.cli.add_command(seed_compliance_catalogue_command)
