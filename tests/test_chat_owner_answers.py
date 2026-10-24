"""The chat's application search answers ownership from the ownership records.

A startup CTO asks "who owns what" and "which applications have no owner". The
tool used to read an owner column that does not exist on the model, so it
reported every owned application as ownerless. It now reads the same owner
records the Applications pages read.
"""
from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, first, last):
    from app.models.user import User

    user = User(
        email=f"{first.lower()}-{uuid.uuid4().hex[:8]}@example.test",
        first_name=first,
        last_name=last,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _executor(org):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    executor = ToolExecutor.__new__(ToolExecutor)
    executor._resolver = None
    executor._org_id = org.id
    return executor


@pytest.fixture
def two_orgs(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.element_ownership_service import set_element_owner

    org_a, org_b = make_org("own-a"), make_org("own-b")
    seeded = {}
    for org, label in ((org_a, "A"), (org_b, "B")):
        with tenant_ctx(org.id):
            owner = _user(db_session, org, f"Olive{label}", "Owner")
            owned = ApplicationComponent(name=f"{label} Billing", organization_id=org.id)
            unowned = ApplicationComponent(name=f"{label} Wiki", organization_id=org.id)
            db_session.add_all([owned, unowned])
            db_session.flush()
            set_element_owner(
                element_type="application", element_id=owned.id, user_id=owner.id,
                organization_id=org.id, ownership_type="primary", commit=False,
            )
            db_session.flush()
            seeded[label] = (org, owned, unowned, owner)
    return seeded


def test_owned_applications_come_back_with_their_owners(two_orgs, tenant_ctx):
    org, owned, unowned, owner = two_orgs["A"]
    with tenant_ctx(org.id):
        result = _executor(org)._tool_find_applications({"ownership": "owned"})
    rows = {r["name"]: r for r in result["result"]}
    assert set(rows) == {"A Billing"}
    assert rows["A Billing"]["owners"] == [
        {"name": "OliveA Owner", "ownership_type": "primary"}
    ]
    assert rows["A Billing"]["has_owner"] is True


def test_unowned_filter_returns_only_applications_without_an_ownership_record(two_orgs, tenant_ctx):
    org, owned, unowned, owner = two_orgs["A"]
    with tenant_ctx(org.id):
        result = _executor(org)._tool_find_applications({"ownership": "unowned"})
    assert [r["name"] for r in result["result"]] == ["A Wiki"]
    assert result["result"][0]["owners"] == []
    assert result["result"][0]["has_owner"] is False
    assert result["total"] == 1


def test_unfiltered_search_states_who_owns_each_application(two_orgs, tenant_ctx):
    org, *_ = two_orgs["A"]
    with tenant_ctx(org.id):
        result = _executor(org)._tool_find_applications({})
    by_name = {r["name"]: r for r in result["result"]}
    assert by_name["A Billing"]["has_owner"] is True
    assert by_name["A Wiki"]["has_owner"] is False
    assert all("owner_team" not in r for r in result["result"])


def test_other_organisations_owners_and_applications_never_appear(two_orgs, tenant_ctx):
    org_a = two_orgs["A"][0]
    with tenant_ctx(org_a.id):
        owned = _executor(org_a)._tool_find_applications({"ownership": "owned"})
        everything = _executor(org_a)._tool_find_applications({})
    text = repr(owned) + repr(everything)
    assert "OliveB" not in text and "B Billing" not in text and "B Wiki" not in text


def test_capability_listing_does_not_report_a_missing_owner_column(two_orgs, tenant_ctx):
    import inspect

    from app.modules.ai_chat.tools import executor

    assert "owner_team" not in inspect.getsource(executor)


def test_table_data_filter_owner_none_lists_only_unowned_applications(two_orgs, app):
    """The route the chat's "applications without owner" question calls."""
    from flask import g
    from flask_login import login_user

    from app.api.application_routes import api_applications_table

    org, owned, unowned, owner = two_orgs["A"]
    with app.test_request_context("/api/applications/table-data?filter_owner=none"):
        g.current_org_id = org.id
        login_user(owner)
        body = api_applications_table().get_json()
    assert [r["name"] for r in body["data"]] == ["A Wiki"]
    assert body["recordsTotal"] == 1
