"""The Overview edit form saves every field a person changes, through one route.

Two organisations throughout: a second organisation cannot save to the first's
application by id, and a refused save leaves the row as it was.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

FORM_FIELDS = {
    "name": "Renamed ledger",
    "application_category": "SaaS",
    "deployment_status": "production",
    "version": "4.2",
    "businessDomain": "Finance",
    "userCount": "250",
    "businessOwner": "Ada Business",
    "techOwner": "Tom Tech",
    "devTeam": "Platform team",
    "supportLevel": "Tier 2",
    "description": "Keeps the general ledger.",
}


def _make_user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role_obj = Role.query.filter_by(name="User").first()
    user = User(
        email=f"overview-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="application_manager",
        role_id=role_obj.id if role_obj else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(db_session, org, name):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        description="Old description",
        deployment_status="development",
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a, org_b = make_org("ovw-a"), make_org("ovw-b")
    return {
        "org_a": org_a,
        "org_b": org_b,
        "user_a": _make_user(db_session, org_a, "usera"),
        "user_b": _make_user(db_session, org_b, "userb"),
        "app_a": _make_app(db_session, org_a, "Ledger"),
    }


def _edit_page(client, app_id):
    resp = client.get(f"/applications/{app_id}?edit=1&tab=overview")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _form_action(html):
    match = re.search(r'<form id="overview-edit-form"[^>]*action="([^"]+)"', html)
    assert match, "the Overview edit page has no form"
    return match.group(1)


def _csrf(html):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def test_every_changed_field_is_saved_and_shown(two_orgs, client, login_as, db_session):
    from app.models.application_portfolio import ApplicationComponent

    app_a = two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    resp = client.post(
        _form_action(html), data=dict(FORM_FIELDS, csrf_token=_csrf(html)),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    db_session.expire_all()
    saved = db_session.get(ApplicationComponent, app_a.id)
    assert saved.name == "Renamed ledger"
    assert saved.deployment_status == "production"
    assert saved.description == "Keeps the general ledger."
    assert saved.version == "4.2"
    assert saved.application_category == "SaaS"
    assert saved.business_domain == "Finance"
    assert saved.user_count == 250
    assert saved.business_owner == "Ada Business"
    assert saved.technical_lead == "Tom Tech"
    assert saved.development_team == "Platform team"
    assert saved.support_team == "Tier 2"
    page = resp.get_data(as_text=True)
    assert "Keeps the general ledger." in page
    assert "Renamed ledger" in page


def test_invalid_value_names_the_field_keeps_input_and_saves_nothing(
    two_orgs, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent

    app_a = two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    bad = dict(FORM_FIELDS, csrf_token=_csrf(html), userCount="many", description="Typed text")
    resp = client.post(_form_action(html), data=bad, follow_redirects=True)
    page = resp.get_data(as_text=True)
    assert resp.status_code == 422
    assert 'id="edit-userCount-error"' in page, "no error beside the User Count field"
    assert "User Count must be a whole number" in page
    assert "Typed text" in page and "Platform team" in page, "typed values were dropped"
    db_session.expire_all()
    saved = db_session.get(ApplicationComponent, app_a.id)
    assert saved.description == "Old description"
    assert saved.deployment_status == "development"


def test_a_status_off_the_list_is_refused_unless_unchanged(two_orgs, client, login_as, db_session):
    from app.models.application_portfolio import ApplicationComponent

    app_a = two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    resp = client.post(
        _form_action(html),
        data=dict(FORM_FIELDS, csrf_token=_csrf(html), deployment_status="Live"),
    )
    assert resp.status_code == 422
    assert "Deployment Status must be one of" in resp.get_data(as_text=True)

    # A stored value that is not on the list is shown as it is and kept.
    app_a.deployment_status = "Live"
    db_session.flush()
    html = _edit_page(client, app_a.id)
    assert '<option value="Live" selected' in html
    resp = client.post(
        _form_action(html),
        data=dict(FORM_FIELDS, csrf_token=_csrf(html), deployment_status="Live"),
    )
    assert resp.status_code in (200, 302)
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, app_a.id).deployment_status == "Live"


def test_second_organisation_cannot_save_by_id(two_orgs, client, login_as, db_session):
    from app.models.application_portfolio import ApplicationComponent

    app_a = two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    action, token = _form_action(html), _csrf(html)
    login_as(client, two_orgs["user_b"])
    resp = client.post(action, data=dict(FORM_FIELDS, csrf_token=token))
    assert resp.status_code == 404
    db_session.expire_all()
    saved = db_session.get(ApplicationComponent, app_a.id)
    assert saved.description == "Old description" and saved.name != "Renamed ledger"


def test_one_handler_saves_the_overview():
    from app import create_app

    rules = [
        rule.rule for rule in create_app("testing").url_map.iter_rules()
        if rule.rule.endswith("/overview-update")
    ]
    assert rules == ["/applications/<int:id>/overview-update"], rules


def test_form_and_assistant_read_the_same_status_list():
    from app.application_mgmt.forms import ApplicationComponentForm
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS
    from app.services.application_status import APPLICATION_STATUS_VALUES

    tool = next(t for t in TOOL_SCHEMAS if t["name"] == "update_application_status")
    assistant = tool["parameters"]["properties"]["new_status"]["enum"]
    form = [value for value, _label in ApplicationComponentForm.deployment_status.kwargs["choices"]]
    assert assistant == list(APPLICATION_STATUS_VALUES) == form
