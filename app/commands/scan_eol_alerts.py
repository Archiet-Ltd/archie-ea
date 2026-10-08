"""flask scan-eol-alerts -- R1-B85 PB-0043, run the end-of-support scan for
every organisation. Scheduled job entry point; see
app.modules.intelligence.services.deadline_alert_service for the scan logic
itself (this command is wiring only).
"""
import click
from flask.cli import with_appcontext


@click.command("scan-eol-alerts")
@with_appcontext
def scan_eol_alerts():
    from app.modules.intelligence.services.deadline_alert_service import (
        scan_all_organizations_for_eol_alerts,
    )

    results = scan_all_organizations_for_eol_alerts()
    total_created = sum(len(r["created"]) for r in results.values())
    click.echo(
        f"  scanned {len(results)} organisation(s), created {total_created} alert(s)."
    )


def init_app(app):
    app.cli.add_command(scan_eol_alerts)
