"""``flask purge-cross-tenant-copilot-insights`` — delete cross-tenant insight rows.

``portfolio_duplicate`` and ``pattern_available`` insights quote other solutions
by name. Before the tenant-scope fix they could name solutions from other
organisations. This command deletes only the insights whose named solution
belongs to a different organisation than the insight's own solution; legitimate
within-organisation insights are left untouched.

The analysis stores the referenced solution by name (quoted) in the insight
text, not by id, so the referenced solution is resolved by exact name match
against the solutions table.

It is a one-off housekeeping command, not a scheduled job. Dry-run by default;
deletes only with ``--apply``.
"""

import re

import click

_QUOTED_NAME = re.compile(r'"([^"]+)"')
_TARGET_TYPES = ("portfolio_duplicate", "pattern_available")


def _named_solution_org_ids(insight, own_solution):
    """Return the organisation ids of solutions an insight names that are
    definitively foreign — i.e. the quoted name matches no solution in the
    insight's own organisation but does match a solution in another organisation.

    A name that exists in both the insight's own organisation and another
    organisation is treated as a legitimate within-organisation reference and
    contributes no org_ids.
    """
    from app.models.solution_models import Solution

    text = " ".join(
        part for part in (insight.title, insight.body, insight.suggested_query) if part
    )
    names = set(_QUOTED_NAME.findall(text))
    org_ids = set()
    for name in names:
        all_matches = Solution.query.filter(Solution.name == name).all()
        # Does this name match any solution in the insight's own organisation
        # (other than the insight's own solution)?
        has_own_org_match = any(
            s.id != own_solution.id
            and s.organization_id == own_solution.organization_id
            for s in all_matches
        )
        if has_own_org_match:
            # The name resolves within the insight's own organisation — not cross-tenant.
            continue
        # No own-org match: any match in another organisation is cross-tenant.
        for solution in all_matches:
            if solution.id != own_solution.id and solution.organization_id is not None:
                org_ids.add(solution.organization_id)
    return org_ids


def _cross_tenant_insights():
    """Return cross-tenant insights of the two quoting types, grouped by type."""
    from app.models.copilot_insight import CopilotInsight
    from app.models.solution_models import Solution

    by_type = {insight_type: [] for insight_type in _TARGET_TYPES}
    insights = CopilotInsight.query.filter(
        CopilotInsight.insight_type.in_(_TARGET_TYPES)
    ).all()

    for insight in insights:
        own = Solution.query.get(insight.solution_id)
        if own is None or own.organization_id is None:
            continue
        named_org_ids = _named_solution_org_ids(insight, own)
        if any(org_id != own.organization_id for org_id in named_org_ids):
            by_type[insight.insight_type].append(insight)

    return by_type


def init_app(app):
    @app.cli.command("purge-cross-tenant-copilot-insights")
    @click.option(
        "--apply",
        is_flag=True,
        default=False,
        help="Actually delete cross-tenant rows. Without this flag the command prints counts only.",
    )
    def purge_cross_tenant_copilot_insights(apply):
        """Delete cross-tenant portfolio_duplicate and pattern_available insights."""
        from app import db

        by_type = _cross_tenant_insights()

        for insight_type in _TARGET_TYPES:
            click.echo(f"{insight_type}: {len(by_type[insight_type])} row(s)")

        if apply:
            total = 0
            for insight_type in _TARGET_TYPES:
                for insight in by_type[insight_type]:
                    db.session.delete(insight)
                total += len(by_type[insight_type])
            db.session.commit()
            click.echo(f"Deleted {total} row(s).")
        else:
            click.echo("Dry run — use --apply to delete.")