"""The roadmap's gap signals count the supporting applications of the signed-in organisation only."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    user = User(
        email=f"gapsig-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(), last_name="Test", organization_id=org.id, confirmed=True,
        enterprise_role="enterprise_architect", role_id=role.id if role else None)
    db_session.add(user)
    db_session.flush()
    return user


def _business_signals(client):
    body = client.get("/capability-map/api/roadmap/gaps").get_json()
    return {g["name"]: g for g in body["gaps"] if g["capability_type"] == "business"}


def test_support_in_another_organisation_never_counts_here(db_session, make_org, client, login_as):
    from app.services.capability_support import record_capability_support
    from tests.test_capability_support_writer import seed

    org_a, org_b = make_org("gapsig-a"), make_org("gapsig-b")
    user_a, user_b = _user(db_session, org_a, "a"), _user(db_session, org_b, "b")
    app_a, cap_a = seed(db_session, org_a, "A")
    app_b, cap_b = seed(db_session, org_b, "B")
    app_b2, _ = seed(db_session, org_b, "B2")
    record_capability_support(org_a.id, app_a.archimate_element_id, cap_a.archimate_element_id)
    record_capability_support(org_b.id, app_b.archimate_element_id, cap_b.archimate_element_id)
    record_capability_support(org_b.id, app_b2.archimate_element_id, cap_b.archimate_element_id)
    db_session.flush()

    login_as(client, user_a)
    signals_a = _business_signals(client)
    assert cap_b.name not in signals_a
    # Supported by one application: no coverage gap is raised for it.
    assert cap_a.name not in signals_a or "coverage" not in signals_a[cap_a.name]["gap_types"]

    # Organisation A's capability is not supported in B, and the other way round.
    login_as(client, user_b)
    signals_b = _business_signals(client)
    assert cap_a.name not in signals_b
    assert cap_b.name not in signals_b or "coverage" not in signals_b[cap_b.name]["gap_types"]


def test_a_capability_supported_only_elsewhere_is_unmapped_here(db_session, make_org, client, login_as):
    from app.services.capability_support import record_capability_support
    from tests.test_capability_support_writer import seed

    org_a, org_b = make_org("gapsig-c"), make_org("gapsig-d")
    user_a = _user(db_session, org_a, "c")
    app_a, cap_a = seed(db_session, org_a, "C")
    app_b, cap_b = seed(db_session, org_b, "D")
    record_capability_support(org_b.id, app_b.archimate_element_id, cap_b.archimate_element_id)
    db_session.flush()

    login_as(client, user_a)
    signals = _business_signals(client)
    assert signals[cap_a.name]["gap_details"][0] == "No applications mapped"
    assert signals[cap_a.name]["app_count"] == 0
