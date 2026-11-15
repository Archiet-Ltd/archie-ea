"""Backfill: give each backlog requirement written before projection ran its ranked record.

New and edited backlog requirements get theirs when they are written. This covers
the ones that predate that, one organisation at a time. Idempotent.

    flask --app manage project-backlog-requirements
"""

import click
from flask.cli import with_appcontext

from app import db


@click.command("project-backlog-requirements")
@with_appcontext
def project_backlog_requirements_command():
    """Project every organisation's backlog requirements that have no ranked record."""
    from app.models.organization import Organization
    from app.services.prioritisation_service import project_backlog_requirements

    total = 0
    for organization_id in [row[0] for row in db.session.query(Organization.id).all()]:
        db.session.remove()
        added = project_backlog_requirements(organization_id)
        db.session.commit()
        total += added
    click.echo(f"project-backlog-requirements: {total} backlog requirement(s) projected.")


def init_app(app):
    """Register the project-backlog-requirements CLI command."""
    app.cli.add_command(project_backlog_requirements_command)
