"""Write an organisation's architecture health-check report.

    flask --app manage health-check-report --org <id> --out <dir>

Writes the report as HTML and PDF into the directory.
"""

import click


def register_health_check_report_command(app):
    @app.cli.command("health-check-report")
    @click.option("--org", "org_id", required=True, type=int, help="Organisation id to report on.")
    @click.option("--out", "out_dir", required=True, type=click.Path(file_okay=False),
                  help="Directory to write the HTML and PDF files into.")
    @click.option("--data", "with_data", is_flag=True, help="Also write the figures as JSON, for the sample page.")
    def health_check_report(org_id, out_dir, with_data):
        """Write the health-check report for one organisation as HTML and PDF."""
        from app import db
        from app.models.organization import Organization
        from app.services.health_check_report_service import write_report

        if db.session.get(Organization, org_id) is None:
            raise click.ClickException("No organisation has id %d." % org_id)
        written = write_report(org_id, out_dir)
        click.echo("Wrote %s" % written["html"])
        click.echo("Wrote %s" % written["pdf"])
        if with_data:
            from pathlib import Path

            from app.services.health_check_report_service import SAMPLE_DATA_FILE, dump_report_data

            data_path = Path(out_dir) / SAMPLE_DATA_FILE
            data_path.write_text(dump_report_data(written["report"]), encoding="utf-8")
            click.echo("Wrote %s" % data_path)
