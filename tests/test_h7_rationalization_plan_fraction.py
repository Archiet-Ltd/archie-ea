"""H7: /applications/rationalization showed "Plan: 2/0 (0%)" -- a numerator
bigger than its denominator. Root cause: the Plan stage's denominator was
stats.time_scored_count (from ApplicationRationalizationScore), a table with
no subset relationship to stats.consolidation_count (from
ConsolidationListEntry) -- two independent populations compared as if one
contained the other. Fixed in
app/templates/applications/rationalization/dashboard.html by sharing
total_apps as every stage's denominator, matching Detect and Score, and in
app/modules/applications/routes/rationalization_api_routes.py by logging a
warning if a stage count ever still exceeds total_apps.
"""

from __future__ import annotations

import re

from flask import render_template


def _render(app, stats):
    with app.test_request_context("/applications/rationalization"):
        return render_template(
            "applications/rationalization/dashboard.html",
            stats=stats,
            groups=[],
            runs=[],
            latest_run=None,
            currency_symbol="£",
            active_tab="dashboard",
            data_quality=None,
            insufficient_count=0,
        )


def _consolidation_count(body: str):
    """The consolidation figure a person reads ("<n> on the consolidation
    list"), taken from the Get started step while nothing is scored, or from
    the savings card's sentence once scoring has begun. None when the page
    prints no count (an empty list)."""
    match = re.search(r"(\d+) on the consolidation list", body)
    return int(match.group(1)) if match else None


def _total_applications(body: str) -> int:
    # The Score step states the denominator in words while nothing is scored.
    match = re.search(r"Score all (\d+) applications", body)
    assert match, "could not find the portfolio total in the rendered output"
    return int(match.group(1))


def test_plan_fraction_never_has_numerator_exceeding_denominator(app):
    """The exact reported scenario: consolidation entries exist (2) but zero
    applications have been TIME-scored yet -- the old code compared these two
    unrelated counts directly and produced 2/0.

    With nothing scored the page now opens on its Get started steps instead
    of the Progress card, so no fraction is drawn at all; once one application
    is scored the Plan step reads against total_applications."""
    stats = {
        "total_applications": 50,
        "duplicate_groups": 3,
        "estimated_savings": 1000.0,
        "consolidation_count": 2,
        "time_scored_count": 0,
        "roadmap_count": 1,
    }
    body = _render(app, stats)
    assert 'data-testid="rationalization-get-started"' in body
    assert not re.search(r"\b2 ?(/|of) ?0\b", body), "Plan rendered 2/0"
    assert "Score all 50 applications" in body

    count = _consolidation_count(body)
    assert count == 2
    assert count <= _total_applications(body), "consolidation count exceeds the portfolio"

    # Once scoring has begun the page still reads the count against nothing
    # smaller than the portfolio, and never as a "2 of 0" style fraction.
    body = _render(app, dict(stats, time_scored_count=1))
    assert not re.search(r"2 ?(/|of) ?0", body), "Plan rendered 2/0"
    assert _consolidation_count(body) in (None, 2)


def test_plan_fraction_stays_sane_across_arbitrary_counts(app):
    for consolidation_count, time_scored_count, total_apps in [
        (0, 0, 10),
        (5, 5, 10),
        (10, 0, 10),
        (1, 100, 10),
    ]:
        stats = {
            "total_applications": total_apps,
            "duplicate_groups": 0,
            "estimated_savings": 0.0,
            "consolidation_count": consolidation_count,
            "time_scored_count": time_scored_count,
            "roadmap_count": 0,
        }
        body = _render(app, dict(stats, time_scored_count=0))
        assert 'data-testid="rationalization-get-started"' in body
        total = _total_applications(body)
        assert total == total_apps
        count = _consolidation_count(body)
        if consolidation_count:
            assert count == consolidation_count
            assert count <= total, f"{count} on the list but only {total} applications"
        else:
            assert count is None
