"""Initiatives competing for the same capability or owner in a quarter."""

import datetime
import uuid

import pytest

from app.modules.portfolio.services.initiative_conflicts import (
    find_conflicts,
    parse_quarter,
    quarter_bounds,
)


def _clear_login_cache():
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _client_for(app, db_session, org, role="platform_admin"):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(
        email=f"conflicts-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Con",
        last_name="Flict",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid
    _clear_login_cache()
    return client, user


def _tenant(org_id):
    from flask import g

    g.current_org_id = org_id


def _initiative(db_session, org, name, start, end, owner=None, capability=None, status="in_progress"):
    from app.models.strategic import StrategicInitiative

    row = StrategicInitiative(
        name=name, organization_id=org.id, start_date=start, target_completion_date=end,
        owner_id=owner.id if owner else None, status=status,
    )
    db_session.add(row)
    db_session.flush()
    if capability is not None:
        row.capabilities.append(capability)
        db_session.flush()
    return row


def _capability(db_session, org, name):
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(name=name, organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    return cap


Q4 = (2026, 4)
D = datetime.date


@pytest.fixture
def world(app, db_session, make_org):
    org_a, org_b = make_org("conf-a"), make_org("conf-b")
    client_a, user_a = _client_for(app, db_session, org_a)
    client_b, user_b = _client_for(app, db_session, org_b)
    return {"a": (org_a, client_a, user_a), "b": (org_b, client_b, user_b)}


def test_quarter_helpers():
    assert quarter_bounds(2026, 4) == (D(2026, 10, 1), D(2026, 12, 31))
    assert quarter_bounds(2026, 1) == (D(2026, 1, 1), D(2026, 3, 31))
    assert parse_quarter("2026-q4") == (2026, 4)
    assert parse_quarter("2026-Q5") is None
    assert parse_quarter("nonsense") is None


def test_two_initiatives_on_one_capability_in_the_quarter_conflict(world, db_session):
    org, _, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Order to Cash")
    _initiative(db_session, org, "Billing rebuild", D(2026, 9, 1), D(2026, 12, 1), capability=cap)
    _initiative(db_session, org, "Invoice portal", D(2026, 10, 15), D(2027, 2, 1), capability=cap)
    result = find_conflicts(*Q4)
    assert [(c["kind"], c["subject"]) for c in result["conflicts"]] == [("capability", "Order to Cash")]
    assert [i["name"] for i in result["conflicts"][0]["initiatives"]] == ["Billing rebuild", "Invoice portal"]


def test_same_owner_conflicts(world, db_session):
    org, _, user = world["a"]
    _tenant(org.id)
    _initiative(db_session, org, "Alpha", D(2026, 10, 1), D(2026, 11, 1), owner=user)
    _initiative(db_session, org, "Beta", D(2026, 11, 15), D(2026, 12, 20), owner=user)
    result = find_conflicts(*Q4)
    assert [c["kind"] for c in result["conflicts"]] == ["owner"]


def test_initiatives_in_different_quarters_do_not_conflict(world, db_session):
    org, _, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Payroll")
    _initiative(db_session, org, "Early", D(2026, 1, 1), D(2026, 6, 30), capability=cap)
    _initiative(db_session, org, "Late", D(2026, 10, 1), D(2026, 12, 1), capability=cap)
    assert find_conflicts(*Q4)["conflicts"] == []


def test_finished_or_cancelled_initiatives_are_left_out(world, db_session):
    org, _, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Payroll")
    _initiative(db_session, org, "Live", D(2026, 10, 1), D(2026, 12, 1), capability=cap)
    _initiative(db_session, org, "Dropped", D(2026, 10, 1), D(2026, 12, 1), capability=cap, status="cancelled")
    assert find_conflicts(*Q4)["conflicts"] == []


def test_initiative_without_a_start_date_is_reported_not_guessed(world, db_session):
    org, _, _ = world["a"]
    _tenant(org.id)
    cap = _capability(db_session, org, "Payroll")
    _initiative(db_session, org, "Dated", D(2026, 10, 1), D(2026, 12, 1), capability=cap)
    _initiative(db_session, org, "Undated", None, None, capability=cap)
    result = find_conflicts(*Q4)
    assert result["conflicts"] == []
    assert [i["name"] for i in result["undated"]] == ["Undated"]


def test_other_organisations_initiatives_never_conflict_with_mine(world, db_session):
    org_a, _, _ = world["a"]
    org_b, _, _ = world["b"]
    _tenant(org_b.id)
    cap_b = _capability(db_session, org_b, "Payroll")
    _initiative(db_session, org_b, "B one", D(2026, 10, 1), D(2026, 12, 1), capability=cap_b)
    _initiative(db_session, org_b, "B two", D(2026, 10, 1), D(2026, 12, 1), capability=cap_b)
    _tenant(org_a.id)
    cap_a = _capability(db_session, org_a, "Payroll")
    _initiative(db_session, org_a, "A one", D(2026, 10, 1), D(2026, 12, 1), capability=cap_a)
    result = find_conflicts(*Q4)
    assert result["conflicts"] == []
    assert result["running_count"] == 1


def test_page_lists_conflicts_for_the_chosen_quarter_and_hides_other_organisations(world, db_session):
    org_a, client_a, _ = world["a"]
    org_b, client_b, _ = world["b"]
    _tenant(org_a.id)
    cap = _capability(db_session, org_a, "Order to Cash")
    _initiative(db_session, org_a, "Billing rebuild", D(2026, 9, 1), D(2026, 12, 1), capability=cap)
    _initiative(db_session, org_a, "Invoice portal", D(2026, 10, 15), D(2027, 2, 1), capability=cap)
    _clear_login_cache()
    page = client_a.get("/portfolio/conflicts?quarter=2026-Q4").get_data(as_text=True)
    assert "Same capability: Order to Cash" in page
    assert "Invoice portal" in page
    _clear_login_cache()
    other = client_b.get("/portfolio/conflicts?quarter=2026-Q4").get_data(as_text=True)
    assert "Invoice portal" not in other
    assert "No conflicts recorded" in other


def test_bad_quarter_is_reported(world):
    _, client, _ = world["a"]
    _clear_login_cache()
    page = client.get("/portfolio/conflicts?quarter=banana").get_data(as_text=True)
    assert "Quarter must look like 2026-Q4" in page
