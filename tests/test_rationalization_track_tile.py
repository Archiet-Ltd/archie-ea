"""The rationalization dashboard's pipeline stepper "Track" stage used to
hardcode its count to 0 in the template regardless of the real
RationalizationBenefitsTracker row count passed through ``stats`` -- so the
stage read "0/1 (0%)" even once an application's benefits were actually being
tracked. Fixed by reading ``stats.tracking_count`` (now computed in
rationalization_dashboard() from RationalizationBenefitsTracker, the same way
the four earlier stages are each backed by their own real count) instead of
a literal 0.
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


def _track_fraction(body: str) -> tuple[int, int]:
    match = re.search(r"Track</span>\s*<span[^>]*>(\d+)/(\d+)", body)
    assert match, "could not find the Track step's count/total in rendered output"
    return int(match.group(1)), int(match.group(2))


def _base_stats(**overrides):
    stats = {
        "total_applications": 20,
        "duplicate_groups": 2,
        "estimated_savings": 0.0,
        "consolidation_count": 3,
        "time_scored_count": 20,
        "roadmap_count": 1,
    }
    stats.update(overrides)
    return stats


def test_track_reflects_a_real_tracking_count_not_a_hardcoded_zero(app):
    stats = _base_stats(tracking_count=1)
    body = _render(app, stats)
    count, total = _track_fraction(body)
    assert count == 1, "Track must render the real tracking_count, not a hardcoded 0"
    assert total == 1


def test_track_reads_zero_when_nothing_is_tracked_yet(app):
    stats = _base_stats(tracking_count=0)
    body = _render(app, stats)
    count, _total = _track_fraction(body)
    assert count == 0


def test_track_defaults_to_zero_when_tracking_count_is_absent(app):
    """stats dicts built before tracking_count existed must not error."""
    stats = _base_stats()
    body = _render(app, stats)
    count, _total = _track_fraction(body)
    assert count == 0
