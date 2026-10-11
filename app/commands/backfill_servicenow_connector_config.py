"""
flask backfill-servicenow-connector-config -- copy legacy ServiceNow settings
into ConnectorConfig.

M2 of cloud-review-pr448-961a0c35-v1.md: the ServiceNow form used to save
into OrgConnectorConfig (now retired -- its writers raise RuntimeError), and
the connector framework's save path writes ConnectorConfig instead. Any
organisation that configured ServiceNow before that change would otherwise
see its settings vanish from the form and the CMDB/ARB service would read
nothing.

Copies, for every OrgConnectorConfig row with connector_type="servicenow",
instance_url/client_id/field_mapping/enabled into a ConnectorConfig row's
``config`` JSON for the same organisation -- creating one if none exists,
leaving an existing ConnectorConfig row untouched (it already reflects a
real save through the new path, which must win). Never touches the
client_secret -- that already lives in OrgCredentialVault, read the same
way by both the old and new config path, and this command carries no
secrets. The legacy row is left in place, never deleted, so this command is
safe to run more than once.

Usage:
    flask --app manage backfill-servicenow-connector-config
    flask --app manage backfill-servicenow-connector-config --dry-run
"""

from __future__ import annotations

import logging

import click
from flask.cli import with_appcontext

from app import db

logger = logging.getLogger(__name__)


def backfill_servicenow_config(*, dry_run: bool = False) -> dict:
    from app.models.connector_config import ConnectorConfig, OrgConnectorConfig

    legacy_rows = OrgConnectorConfig.query.filter_by(connector_type="servicenow").all()
    created = 0
    already_present = 0

    for legacy in legacy_rows:
        existing = ConnectorConfig.query.filter_by(
            organization_id=legacy.organization_id, connector_type="servicenow"
        ).first()
        if existing is not None:
            already_present += 1
            continue

        if not dry_run:
            cfg = ConnectorConfig(
                organization_id=legacy.organization_id,
                connector_type="servicenow",
                name="ServiceNow CMDB",
                config={
                    "instance_url": legacy.instance_url or "",
                    "client_id": legacy.client_id or "",
                    "field_mapping": legacy.field_mapping or {},
                    "enabled": bool(legacy.enabled),
                },
                status="active" if legacy.enabled else "inactive",
            )
            db.session.add(cfg)
        created += 1

    if not dry_run:
        db.session.commit()

    return {
        "legacy_rows_found": len(legacy_rows),
        "connector_config_rows_created": created,
        "already_had_a_connector_config_row": already_present,
    }


@click.command("backfill-servicenow-connector-config")
@click.option("--dry-run", is_flag=True, help="Report what would change without writing.")
@with_appcontext
def backfill_servicenow_connector_config_command(dry_run):
    """Copy legacy OrgConnectorConfig ServiceNow settings into ConnectorConfig."""
    click.echo("backfill-servicenow-connector-config:" + (" (dry-run)" if dry_run else ""))
    stats = backfill_servicenow_config(dry_run=dry_run)
    click.echo("\n  Results:")
    for key, value in stats.items():
        click.echo(f"    {key}: {value}")
    click.echo("Done.")


def init_app(app):
    """Register the backfill-servicenow-connector-config CLI command."""
    app.cli.add_command(backfill_servicenow_connector_config_command)
