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


def _make_user(db_session, org, label, persona="enterprise_architect", first=None, last="Test",
               platform_role="User"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"overview-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=first or label.capitalize(),
        last_name=last,
        organization_id=org.id,
        confirmed=True,
        enterprise_role=persona,
    )
    # The constructor gives a new account the default role, so set the named one after it.
    user.role = Role.query.filter_by(name=platform_role).one()
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


# ---------------------------------------------------------------------------
# An owner on record, who may edit, field lengths, one status list, ended apps.
# ---------------------------------------------------------------------------


def _owned_app(db_session, org, owner):
    """An application whose business owner is ``owner`` (a user), on record."""
    row = _make_app(db_session, org, "Owned")
    row.business_owner = f"{owner.first_name} {owner.last_name}"
    db_session.flush()
    db_session.expire(row)
    assert row.business_owner == f"{owner.first_name} {owner.last_name}"
    return row


def _post(client, app_id, html, **fields):
    return client.post(_form_action(html), data=dict(FORM_FIELDS, csrf_token=_csrf(html), **fields))


def test_an_owner_name_matching_no_user_is_refused_beside_the_field(
    two_orgs, client, login_as, db_session
):
    org = two_orgs["org_a"]
    owner = _make_user(db_session, org, "olive", first="Olive", last="Owner")
    owned = _owned_app(db_session, org, owner)
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, owned.id)
    resp = _post(client, owned.id, html, businessOwner="Nobody Atall")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 422
    assert 'id="edit-businessOwner-error"' in page
    assert "no one in this organisation is called" in page
    assert "Nobody Atall" in page, "what was typed was dropped"
    assert "Application updated" not in page
    db_session.expire_all()
    assert db_session.get(type(owned), owned.id).business_owner == "Olive Owner"


def test_a_cleared_owner_field_is_refused_in_words(two_orgs, client, login_as, db_session):
    org = two_orgs["org_a"]
    owner = _make_user(db_session, org, "olive", first="Olive", last="Owner")
    owned = _owned_app(db_session, org, owner)
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, owned.id)
    resp = _post(client, owned.id, html, businessOwner="")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 422
    assert "cannot be cleared here" in page
    db_session.expire_all()
    assert db_session.get(type(owned), owned.id).business_owner == "Olive Owner"


def test_an_owner_name_matching_a_user_changes_the_owner(two_orgs, client, login_as, db_session):
    org = two_orgs["org_a"]
    owner = _make_user(db_session, org, "olive", first="Olive", last="Owner")
    other = _make_user(db_session, org, "pat", first="Pat", last="Peer")
    owned = _owned_app(db_session, org, owner)
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, owned.id)
    resp = _post(client, owned.id, html, businessOwner="Pat Peer")
    assert resp.status_code == 302
    db_session.expire_all()
    assert db_session.get(type(owned), owned.id).business_owner == "Pat Peer"
    assert other.organization_id == org.id


def test_a_blank_owner_clears_text_kept_on_an_application_with_no_owner_on_record(
    two_orgs, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent

    app_a = two_orgs["app_a"]
    app_a.legacy_business_owner = "Somebody External"
    db_session.flush()
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    assert "Somebody External" in html
    resp = _post(client, app_a.id, html, businessOwner="")
    assert resp.status_code == 302
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, app_a.id).business_owner in (None, "")


def test_a_person_who_may_not_edit_gets_no_form_and_cannot_save(
    two_orgs, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent

    org, app_a = two_orgs["org_a"], two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])
    editor_html = _edit_page(client, app_a.id)
    action, token = _form_action(editor_html), _csrf(editor_html)
    for persona, platform_role in (
        ("procurement_manager", "User"), ("enterprise_architect", "Viewer"),
    ):
        reader = _make_user(db_session, org, persona[:6], persona=persona, platform_role=platform_role)
        login_as(client, reader)
        page = client.get(f"/applications/{app_a.id}?edit=1&tab=overview")
        text = page.get_data(as_text=True)
        assert page.status_code == 200 and app_a.name in text, "the person cannot read the page"
        assert 'id="overview-edit-form"' not in text, f"{persona} is given the edit form"
        assert "?edit=1" not in text, f"{persona} is offered an Edit link"
        resp = client.post(action, data=dict(FORM_FIELDS, csrf_token=token))
        assert resp.status_code == 403, f"{persona} saved through the route"
    db_session.expire_all()
    saved = db_session.get(ApplicationComponent, app_a.id)
    assert saved.description == "Old description" and saved.name == app_a.name


