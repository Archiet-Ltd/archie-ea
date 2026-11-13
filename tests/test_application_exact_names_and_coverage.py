"""Saving an Overview whose owner shares a name, a chat change that names one exact application,
and a capability coverage that nobody entered.

Two organisations throughout: the other organisation's people and applications are never found.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, first=None, last="Test", platform_role="User"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=first or label.capitalize(),
        last_name=last, organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect",
    )
    user.role = Role.query.filter_by(name=platform_role).one()
    db_session.add(user)
    db_session.flush()
    return user


def _application(db_session, org, name, **columns):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id, **columns)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("exact-a"), make_org("exact-b")
    tag = uuid.uuid4().hex[:5]
    return {
        "org_a": org_a, "org_b": org_b, "tag": tag,
        "editor": _user(db_session, org_a, "editor"),
        "user_b": _user(db_session, org_b, "userb"),
    }


def _ask(client, text):
    resp = client.post("/ai-chat/estate-answer", json={"message": text, "remember": False})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


# -- saving the Overview twice ----------------------------------------------------------


def _form_action(html):
    match = re.search(r'<form id="overview-edit-form"[^>]*action="([^"]+)"', html)
    assert match, "the Overview edit page has no form"
    return match.group(1)


def _csrf(html):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def test_an_owner_who_shares_a_name_does_not_block_the_next_save(world, client, login_as, db_session):
    from app.models.application_portfolio import ApplicationComponent

    org = world["org_a"]
    pick = _user(db_session, org, "samone", first="Sam", last="Same")
    _user(db_session, org, "samtwo", first="Sam", last="Same")
    outsider = _user(db_session, world["org_b"], "samother", first="Sam", last="Same")
    app_a = _application(db_session, org, f"Ledger {world['tag']}", description="Old description")
    login_as(client, world["editor"])

    def save(**fields):
        html = client.get(f"/applications/{app_a.id}?edit=1&tab=overview").get_data(as_text=True)
        data = {"name": app_a.name, "csrf_token": _csrf(html)}
        data.update(fields)
        return client.post(_form_action(html), data=data)

    first = save(businessOwner=pick.email)
    assert first.status_code in (200, 302), first.get_data(as_text=True)[:400]
    db_session.expire_all()
    assert db_session.get(ApplicationComponent, app_a.id).business_owner == "Sam Same"

    # Only the description changes: the owner field holds the name the form shows.
    second = save(businessOwner="Sam Same", description="Keeps the general ledger.")
    assert second.status_code in (200, 302), second.get_data(as_text=True)[:400]
    assert "matches more than one person" not in second.get_data(as_text=True)
    db_session.expire_all()
    saved = db_session.get(ApplicationComponent, app_a.id)
    assert saved.description == "Keeps the general ledger."
    from app.models.application_owner import ApplicationOwner

    owner = ApplicationOwner.query.filter_by(
        application_id=app_a.id, organization_id=org.id, ownership_type="business",
    ).one()
    assert owner.user_id == pick.id, "the owner stays the person picked"

    # Changing to the shared name from a different owner still asks which person.
    other = _application(db_session, org, f"Other {world['tag']}", description="Old")
    html = client.get(f"/applications/{other.id}?edit=1&tab=overview").get_data(as_text=True)
    refused = client.post(_form_action(html), data={
        "name": other.name, "businessOwner": "Sam Same", "csrf_token": _csrf(html)})
    refusal = refused.get_data(as_text=True)
    assert refused.status_code == 422 and "matches more than one person" in refusal
    assert "2 people in this organisation" in refusal, "only the asker's organisation is counted"
    assert pick.email in refusal
    assert outsider.email not in refusal, "another organisation's person is never offered"


# -- a chat change names one exact application ---------------------------------------


def test_a_partial_name_gets_the_closest_names_and_no_preview(world, client, login_as, db_session):
    full = f"Billing{world['tag']} Gateway"
    target = _application(db_session, world["org_a"], full, description="Old description")
    _application(db_session, world["org_b"], "Gateway", description="Theirs")
    login_as(client, world["editor"])

    partial = _ask(client, "set the description of Gateway to Takes the payments")
    assert partial.get("change_state") != "preview", partial
    assert full in partial["html"] and "Takes the payments" not in partial["html"]

    for typed in (full, full.upper(), f"  {full.lower()}  "):
        shown = _ask(client, f"set the description of {typed} to Takes the payments")
        assert shown["change_state"] == "preview", (typed, shown)
        assert full in shown["html"]
    db_session.expire_all()
    assert target.description == "Old description", "nothing is saved before Save"

    login_as(client, world["user_b"])
    theirs = _ask(client, "set the description of Gateway to Mine")
    assert theirs["change_state"] == "preview" and full not in theirs["html"]


# -- the name resolver treats % and _ as text ---------------------------------------------


def test_percent_and_underscore_in_a_name_are_not_wildcards(world, db_session):
    from app.modules.intelligence.services.query_catalogue import _resolve_application

    tag = world["tag"]
    with_percent = _application(db_session, world["org_a"], f"Rate{tag}%Tool")
    _application(db_session, world["org_a"], f"Rate{tag}9999Tool")
    with_underscore = _application(db_session, world["org_a"], f"Rate{tag}_Lab")
    _application(db_session, world["org_a"], f"Rate{tag}xLab")
    _application(db_session, world["org_b"], f"Rate{tag}%Tool")

    found, others = _resolve_application(world["org_a"].id, f"{tag}%Tool")
    assert found is not None and found.id == with_percent.id and not others
    found, others = _resolve_application(world["org_a"].id, f"{tag}_Lab")
    assert found is not None and found.id == with_underscore.id and not others
    found, others = _resolve_application(world["org_a"].id, "9999%Tool")
    assert found is None and others == []


# -- coverage nobody entered is not 0 ------------------------------------------------------


def _map(db_session, org, application, capability, **columns):
    from app.models.application_capability import ApplicationCapabilityMapping

    row = ApplicationCapabilityMapping(
        application_component_id=application.id, business_capability_id=capability.id,
        organization_id=org.id, support_level="full", **columns,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_unrecorded_coverage_reads_not_recorded_and_a_recorded_zero_reads_zero(
    world, client, login_as, db_session
):
    from app.models.business_capabilities import BusinessCapability

    org = world["org_a"]
    application = _application(db_session, org, f"Coverage {world['tag']}")
    blank = BusinessCapability(name=f"Blank {world['tag']}", organization_id=org.id)
    zero = BusinessCapability(name=f"Zero {world['tag']}", organization_id=org.id)
    db_session.add_all([blank, zero])
    db_session.flush()
    unrecorded = _map(db_session, org, application, blank)
    _map(db_session, org, application, zero, coverage_percentage=0)
    db_session.refresh(unrecorded)
    assert unrecorded.coverage_percentage is None, "nobody entered a coverage, so none is stored"

    login_as(client, world["editor"])
    answer = _ask(client, f"Which capabilities does {application.name} support?")["html"]
    page = client.get(f"/applications/{application.id}?tab=capabilities").get_data(as_text=True)
    # The chat lists each capability with its source line; the page's table has one row each.
    start = {name: answer.index(name) for name in (blank.name, zero.name)}
    chat = {}
    for name, other in ((blank.name, zero.name), (zero.name, blank.name)):
        end = start[other] if start[other] > start[name] else len(answer)
        chat[name] = answer[start[name]:end]
    table = {}
    for name in (blank.name, zero.name):
        found = re.search(r'font-medium">' + re.escape(name) + r'</span>(.*?)</tr>', page, re.S)
        assert found, f"the capabilities table has no row for {name}"
        table[name] = found.group(1)
    for label, row in (("chat", chat), ("page", table)):
        assert "Not recorded" in row[blank.name] and "0%" not in row[blank.name], label
        assert "0%" in row[zero.name] and "Not recorded" not in row[zero.name], label
    assert "Coverage: Not recorded" in chat[blank.name] and "Coverage: 0%" in chat[zero.name]
