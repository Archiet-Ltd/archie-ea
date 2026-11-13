"""Six small faults on an application's page and its full editor, one test each.

Two organisations throughout: the connector note on the editor names Abacus only
for the organisation that has the connector.
"""

from __future__ import annotations

import re
import uuid

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"faults-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    user.role = Role.query.filter_by(name="User").one()
    db_session.add(user)
    db_session.flush()
    return user


def _app(db_session, org, **fields):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=f"Faults {uuid.uuid4().hex[:6]}", organization_id=org.id, **fields
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def world(db_session, make_org):
    from app.models.connector_config import ConnectorConfig

    org_a, org_b = make_org("flt-a"), make_org("flt-b")
    db_session.add(
        ConnectorConfig(
            organization_id=org_a.id, connector_type="abacus", name="Abacus",
            config={"base_url": "https://abacus.example.com/api/"}, status="active",
        )
    )
    db_session.flush()
    return {
        "org_a": org_a,
        "org_b": org_b,
        "user_a": _user(db_session, org_a, "usera"),
        "user_b": _user(db_session, org_b, "userb"),
    }


def _page(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, path
    return resp.get_data(as_text=True)


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_no_hint_sends_a_person_to_a_control_that_does_not_exist(world, client, login_as, db_session):
    row = _app(db_session, world["org_a"])
    login_as(client, world["user_a"])
    text = _text(_page(client, f"/applications/{row.id}"))
    assert "add one in Architecture" not in text
    assert "None linked" in text


def test_unset_competitive_advantage_reads_not_recorded_and_is_editable(
    world, client, login_as, db_session
):
    row = _app(db_session, world["org_a"])
    login_as(client, world["user_a"])
    text = _text(_page(client, f"/applications/{row.id}"))
    assert "Competitive Advantage Not recorded" in text
    assert "Competitive Advantage No " not in text

    edit = _page(client, f"/applications/{row.id}/edit")
    assert 'name="competitive_advantage"' in edit, "shown on the page but not editable"
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', edit).group(1)
    resp = client.post(
        f"/applications/{row.id}/edit",
        data={"csrf_token": token, "name": row.name, "competitive_advantage": "yes"},
    )
    assert resp.status_code in (200, 302)
    text = _text(_page(client, f"/applications/{row.id}"))
    assert "Competitive Advantage Yes" in text


def test_category_keeps_the_capitals_a_person_typed(world, client, login_as, db_session):
    from app.models.application_portfolio import ApplicationComponent
    row = _app(db_session, world["org_a"], application_category="AI code generation platform")
    login_as(client, world["user_a"])
    text = _text(_page(client, f"/applications/{row.id}"))
    assert "AI Code Generation Platform" in text
    assert "Ai Code" not in text
    assert db_session.get(ApplicationComponent, row.id).application_category == (
        "AI code generation platform"
    )
    from app.utils.template_utils import title_keep_caps

    assert title_keep_caps("single_sign_on") == "Single Sign On"
    assert title_keep_caps("SAML gateway") == "SAML Gateway"
    assert title_keep_caps("ENTERPRISE") == "Enterprise"


def test_identity_card_is_not_drawn_without_rows(world, client, login_as, db_session):
    row = _app(db_session, world["org_a"])
    db_session.execute(
        db.text(
            "UPDATE application_components SET lifecycle_status = NULL, application_category = NULL,"
            " application_type = NULL, deployment_model = NULL, architecture_domain = NULL,"
            " version = NULL, application_code = NULL WHERE id = :i"
        ),
        {"i": row.id},
    )
    db_session.expire_all()
    login_as(client, world["user_a"])
    html = _page(client, f"/applications/{row.id}")
    assert "Application Identity" not in html

    full = _app(db_session, world["org_a"], application_category="ERP")
    html = _page(client, f"/applications/{full.id}")
    assert "Application Identity" in html


def test_edit_page_has_one_breadcrumb_trail(world, client, login_as, db_session):
    row = _app(db_session, world["org_a"])
    login_as(client, world["user_a"])
    html = _page(client, f"/applications/{row.id}/edit")
    trails = re.findall(r'<nav[^>]*aria-label="Breadcrumb"', html)
    assert len(trails) == 1, len(trails)
    nav = re.search(r'<nav[^>]*aria-label="Breadcrumb".*?</nav>', html, re.S).group(0)
    trail = [t for t in re.sub(r"<[^>]+>", "\n", nav).split("\n") if t.strip() and t.strip() != ">"]
    assert [t.strip() for t in trail if t.strip() not in ("/", "›")] == [
        "Home", "Systems", "Applications", "Edit Application",
    ], trail


def test_abacus_is_named_only_for_the_organisation_that_has_it(world, client, login_as, db_session):
    mine = _app(db_session, world["org_a"])
    theirs = _app(db_session, world["org_b"])
    login_as(client, world["user_a"])
    assert "Managed by Abacus sync" in _page(client, f"/applications/{mine.id}/edit")
    login_as(client, world["user_b"])
    assert "Abacus" not in _page(client, f"/applications/{theirs.id}/edit")


def test_authentication_method_shows_on_the_technology_tab(world, client, login_as, db_session):
    row = _app(db_session, world["org_a"], authentication_method="SAML single sign-on")
    login_as(client, world["user_a"])
    text = _text(_page(client, f"/applications/{row.id}?tab=technology"))
    assert "Authentication Method SAML single sign-on" in text


def _save_advantage(client, row, value):
    edit = _page(client, f"/applications/{row.id}/edit")
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', edit).group(1)
    resp = client.post(
        f"/applications/{row.id}/edit",
        data={"csrf_token": token, "name": row.name, "competitive_advantage": value},
    )
    assert resp.status_code in (200, 302)


def test_a_recorded_no_reads_no_and_an_unset_one_reads_not_recorded(
    world, client, login_as, db_session
):
    chosen = _app(db_session, world["org_a"])
    never = _app(db_session, world["org_a"])
    other_org = _app(db_session, world["org_b"])
    login_as(client, world["user_a"])
    edit = _text(_page(client, f"/applications/{chosen.id}/edit"))
    assert "Not recorded" in edit and "Yes" in edit and "No" in edit.split("Competitive Advantage")[1][:80]

    _save_advantage(client, chosen, "no")
    assert "Competitive Advantage No " in _text(_page(client, f"/applications/{chosen.id}")) + " "
    assert "Competitive Advantage Not recorded" in _text(_page(client, f"/applications/{never.id}"))

    # The editor shows what was chosen, and a person can go back to unset.
    html = _page(client, f"/applications/{chosen.id}/edit")
    assert re.search(r'<option[^>]*value="no"[^>]*selected', html)
    _save_advantage(client, chosen, "")
    assert "Competitive Advantage Not recorded" in _text(_page(client, f"/applications/{chosen.id}"))

    login_as(client, world["user_b"])
    assert "Competitive Advantage Not recorded" in _text(_page(client, f"/applications/{other_org.id}"))


def test_acronyms_keep_their_capitals_in_any_title_cased_label(world, client, login_as, db_session):
    from app.utils.template_utils import title_keep_caps

    assert title_keep_caps("SaaS") == "SaaS"
    assert title_keep_caps("saas_platform") == "Saas Platform"
    assert title_keep_caps("SaaS billing API") == "SaaS Billing API"
    assert title_keep_caps("IoT gateway") == "IoT Gateway"
    assert title_keep_caps("AI") == "AI"
    row = _app(db_session, world["org_a"], application_category="SaaS billing", application_type="IoT API")
    login_as(client, world["user_a"])
    text = _text(_page(client, f"/applications/{row.id}"))
    assert "SaaS Billing" in text and "IoT API" in text
    assert "Saas" not in text and "Iot " not in text


def test_there_is_one_title_case_helper(app):
    from app.utils.template_utils import ENUM_LABEL_OVERRIDES, title_keep_caps

    filters = app.jinja_env.filters
    assert filters["humanize_key"] is title_keep_caps
    assert title_keep_caps("arb_submission") == ENUM_LABEL_OVERRIDES["arb_submission"]
    assert title_keep_caps("some_new_SaaS_code") == "Some New SaaS Code"


def test_a_hyphen_in_a_typed_label_is_kept_and_each_part_is_capitalised():
    from app.utils.template_utils import title_keep_caps

    assert title_keep_caps("off-the-shelf") == "Off-The-Shelf"
    assert title_keep_caps("line-of-business app") == "Line-Of-Business App"
    assert title_keep_caps("single_sign_on") == "Single Sign On"