def test_fields_hold_what_their_columns_hold_and_a_longer_value_is_refused(
    two_orgs, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_overview_fields import OVERVIEW_TEXT_FIELDS, overview_limits

    columns = ApplicationComponent.__table__.columns
    limits = overview_limits()
    assert limits["application_category"] == columns["application_category"].type.length == 50
    assert limits["techOwner"] == columns["technical_lead"].type.length == 100
    assert limits["businessOwner"] == columns["business_owner"].type.length == 100
    assert set(limits) == set(OVERVIEW_TEXT_FIELDS)

    app_a = two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    for key in ("application_category", "techOwner", "devTeam", "supportLevel", "businessDomain",
                "businessOwner", "version", "name"):
        field = re.search(r'<input[^>]*id="edit-%s"[^>]*>' % key, html)
        assert field, key
        assert 'maxlength="%d"' % limits[key] in field.group(0), (key, field.group(0))

    resp = _post(client, app_a.id, html, application_category="x" * 60, techOwner="y" * 150,
                 description="Typed text")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 422
    assert 'id="edit-application_category-error"' in page
    assert "Category must be 50 characters or fewer." in page
    assert "Technical Lead must be 100 characters or fewer." in page
    assert "Typed text" in page and "x" * 60 in page, "what was typed was dropped"
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, app_a.id).description == "Old description"

    exact = _post(client, app_a.id, html, application_category="c" * 50, techOwner="t" * 100)
    assert exact.status_code == 302
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, app_a.id).application_category == "c" * 50


def test_a_user_count_of_a_superscript_digit_is_refused_not_an_error(
    two_orgs, client, login_as
):
    app_a = two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    resp = _post(client, app_a.id, html, userCount="\u00b2")
    assert resp.status_code == 422
    assert "User Count must be a whole number" in resp.get_data(as_text=True)


def test_a_stored_status_with_a_trailing_space_still_saves_the_other_fields(
    two_orgs, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent

    app_a = two_orgs["app_a"]
    app_a.deployment_status = "Live "
    db_session.flush()
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    resp = _post(client, app_a.id, html, deployment_status="Live ")
    assert resp.status_code == 302
    db_session.expire_all()
    saved = db_session.get(ApplicationComponent, app_a.id)
    assert saved.deployment_status == "Live ", "the stored value was rewritten"
    assert saved.description == "Keeps the general ledger."


def test_every_screen_that_offers_a_status_reads_the_one_list(
    two_orgs, client, login_as, db_session
):
    from app.schemas.api_schemas import VALID_DEPLOYMENT_STATUS
    from app.services.application_status import APPLICATION_STATUS_VALUES

    from app.modules.import_batch.v2.services.import_validation import (
        validation_schemas_v2,
    )
    from app.services.import_validation import validation_schemas

    assert list(VALID_DEPLOYMENT_STATUS) == list(APPLICATION_STATUS_VALUES)
    # The batch import (both copies of its schemas) reads the same list.
    assert list(validation_schemas.DEPLOYMENT_STATUS_VALUES) == list(APPLICATION_STATUS_VALUES)
    assert list(validation_schemas_v2.DEPLOYMENT_STATUS_VALUES) == list(APPLICATION_STATUS_VALUES)
    app_a = two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])

    def options(html, select_id):
        block = re.search(r'<select[^>]*id="%s"[^>]*>(.*?)</select>' % select_id, html, re.S)
        assert block, select_id
        return [v for v in re.findall(r'<option[^>]*value="([^"]*)"', block.group(1)) if v]

    wanted = list(APPLICATION_STATUS_VALUES)
    legacy = client.get(f"/applications/{app_a.id}/edit")
    assert legacy.status_code == 200
    assert options(legacy.get_data(as_text=True), "deployment_status") == wanted
    listing = client.get("/applications/")
    assert listing.status_code == 200
    assert options(listing.get_data(as_text=True), "ca-status") == wanted
    manual = re.search(
        r'<template id="manual-entry-status-options">(.*?)</template>',
        listing.get_data(as_text=True), re.S,
    )
    assert manual and re.findall(r'value="([^"]*)"', manual.group(1)) == wanted

    # The older edit page refuses a value off the list unless it is the stored one.
    html = legacy.get_data(as_text=True)
    refused = client.post(
        f"/applications/{app_a.id}/edit",
        data={"csrf_token": _csrf(html), "name": app_a.name, "deployment_status": "Live"},
    )
    assert refused.status_code == 400
    assert "Deployment Status must be one of" in refused.get_data(as_text=True)


