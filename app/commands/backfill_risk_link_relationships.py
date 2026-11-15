"""Give every existing risk link its association on the model.

``risk_entity_links`` is the one record of what a risk touches. Each link is also drawn on the model
as an association from the risk's Assessment element to the element it points at, so the element
lists what it is connected to and impact analysis can follow it. New links draw their own
association (app/services/risk_service.py); this command draws the ones made before that.

Idempotent: a link whose association exists is left alone.

    flask --app manage backfill-risk-link-relationships --dry-run
    flask --app manage backfill-risk-link-relationships
    flask --app manage backfill-risk-link-relationships --organization-id 7
"""
import click
from flask.cli import with_appcontext


def init_app(app):
    app.cli.add_command(backfill_risk_link_relationships)


@click.command("backfill-risk-link-relationships")
@click.option("--dry-run", is_flag=True, help="Report how many would be drawn; write nothing.")
@click.option("--organization-id", type=int, default=None, help="Only this organisation.")
@with_appcontext
def backfill_risk_link_relationships(dry_run, organization_id):
    """Draw each risk link that has no association on the model."""
    from app.commands.deploy_scope import require_platform_scope
    from app.extensions import db
    from app.services import risk_service

    require_platform_scope(db.session.connection(), "backfill-risk-link-relationships")
    seen, drawn = risk_service.project_all_links(organization_id=organization_id, dry_run=dry_run)
    verb = "would draw" if dry_run else "drew"
    click.echo(f"{seen} risk links checked; {verb} {drawn} associations.")
