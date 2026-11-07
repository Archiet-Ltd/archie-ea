"""Re-score the consolidation pairs still waiting for review.

Pairs proposed before the overlap rule (a shared capability, a shared business
function or a near-identical name) are brought in line with it: a pair the rule
supports takes its computed score and stated reason, a pair nothing supports is
dismissed with that explanation. Decided pairs are left alone. Idempotent.

    flask --app manage rescore-consolidation-candidates
"""

import click
from flask.cli import with_appcontext


@click.command("rescore-consolidation-candidates")
@with_appcontext
def rescore_consolidation_candidates_command():
    """Re-score every consolidation pair waiting for review."""
    from app.services.consolidation_service import ConsolidationService

    counts = ConsolidationService.rescore_pending_candidates()
    click.echo(
        "rescore-consolidation-candidates: %(rescored)d re-scored, %(dismissed)d dismissed, "
        "%(unchanged)d unchanged." % counts
    )


def init_app(app):
    """Register the rescore-consolidation-candidates CLI command."""
    app.cli.add_command(rescore_consolidation_candidates_command)
