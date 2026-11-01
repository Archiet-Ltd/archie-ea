"""RATA-003: CLI commands for rationalization scoring."""

import click


@click.group("rationalization")
def rationalization_cli():
    """Rationalization scoring commands."""


@rationalization_cli.command("score-all")
@click.option("--organization-id", type=int, required=True, help="Organisation whose applications are scored.")
@click.option("--force", is_flag=True, help="Force recalculate all scores (ignore 30-day cache).")
def cli_score_all(organization_id, force):
    """Score the entire application portfolio using the TIME framework."""
    from app.services.rationalization_scoring_service import RationalizationScoringService

    click.echo("Starting portfolio scoring...")
    results = RationalizationScoringService.calculate_portfolio_scores(
        organization_id, force_recalculate=force
    )

    if results.get("success"):
        click.echo(
            f"Done. Scored: {results['processed']}/{results['total_apps']}, "
            f"Errors: {results['errors']}"
        )
        dist = results.get("time_distribution", {})
        if any(dist.values()):
            click.echo(
                f"  TIME: T={dist.get('TOLERATE', 0)} I={dist.get('INVEST', 0)} "
                f"M={dist.get('MIGRATE', 0)} E={dist.get('ELIMINATE', 0)}"
            )
    else:
        click.echo(f"Scoring failed: {results.get('error', 'unknown')}")
        raise SystemExit(1)


def register_rationalization_commands(app):
    """Register rationalization CLI commands with Flask app."""
    app.cli.add_command(rationalization_cli)
