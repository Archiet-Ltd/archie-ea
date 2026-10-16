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
import uuid

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


# ── Through the real route, against real data (not a hand-built stats dict) ──
#
# The tests above render the template directly with a hand-built stats dict,
# so they never exercise rationalization_dashboard()'s own tracking_count
# query -- which is exactly how a real cross-tenant leak in that query went
# undetected: RationalizationBenefitsTracker has no organization_id of its
# own, so a bare .query.count() read every tenant's rows. These hit
# GET /applications/rationalization for real, across two organisations.


def _make_user(db_session, org):
    from app.models.user import Role, User

    from tests.smoke.conftest import PASSWORD

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=f"track-tile-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Track",
        last_name="Test",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = PASSWORD
    db_session.add(user)
    db_session.flush()
    return user


def test_tracking_count_through_the_real_route_excludes_another_organisations_trackers(
    db_session, make_org, client, login_as,
):
    """Org A must not see Org B's RationalizationBenefitsTracker rows in its
    own Track tile. Org B has 5 tracker rows on an application that is also
    on org B's own roadmap, so the leak would show a real nonzero count, not
    merely a division artifact; Org A has none of its own."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import RationalizationBenefitsTracker
    from app.models.consolidation_list import ConsolidationListEntry

    org_a = make_org("track-a")
    org_b = make_org("track-b")
    user_a = _make_user(db_session, org_a)

    suffix = uuid.uuid4().hex[:10]
    app_b = ApplicationComponent(name=f"Org-B-App-{suffix}", organization_id=org_b.id)
    db_session.add(app_b)
    db_session.flush()

    # app_b is on org B's own roadmap, so its trackers are a real, countable
    # population -- not excluded by the roadmap-only filter either.
    db_session.add(
        ConsolidationListEntry(
            application_id=app_b.id, recommended_action="add_to_roadmap"
        )
    )
    for _ in range(5):
        db_session.add(RationalizationBenefitsTracker(application_id=app_b.id))
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/applications/rationalization")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    count, _total = _track_fraction(html)
    assert count == 0, (
        "Org A's Track tile must not count Org B's tracker rows -- got "
        f"{count}, expected 0"
    )


def test_tracking_count_through_the_real_route_counts_the_current_organisations_own_trackers(
    db_session, make_org, client, login_as,
):
    """The other side of the same fence: Org A's own tracker, for an
    application already on Org A's own roadmap, must still be counted."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import RationalizationBenefitsTracker
    from app.models.consolidation_list import ConsolidationListEntry

    org_a = make_org("track-own-a")
    user_a = _make_user(db_session, org_a)

    suffix = uuid.uuid4().hex[:10]
    app_a = ApplicationComponent(name=f"Org-A-App-{suffix}", organization_id=org_a.id)
    db_session.add(app_a)
    db_session.flush()

    db_session.add(
        ConsolidationListEntry(
            application_id=app_a.id, recommended_action="add_to_roadmap"
        )
    )
    db_session.add(RationalizationBenefitsTracker(application_id=app_a.id))
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/applications/rationalization")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    count, _total = _track_fraction(html)
    assert count == 1, (
        "Org A's own tracker, for an application on its own roadmap, must be "
        f"counted -- got {count}, expected 1"
    )


def test_tracking_count_through_the_real_route_excludes_trackers_off_the_roadmap(
    db_session, make_org, client, login_as,
):
    """A tracker row created by the unrelated AI-chat approve path, for an
    application never added to the roadmap, must not inflate Track's count
    past its own denominator (roadmap_count) -- e.g. "Track 3/1 (300%)"."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import RationalizationBenefitsTracker

    org_a = make_org("track-noroadmap-a")
    user_a = _make_user(db_session, org_a)

    suffix = uuid.uuid4().hex[:10]
    app_a = ApplicationComponent(
        name=f"Org-A-Off-Roadmap-{suffix}", organization_id=org_a.id
    )
    db_session.add(app_a)
    db_session.flush()

    # No ConsolidationListEntry at all for app_a -- it is not on the roadmap.
    db_session.add(RationalizationBenefitsTracker(application_id=app_a.id))
    db_session.flush()

    login_as(client, user_a)
    resp = client.get("/applications/rationalization")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    count, total = _track_fraction(html)
    assert count == 0, (
        "A tracker for an application that was never added to the roadmap "
        f"must not be counted -- got {count}/{total}"
    )
