"""The roadmap lists only gaps a person recorded by hand.

A gap between two plateaus (a plateau transition) is kept in its own place and
count; it is never shown on the roadmap as a hand-recorded, editable and
deletable gap. Two organisations: the other organisation's gaps never appear.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="rg-%s@example.com" % uuid.uuid4().hex[:8],
        first_name="rg",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="business_architect",
    )
    db_session.add(user)
    db_session.flush()
    user.role = Role.query.filter_by(name="Administrator").first()
    db_session.commit()
    return user


def test_transition_gap_is_not_listed_as_recorded(app, db_session, make_org, login_as, client):
    from app.models.implementation_migration import GAP_KIND_PLATEAU_TRANSITION, Gap, Plateau
    from app.services.gap_register_service import create_gap

    org_a = make_org("rg-a")
    org_b = make_org("rg-b")
    kept, _ = create_gap(org_a.id, "Hand recorded shortfall")
    other, _ = create_gap(org_b.id, "Other organisation shortfall")
    first = Plateau(name="Plateau one", organization_id=org_a.id)
    second = Plateau(name="Plateau two", organization_id=org_a.id)
    db_session.add_all([first, second])
    db_session.flush()
    transition = Gap(
        name="Plateau one to plateau two",
        organization_id=org_a.id,
        gap_kind=GAP_KIND_PLATEAU_TRANSITION,
        originating_plateau_id=first.id,
        target_plateau_id=second.id,
        auto_generated=False,
    )
    db_session.add(transition)
    db_session.commit()

    login_as(client, _user(db_session, org_a))
    resp = client.get("/capability-map/api/roadmap/gaps?include=recorded")
    assert resp.status_code == 200, resp.data[:200]
    body = resp.get_json()
    recorded = [g for g in body["gaps"] if g.get("recorded")]
    names = {g["name"] for g in recorded}

    assert "Hand recorded shortfall" in names
    assert "Plateau one to plateau two" not in names
    assert "Other organisation shortfall" not in names
    assert transition.id not in {g["id"] for g in recorded}
    assert body["statistics"]["total_gaps"] == len(body["gaps"])
