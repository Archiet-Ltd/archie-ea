"""An owner who has no account in the workspace is recorded, shown and counted.

Two organisations throughout: a named person recorded in one organisation is
never read by the other, and a name that matches a user of another organisation
is still a named person here.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, first, last, persona="enterprise_architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=first, last_name=last, organization_id=org.id,
        confirmed=True, enterprise_role=persona,
    )
    user.role = Role.query.filter_by(name="User").one()
    db_session.add(user)
    db_session.flush()
    return user


def _app(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id,
        description="Old", deployment_status="development",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _save_owner_through_the_form(client, application_id, name):
    """Type *name* into Business Owner on the Overview edit form and save it."""
    page = client.get(f"/applications/{application_id}?edit=1&tab=overview")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    action = re.search(r'<form id="overview-edit-form"[^>]*action="([^"]+)"', html).group(1)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    return client.post(action, data={"businessOwner": name, "csrf_token": token})


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("own-a"), make_org("own-b")
    return {
        "org_a": org_a, "org_b": org_b,
        "user_a": _user(db_session, org_a, "usera", "Una", "Editor"),
        "user_b": _user(db_session, org_b, "userb", "Ben", "Editor"),
        "app_a": _app(db_session, org_a, "Ledger"),
        "app_b": _app(db_session, org_b, "Payroll"),
    }


def test_a_name_typed_into_the_owner_field_that_matches_no_user_is_recorded_as_a_named_person(
    world, client, login_as, db_session
):
    from app.models.application_owner import ApplicationOwner
    from app.services.element_ownership_service import application_is_owned

    org, application = world["org_a"], world["app_a"]
    assert application_is_owned(application) is False
    login_as(client, world["user_a"])
    resp = _save_owner_through_the_form(client, application.id, "Ifiok Etim")
    assert resp.status_code == 302, resp.get_data(as_text=True)[:400]
    db_session.expire_all()

    row = ApplicationOwner.query.filter_by(application_id=application.id).one()
    assert (row.user_id, row.person_name, row.ownership_type, row.organization_id) == (
        None, "Ifiok Etim", "business", org.id,
    )
    assert application.business_owner == "Ifiok Etim"
    assert application_is_owned(application) is True
    assert application.legacy_business_owner is None


def test_a_named_person_is_shown_with_email_and_role_and_not_reported_as_missing(world, db_session):
    from app.models.application_owner import ApplicationOwner
    from app.modules.intelligence.services.query_catalogue import run_entry
    from app.services.element_ownership_service import (
        application_is_owned_expression, set_named_person_owner,
    )
    from app.models.application_portfolio import ApplicationComponent

    org, application, other = world["org_a"], world["app_a"], None
    other = _app(db_session, org, "Unowned")
    set_named_person_owner(
        application, "Ifiok Etim", "primary", organization_id=org.id,
        email="ifiok@contractor.example", role="Engineering lead",
    )
    db_session.flush()

    rows = ApplicationOwner.get_display_rows_for_application(application.id, org.id)
    assert len(rows) == 1
    shown = rows[0]
    assert shown["user_name"] == "Ifiok Etim"
    assert shown["user_email"] == "ifiok@contractor.example"
    assert shown["person_role"] == "Engineering lead"
    assert shown["has_account"] is False

    owned_ids = {
        row.id for row in ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == org.id, application_is_owned_expression(),
        )
    }
    assert application.id in owned_ids and other.id not in owned_ids

    missing = run_entry("applications_without_owner", org.id)
    missing_ids = {c["id"] for c in missing["citations"]}
    assert other.id in missing_ids and application.id not in missing_ids

    inventory = run_entry("applications_and_owners", org.id)
    owner_of = {row["id"]: row["owner"] for row in inventory["rows"]}
    assert owner_of[application.id] == "Ifiok Etim"


def test_a_named_person_in_one_organisation_is_not_read_by_the_other(world, db_session):
    from app.models.application_owner import ApplicationOwner
    from app.modules.intelligence.services.query_catalogue import run_entry
    from app.services.element_ownership_service import set_named_person_owner

    org_a, org_b = world["org_a"], world["org_b"]
    set_named_person_owner(world["app_a"], "Ifiok Etim", "primary", organization_id=org_a.id)
    db_session.flush()

    assert ApplicationOwner.get_display_rows_for_application(world["app_a"].id, org_b.id) == []
    missing_b = {c["id"] for c in run_entry("applications_without_owner", org_b.id)["citations"]}
    assert world["app_b"].id in missing_b
    inventory_b = {row["id"]: row["owner"] for row in run_entry("applications_and_owners", org_b.id)["rows"]}
    assert inventory_b[world["app_b"].id] == "not recorded"
    assert world["app_a"].id not in inventory_b


def test_a_name_that_matches_a_user_of_another_organisation_stays_a_named_person(
    world, client, login_as, db_session
):
    from app.models.application_owner import ApplicationOwner

    elsewhere = _user(db_session, world["org_b"], "elsewhere", "Erin", "Elsewhere")
    login_as(client, world["user_a"])
    resp = _save_owner_through_the_form(client, world["app_a"].id, "Erin Elsewhere")
    assert resp.status_code == 302
    row = ApplicationOwner.query.filter_by(application_id=world["app_a"].id).one()
    assert row.user_id is None and row.person_name == "Erin Elsewhere"
    assert elsewhere.id != row.user_id


def test_changing_a_named_person_to_a_user_and_back_keeps_one_owner(world, client, login_as, db_session):
    from app.models.application_owner import ApplicationOwner

    org, application = world["org_a"], world["app_a"]
    colleague = _user(db_session, org, "colleague", "Cora", "Colleague")
    login_as(client, world["user_a"])
    for typed, expected in (
        ("Ifiok Etim", (None, "Ifiok Etim")),
        ("Cora Colleague", (colleague.id, None)),
        ("Ifiok Etim", (None, "Ifiok Etim")),
    ):
        assert _save_owner_through_the_form(client, application.id, typed).status_code == 302
        db_session.expire_all()
        rows = ApplicationOwner.query.filter_by(application_id=application.id, ownership_type="business").all()
        assert [(r.user_id, r.person_name) for r in rows] == [expected], typed


def test_the_owner_route_records_a_person_without_an_account(world, client, login_as, db_session):
    from app.models.application_owner import ApplicationOwner

    login_as(client, world["user_a"])
    resp = client.post(
        f"/applications/{world['app_a'].id}/owners",
        json={"person_name": "Ifiok Etim", "person_email": "ifiok@contractor.example",
              "person_role": "Engineering lead", "ownership_type": "technical"},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()["owner"]
    assert body["person_name"] == "Ifiok Etim" and body["user_id"] is None
    row = ApplicationOwner.query.filter_by(application_id=world["app_a"].id).one()
    assert row.person_role == "Engineering lead" and row.organization_id == world["org_a"].id

    # The other organisation cannot record an owner on this organisation's application.
    login_as(client, world["user_b"])
    refused = client.post(
        f"/applications/{world['app_a'].id}/owners",
        json={"person_name": "Mallory", "ownership_type": "primary"},
    )
    assert refused.status_code == 404
    assert ApplicationOwner.query.filter_by(application_id=world["app_a"].id).count() == 1


def test_the_application_page_shows_the_named_person_with_role(world, client, login_as, db_session):
    from app.services.element_ownership_service import set_named_person_owner

    set_named_person_owner(
        world["app_a"], "Ifiok Etim", "primary", organization_id=world["org_a"].id,
        email="ifiok@contractor.example", role="Engineering lead",
    )
    db_session.flush()
    login_as(client, world["user_a"])
    page = client.get(f"/applications/{world['app_a'].id}").get_data(as_text=True)
    assert "Ifiok Etim" in page
    assert "Engineering lead" in page
    assert "No owner recorded" not in page
    assert 'data-testid="application-owner"' in page
