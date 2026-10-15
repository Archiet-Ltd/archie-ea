"""POST /applications/rationalization/api/benefits/<app_id> must refuse to create or
modify a RationalizationBenefitsTracker row for an application outside the caller's
active organisation, the same way the sibling GET route already does.

These hit the real route through the Flask test client, across two real
organisations, rather than asserting against a hand-built queryset.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text


def _make_user(db_session, org, role_name="Administrator"):
    from app.models.user import Role, User

    from tests.smoke.conftest import PASSWORD

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()

    user = User(
        email=f"benefits-save-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Benefits",
        last_name="Save",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = PASSWORD
    db_session.add(user)
    db_session.flush()
    return user


def _setup(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    org_a = make_org("benefits-save-a")
    org_b = make_org("benefits-save-b")
    user_a = _make_user(db_session, org_a)

    suffix = uuid.uuid4().hex[:10]
    app_a = ApplicationComponent(name=f"Save-A-{suffix}", organization_id=org_a.id)
    app_b = ApplicationComponent(name=f"Save-B-{suffix}", organization_id=org_b.id)
    db_session.add_all([app_a, app_b])
    db_session.flush()
    return user_a, app_a, app_b


def _tracker_rows(db_session, app_id):
    return db_session.execute(
        text(
            "SELECT id, measurement_notes, projected_annual_savings "
            "FROM rationalization_benefits WHERE application_id = :a"
        ),
        {"a": app_id},
    ).fetchall()


def test_post_against_another_organisations_application_with_no_tracker_is_refused(
    db_session, make_org, client, login_as,
):
    """A tracker must not be created on another organisation's application."""
    user_a, _app_a, app_b = _setup(db_session, make_org)
    assert _tracker_rows(db_session, app_b.id) == []

    login_as(client, user_a)
    resp = client.post(
        f"/applications/rationalization/api/benefits/{app_b.id}",
        json={
            "measurement_notes": "written by another organisation",
            "actual_annual_savings": 1,
        },
    )

    assert resp.status_code == 404
    assert _tracker_rows(db_session, app_b.id) == [], (
        "a cross-organisation POST must not create a tracker row"
    )


def test_post_against_another_organisations_existing_tracker_is_refused_and_unchanged(
    db_session, make_org, client, login_as,
):
    """An existing tracker on another organisation's application must not be
    overwritten, and the response must not disclose its figures."""
    from app.models.application_rationalization import RationalizationBenefitsTracker

    user_a, _app_a, app_b = _setup(db_session, make_org)
    db_session.add(
        RationalizationBenefitsTracker(
            application_id=app_b.id,
            projected_annual_savings=100000,
            measurement_notes="original",
        )
    )
    db_session.flush()

    login_as(client, user_a)
    resp = client.post(
        f"/applications/rationalization/api/benefits/{app_b.id}",
        json={"measurement_notes": "overwritten", "actual_annual_savings": 1},
    )

    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "savings_variance_pct" not in body, (
        "the response must not reveal another organisation's savings variance"
    )

    rows = _tracker_rows(db_session, app_b.id)
    assert len(rows) == 1
    assert rows[0][1] == "original"
    assert rows[0][2] == 100000


def test_post_against_the_callers_own_application_succeeds(
    db_session, make_org, client, login_as,
):
    """Behaviour against the caller's own data is unchanged by the guard."""
    user_a, app_a, _app_b = _setup(db_session, make_org)

    login_as(client, user_a)
    resp = client.post(
        f"/applications/rationalization/api/benefits/{app_a.id}",
        json={"measurement_notes": "own notes", "actual_annual_savings": 5},
    )

    assert resp.status_code == 200
    rows = _tracker_rows(db_session, app_a.id)
    assert len(rows) == 1
    assert rows[0][1] == "own notes"


def test_post_against_a_non_existent_application_is_refused(
    db_session, make_org, client, login_as,
):
    """An id that does not belong to any application is a 404, not a server error."""
    user_a, _app_a, _app_b = _setup(db_session, make_org)

    login_as(client, user_a)
    resp = client.post(
        "/applications/rationalization/api/benefits/999999999",
        json={"measurement_notes": "ghost"},
    )

    assert resp.status_code == 404
