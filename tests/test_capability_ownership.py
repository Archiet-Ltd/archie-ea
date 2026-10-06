"""Tests for R1-B03 PR 2: capability ownership via the one ownership record's
element_type/element_id reference.

Two-organisation tests verify a capability or an owner from organisation B
cannot be assigned/read against organisation A.
"""
from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role_obj = Role.query.filter_by(name="User").first()
    user = User(
        email=f"capowner-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        role_id=role_obj.id if role_obj else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_capability(db_session, org, name):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _post_json(client, path, data):
    return client.post(path, data=json.dumps(data), content_type="application/json")


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("capown-a")
    org_b = make_org("capown-b")
    user_a = _make_user(db_session, org_a, "usera")
    user_b = _make_user(db_session, org_b, "userb")
    cap_a = _make_capability(db_session, org_a, "Cap A")
    cap_b = _make_capability(db_session, org_b, "Cap B")
    return {
        "org_a": org_a, "org_b": org_b,
        "user_a": user_a, "user_b": user_b,
        "cap_a": cap_a, "cap_b": cap_b,
    }


# ── service layer ───────────────────────────────────────────────────────────


def test_set_capability_owner_success(db_session, two_orgs):
    from app.services.capability_ownership_service import set_capability_owner

    record = set_capability_owner(
        capability_id=two_orgs["cap_a"].id,
        user_id=two_orgs["user_a"].id,
        organization_id=two_orgs["org_a"].id,
    )
    assert record.element_type == "capability"
    assert record.element_id == two_orgs["cap_a"].id
    assert record.user_id == two_orgs["user_a"].id


def test_set_capability_owner_refuses_cross_org_capability(db_session, two_orgs):
    from app.services.capability_ownership_service import (
        CrossOrganisationCapabilityOwner,
        set_capability_owner,
    )

    with pytest.raises(CrossOrganisationCapabilityOwner):
        set_capability_owner(
            capability_id=two_orgs["cap_b"].id,
            user_id=two_orgs["user_a"].id,
            organization_id=two_orgs["org_a"].id,
        )


def test_set_capability_owner_refuses_cross_org_user(db_session, two_orgs):
    from app.services.capability_ownership_service import (
        CrossOrganisationCapabilityOwner,
        set_capability_owner,
    )

    with pytest.raises(CrossOrganisationCapabilityOwner):
        set_capability_owner(
            capability_id=two_orgs["cap_a"].id,
            user_id=two_orgs["user_b"].id,
            organization_id=two_orgs["org_a"].id,
        )


def test_set_capability_owner_is_idempotent(db_session, two_orgs):
    from app.services.capability_ownership_service import set_capability_owner

    first = set_capability_owner(
        capability_id=two_orgs["cap_a"].id,
        user_id=two_orgs["user_a"].id,
        organization_id=two_orgs["org_a"].id,
    )
    second = set_capability_owner(
        capability_id=two_orgs["cap_a"].id,
        user_id=two_orgs["user_a"].id,
        organization_id=two_orgs["org_a"].id,
    )
    assert first.id == second.id


def test_list_capabilities_with_no_owner_excludes_owned_and_other_org(db_session, two_orgs):
    from app.services.capability_ownership_service import (
        list_capabilities_with_no_owner,
        set_capability_owner,
    )

    unowned = _make_capability(db_session, two_orgs["org_a"], "Unowned")
    set_capability_owner(
        capability_id=two_orgs["cap_a"].id,
        user_id=two_orgs["user_a"].id,
        organization_id=two_orgs["org_a"].id,
    )

    result_ids = {c.id for c in list_capabilities_with_no_owner(two_orgs["org_a"].id)}
    assert unowned.id in result_ids
    assert two_orgs["cap_a"].id not in result_ids
    assert two_orgs["cap_b"].id not in result_ids  # other org, never returned


def test_remove_capability_owner_refuses_cross_org(db_session, two_orgs):
    from app.services.capability_ownership_service import (
        remove_capability_owner,
        set_capability_owner,
    )

    record = set_capability_owner(
        capability_id=two_orgs["cap_a"].id,
        user_id=two_orgs["user_a"].id,
        organization_id=two_orgs["org_a"].id,
    )
    assert remove_capability_owner(owner_record_id=record.id, organization_id=two_orgs["org_b"].id) is False
    assert remove_capability_owner(owner_record_id=record.id, organization_id=two_orgs["org_a"].id) is True


# ── routes ───────────────────────────────────────────────────────────────────


def test_api_set_capability_owner_route(db_session, two_orgs, client, login_as):
    login_as(client, two_orgs["user_a"])
    resp = _post_json(client, f"/capability-map/api/capabilities/{two_orgs['cap_a'].id}/owners", {
        "user_id": two_orgs["user_a"].id,
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["owner"]["element_id"] == two_orgs["cap_a"].id


def test_api_set_capability_owner_route_refuses_cross_org_capability(db_session, two_orgs, client, login_as):
    login_as(client, two_orgs["user_a"])
    resp = _post_json(client, f"/capability-map/api/capabilities/{two_orgs['cap_b'].id}/owners", {
        "user_id": two_orgs["user_a"].id,
    })
    assert resp.status_code == 400, resp.get_data(as_text=True)


def test_api_capability_owners_get_route_404s_a_nonexistent_capability(db_session, two_orgs, client, login_as):
    """Adversarial-probe regression: GET on a capability id that does not
    exist must 404, not render an empty-owners 200 as if the id were real."""
    login_as(client, two_orgs["user_a"])
    resp = client.get("/capability-map/api/capabilities/999999999/owners")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_api_capability_owners_get_route_404s_a_cross_org_capability(db_session, two_orgs, client, login_as):
    login_as(client, two_orgs["user_a"])
    resp = client.get(f"/capability-map/api/capabilities/{two_orgs['cap_b'].id}/owners")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_api_capability_owners_get_route(db_session, two_orgs, client, login_as):
    from app.services.capability_ownership_service import set_capability_owner

    set_capability_owner(
        capability_id=two_orgs["cap_a"].id,
        user_id=two_orgs["user_a"].id,
        organization_id=two_orgs["org_a"].id,
    )
    login_as(client, two_orgs["user_a"])
    resp = client.get(f"/capability-map/api/capabilities/{two_orgs['cap_a'].id}/owners")
    assert resp.status_code == 200
    owners = resp.get_json()["owners"]
    assert len(owners) == 1
    assert owners[0]["user_id"] == two_orgs["user_a"].id
