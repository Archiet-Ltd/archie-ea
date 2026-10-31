"""Capability maturity on the capability map (the retired maturity heat map's checks).

The single requirement this file exists to pin: an **unassessed** capability must
never render as Level 1. The maturity columns previously carried
``default=1`` / ``default=3``, so every capability nobody had ever assessed
looked like a real Level 1 score on screen — a heatmap built on that would have
told leadership the whole estate was assessed and found immature.

``maturity_assessment_date IS NOT NULL`` is the only thing that says an
assessment is real, and the map's data endpoint keys off exactly that. The map
reads that endpoint, so the rule is asserted on its rows; ``/capability-maturity/heatmap``
itself answers a redirect to the map coloured by maturity gap.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id, email):
    from app.models.user import User

    user = User(
        email=email,
        first_name="Heat",
        last_name="Map",
        organization_id=org_id,
        confirmed=True,
    )
    user.password = "Passw0rd!123"
    db_session.add(user)
    db_session.flush()
    return user


def _make_capability(db_session, org_id, name, *, current=None, target=None, assessed_on=None,
                     category=None):
    from app.models.capability_models import BusinessCapability

    cap = BusinessCapability(
        name=name,
        organization_id=org_id,
        category=category,
        current_maturity_level=current,
        target_maturity_level=target,
        maturity_assessment_date=assessed_on,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _map_rows(client, login_as, user):
    login_as(client, user)
    resp = client.get("/capability-map/api/unified-capabilities")
    assert resp.status_code == 200, resp.status_code
    data = resp.get_json()
    return {r["name"]: r for r in (data.get("unified_capabilities") or data.get("capabilities") or [])}


def test_old_maturity_heatmap_address_redirects_to_the_map_by_gap(app, db_session, make_org, login_as):
    org = make_org("heat-redirect")
    user = _make_user(db_session, org.id, f"heat-redirect-{org.id}@example.com")
    client = app.test_client()
    login_as(client, user)

    resp = client.get("/capability-maturity/heatmap")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/capability-map/dashboard?heat=gap")

    landed = client.get(resp.headers["Location"])
    assert landed.status_code == 200
    html = landed.get_data(as_text=True)
    assert 'id="capmap-legend"' in html and "Maturity gap" in html


def test_assessed_capability_carries_its_real_levels_and_gap(app, db_session, make_org, login_as):
    from flask import g

    org = make_org("heat-assessed")
    with app.test_request_context("/"):
        g.current_org_id = org.id
        _make_capability(
            db_session, org.id, "Order Fulfilment",
            current=2, target=4, assessed_on=datetime(2026, 5, 1), category="Operations",
        )
        user = _make_user(db_session, org.id, f"heat-assessed-{org.id}@example.com")

    rows = _map_rows(app.test_client(), login_as, user)

    row = rows["Order Fulfilment"]
    assert row["current_maturity"] == 2 and row["target_maturity"] == 4
    # The map draws gap as target - current from these two figures.
    assert row["target_maturity"] - row["current_maturity"] == 2
    assert row["maturity_reason_code"] is None


def test_unassessed_capability_has_no_level_and_is_not_level_one(
    app, db_session, make_org, login_as
):
    """An unassessed capability must be visually distinct from a Level 1 one."""
    from flask import g

    org = make_org("heat-unassessed")
    with app.test_request_context("/"):
        g.current_org_id = org.id
        # No assessment date -- even if levels were somehow populated, they are
        # not evidence and must not be shown.
        _make_capability(db_session, org.id, "Zeta Unassessed Capability",
                         current=1, target=3, assessed_on=None, category="Operations")
        user = _make_user(db_session, org.id, f"heat-unassessed-{org.id}@example.com")

    rows = _map_rows(app.test_client(), login_as, user)

    row = rows["Zeta Unassessed Capability"]
    # null is what the map renders as Not assessed (dashed box, no level, no gap).
    assert row["current_maturity"] is None
    assert row["target_maturity"] is None
    assert row["maturity_gap"] is None
    assert row["maturity_reason_code"] == "no_maturity_recorded"


def test_another_orgs_capabilities_never_appear(app, db_session, make_org, login_as):
    from flask import g

    org_a = make_org("heat-a")
    org_b = make_org("heat-b")

    with app.test_request_context("/"):
        g.current_org_id = org_b.id
        _make_capability(db_session, org_b.id, "Foreign Org Capability",
                         current=5, target=5, assessed_on=datetime(2026, 5, 1))

    with app.test_request_context("/"):
        g.current_org_id = org_a.id
        _make_capability(db_session, org_a.id, "Own Org Capability",
                         current=3, target=4, assessed_on=datetime(2026, 5, 1))
        user_a = _make_user(db_session, org_a.id, f"heat-a-{org_a.id}@example.com")

    rows = _map_rows(app.test_client(), login_as, user_a)

    assert "Own Org Capability" in rows
    assert "Foreign Org Capability" not in rows
