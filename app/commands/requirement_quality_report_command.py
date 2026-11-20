"""Requirement-quality / EARS check (report mode) — founder's 20 Nov 2026
ruling that EARS is the mandatory statement form for every requirement, plus
the agreed ISO/IEC/IEEE 29148 + INCOSE attributes.

Report mode only: measures the backfill backlog, changes nothing. See
``app/services/requirement_quality_report_service.py`` for what each
attribute actually is (structured column vs. free text) and why.

    flask --app manage requirement-quality-report --org-id 7
    flask --app manage requirement-quality-report          # all organisations
"""

import click
from flask.cli import with_appcontext


@click.command("requirement-quality-report")
@click.option(
    "--org-id", type=int, default=None, help="Restrict to a single organization."
)
@with_appcontext
def requirement_quality_report(org_id):
    """Print EARS/ISO-29148 compliance counts for Requirement elements.

    Run outside a request context, so there is no ``g.current_org_id``; with
    no ``--org-id`` the counts are computed across every organization in the
    database and the report says so explicitly rather than silently scoping
    to one.
    """
    from app.services.requirement_quality_report_service import (
        compute_requirement_quality_report,
    )

    report = compute_requirement_quality_report(organization_id=org_id)

    scope = f"organization {org_id}" if org_id is not None else "all organizations (no tenant scope outside a request)"
    click.echo(f"Requirement-quality / EARS report — {scope}")
    click.echo("-" * 70)
    click.echo(f"Total requirements: {report['total']}")
    click.echo(f"Not EARS-compliant statement: {report['not_ears_count']}")
    click.echo(f"Missing verification method: {report['missing_verification_count']}")
    click.echo(f"Missing a trace (Stakeholder/Driver/Goal): {report['missing_trace_count']}")
    click.echo(f"Missing a kind: {report['missing_kind_count']}")
    click.echo(f"Non-compliant overall (fails at least one check): {report['non_compliant_count']}")

    if report["non_compliant_names"]:
        click.echo("")
        click.echo("Non-compliant requirements:")
        for name in report["non_compliant_names"]:
            click.echo(f"  - {name}")


def init_app(app):
    app.cli.add_command(requirement_quality_report)