def test_an_application_at_the_end_of_its_life_is_flagged_everywhere(
    two_orgs, client, login_as, db_session
):
    app_a = two_orgs["app_a"]
    login_as(client, two_orgs["user_a"])
    for stored, banner in (("decommissioned", "RETIRED"), ("retiring", "RETIRING"),
                           ("retired", "RETIRED"), ("deprecated", "RETIRED")):
        app_a.deployment_status = stored
        db_session.flush()
        page = client.get(f"/applications/{app_a.id}").get_data(as_text=True)
        assert banner + " \u2014" in page, f"no banner for {stored}"
        rows = client.get("/dashboard/api/applications/table-data?per_page=100&search=" + app_a.name)
        assert rows.status_code == 200, rows.status_code
        mine = [i for i in rows.get_json()["data"]["applications"] if i["id"] == str(app_a.id)]
        assert mine and mine[0]["deployment_status"] == "Retired", (stored, mine)
    app_a.deployment_status = "production"
    db_session.flush()
    page = client.get(f"/applications/{app_a.id}").get_data(as_text=True)
    assert "RETIRED \u2014" not in page and "RETIRING \u2014" not in page


def _status_select(html, select_id):
    block = re.search(r'<select[^>]*id="%s"[^>]*>(.*?)</select>' % select_id, html, re.S)
    assert block, select_id
    return re.findall(r'<option([^>]*)value="([^"]*)"([^>]*)>', block.group(1))


@pytest.mark.parametrize("stored", ["Production", " PRODUCTION "])
def test_a_stored_status_in_another_case_is_offered_once_and_selected(
    two_orgs, client, login_as, db_session, stored
):
    app_a = two_orgs["app_a"]
    app_a.deployment_status = stored
    db_session.flush()
    login_as(client, two_orgs["user_a"])
    overview = _status_select(_edit_page(client, app_a.id), "edit-deployment_status")
    legacy = _status_select(
        client.get(f"/applications/{app_a.id}/edit").get_data(as_text=True), "deployment_status"
    )
    for options in (overview, legacy):
        values = [v for _a, v, _b in options if v]
        assert values.count("production") == 1 and len(values) == 7, values
        chosen = [v for a, v, b in options if "selected" in a + b]
        assert chosen == ["production"], chosen


def test_a_stored_status_in_another_case_is_kept_on_save_and_changes_when_picked(
    two_orgs, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent

    app_a = two_orgs["app_a"]
    app_a.deployment_status = "Production"
    db_session.flush()
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, app_a.id)
    assert _post(client, app_a.id, html, deployment_status="production").status_code == 302
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, app_a.id).deployment_status == "Production"
    assert _post(client, app_a.id, html, deployment_status="Retiring ").status_code == 302
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, app_a.id).deployment_status == "retiring"


def test_the_older_edit_page_saves_when_the_stored_status_has_a_trailing_space(
    two_orgs, client, login_as, db_session
):
    from app.models.application_portfolio import ApplicationComponent

    app_a = two_orgs["app_a"]
    app_a.deployment_status = "Live "
    db_session.flush()
    login_as(client, two_orgs["user_a"])
    html = client.get(f"/applications/{app_a.id}/edit").get_data(as_text=True)
    resp = client.post(
        f"/applications/{app_a.id}/edit",
        data={"csrf_token": _csrf(html), "name": app_a.name, "deployment_status": "Live"},
    )
    assert resp.status_code != 400, resp.get_data(as_text=True)[:300]
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, app_a.id).deployment_status == "Live "


def test_an_owner_name_two_people_share_asks_for_an_email_not_a_missing_person(
    two_orgs, client, login_as, db_session
):
    org = two_orgs["org_a"]
    owner = two_orgs["user_a"]  # the plan admits three people: owner, and the two of one name
    _make_user(db_session, org, "sam1", first="Sam", last="Twin")
    _make_user(db_session, org, "sam2", first="Sam", last="Twin")
    owned = _owned_app(db_session, org, owner)
    login_as(client, two_orgs["user_a"])
    html = _edit_page(client, owned.id)
    page = _post(client, owned.id, html, businessOwner="Sam Twin").get_data(as_text=True)
    assert "2 people in this organisation are called" in page
    assert "Type the email of the one you mean" in page
    assert "no one in this organisation is called" not in page
    page = _post(client, owned.id, html, businessOwner="Nobody Atall").get_data(as_text=True)
    assert "no one in this organisation is called" in page
