"""A person recorded as an owner with no account is never a recipient and never breaks a reader.

Two organisations throughout. The daily date scan, the change feed and the
chat's owner answers each read the one owner record; a named person has no user
id, so each must tell people who can be told from people who cannot, and an
ambiguous name must be asked about rather than recorded as someone with no account.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, first="Una", last="Owner", persona="enterprise_architect"):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=first, last_name=last,
        organization_id=org.id, confirmed=True, enterprise_role=persona,
    )
    user.role = Role.query.filter_by(name="User").one()
    db_session.add(user)
    db_session.flush()
    return user


def _application(db_session, org, name="Ledger", **columns):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=f"{name} element", type="ApplicationComponent", layer="Application", organization_id=org.id,
    )
    db_session.add(element)
    db_session.flush()
    row = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id,
        archimate_element_id=element.id, **columns,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _own_user(db_session, org, application, user, ownership_type="primary"):
    from app.models.application_owner import ApplicationOwner

    db_session.add(ApplicationOwner(
        application_id=application.id, user_id=user.id, organization_id=org.id, ownership_type=ownership_type,
    ))
    db_session.flush()


def _own_named(db_session, org, application, name, ownership_type="business"):
    from app.models.application_owner import ApplicationOwner

    db_session.add(ApplicationOwner(
        application_id=application.id, user_id=None, person_name=name, organization_id=org.id,
        ownership_type=ownership_type,
    ))
    db_session.flush()


def _alerts(org, user=None):
    from app.models.models import Notification

    query = Notification.query.filter(Notification.organization_id == org.id, Notification.event_type.like("alert.%"))
    if user is not None:
        query = query.filter(Notification.user_id == user.id)
    return query.all()


@pytest.fixture
def world(db_session, make_org):
    org_a, org_b = make_org("named-a"), make_org("named-b")
    soon = date.today() + timedelta(days=60)
    mixed = _application(db_session, org_a, "Mixed", end_of_life_date=soon)
    only_named = _application(db_session, org_a, "OnlyNamed", end_of_life_date=soon)
    account = _user(db_session, org_a, "account", "Anna", "Account")
    outsider = _user(db_session, org_b, "outsider", "Olga", "Outsider")
    _own_user(db_session, org_a, mixed, account)
    _own_named(db_session, org_a, mixed, "Dana Outside")
    _own_named(db_session, org_a, only_named, "Only Named")
    db_session.commit()
    return {"org_a": org_a, "org_b": org_b, "mixed": mixed, "only_named": only_named,
            "account": account, "outsider": outsider}


def test_the_daily_date_scan_tells_the_owner_with_an_account_and_never_a_person_without_one(world):
    from app.services.date_alerts import scan_date_alerts

    scan_date_alerts(world["org_a"].id)  # raised "'<' not supported between 'int' and 'NoneType'"

    told = _alerts(world["org_a"], world["account"])
    assert len(told) == 1, "the owner with an account was not told once"
    assert all(row.user_id is not None for row in _alerts(world["org_a"]))
    assert {row.user_id for row in _alerts(world["org_a"])} == {world["account"].id}, (
        "an application with only a named owner told someone"
    )
    assert _alerts(world["org_b"]) == [] and _alerts(world["org_b"], world["outsider"]) == []


def test_the_scan_runs_for_an_organisation_whose_only_owners_are_named_people(db_session, make_org):
    from app.services.date_alerts import scan_date_alerts

    org = make_org("named-only")
    row = _application(db_session, org, "Solo", end_of_life_date=date.today() + timedelta(days=30))
    _own_named(db_session, org, row, "Solo Person")
    db_session.commit()
    scan_date_alerts(org.id)
    assert _alerts(org) == []
    # Nothing is planned for a person with no account: no alert is addressed to nobody.
    from app.services import change_rules, date_alerts

    rule = next(r for r in change_rules.daily_rules() if r["daily_scan"] == "support_dates")
    assert date_alerts._support_dates(rule, org.id, date.today()) == []


def test_the_change_feed_names_only_owners_with_accounts_as_recipients(world):
    from app.services.change_feed_payload import owner_user_ids

    org = world["org_a"]
    assert owner_user_ids("application_component", world["mixed"], org.id, "updated") == [world["account"].id]
    assert owner_user_ids("application_component", world["only_named"], org.id, "updated") == []


def test_a_change_to_an_application_with_a_named_owner_still_reaches_the_owner_with_an_account(world, db_session):
    from app.services.change_feed_consumer import consume_changes
    from app.services.event_log_service import relay_outbox_batch
    from app.models.models import Notification

    org = world["org_a"]
    world["mixed"].lifecycle_status = "retired"
    db_session.commit()
    while relay_outbox_batch(500):
        pass
    db_session.commit()
    consume_changes(org.id)
    sent = Notification.query.filter(
        Notification.organization_id == org.id, Notification.event_type.like("application_component.%"),
    ).all()
    assert sent and {row.user_id for row in sent} == {world["account"].id}


def test_what_would_stop_the_business_trading_names_a_named_owner(world, db_session):
    from app.modules.intelligence.services.query_catalogue import run_entry

    for application in (world["mixed"], world["only_named"]):
        application.business_criticality = "Critical"
    db_session.commit()
    result = run_entry("business_continuity_criticality", world["org_a"].id, criticality="Critical")
    owners = {row["id"]: row["owners"] for row in result["rows"]}
    assert owners[world["only_named"].id] == ["Only Named"], owners
    assert sorted(owners[world["mixed"].id]) == ["Anna Account", "Dana Outside"], owners
    assert all("not recorded" not in names for names in owners.values())
    assert all(row["owner_reason"] is None for row in result["rows"])
    # The other organisation reads nothing of this one.
    other = run_entry("business_continuity_criticality", world["org_b"].id, criticality="Critical")
    assert other["rows"] == []


def test_the_other_owner_readers_name_a_named_person_and_do_not_raise(world):
    from app.services.eol_window import owner_summaries, owner_summary

    org = world["org_a"]
    assert owner_summary(world["only_named"].id, org.id) == "Only Named"
    assert owner_summaries([world["only_named"].id, world["mixed"].id], org.id)[world["only_named"].id] == "Only Named"


# -- a name that fits two people ------------------------------------------------


@pytest.fixture
def twins(world, db_session):
    org_a, org_b = world["org_a"], world["org_b"]
    first = _user(db_session, org_a, "samone", "Sam", "Same")
    second = _user(db_session, org_a, "samtwo", "Sam", "Same")
    elsewhere = _user(db_session, org_b, "samelsewhere", "Sam", "Same")
    db_session.commit()
    return {"first": first, "second": second, "elsewhere": elsewhere}


def test_a_name_that_fits_two_people_is_told_apart_from_a_name_that_fits_nobody(world, twins):
    from app.services.element_ownership_service import resolve_user_by_name, users_named_alike

    org = world["org_a"]
    assert resolve_user_by_name("Sam Same", org.id) is None
    assert {u.id for u in users_named_alike("Sam Same", org.id)} == {twins["first"].id, twins["second"].id}
    assert users_named_alike("Nobody Atall", org.id) == []
    assert resolve_user_by_name("Nobody Atall", org.id) is None
    assert users_named_alike(twins["first"].email, org.id) == []
    assert resolve_user_by_name(twins["first"].email, org.id).id == twins["first"].id


def test_the_chat_asks_which_person_with_each_email_and_records_nothing(world, twins, client, login_as, db_session):
    from app.models.application_owner import ApplicationOwner

    application = world["only_named"]
    login_as(client, twins["first"])
    resp = client.post(
        "/ai-chat/estate-answer",
        json={"message": f"set {application.name}'s business owner to Sam Same", "remember": False},
    )
    shown = resp.get_json()
    assert shown["change_state"] == "invalid"
    html = shown["html"]
    assert "more than one person" in html
    assert twins["first"].email in html and twins["second"].email in html
    assert twins["elsewhere"].email not in html, "another organisation's person was offered"
    assert "data-change-token" not in html, "a Save was offered for an ambiguous name"
    names = [row.person_name for row in ApplicationOwner.query.filter_by(application_id=application.id).all()]
    assert "Sam Same" not in names

    # Typing the email of the one meant records that person, with an account.
    picked = client.post(
        "/ai-chat/estate-answer",
        json={"message": f"set {application.name}'s business owner to {twins['second'].email}", "remember": False},
    ).get_json()
    assert picked["change_state"] == "preview"


# -- a malformed email -----------------------------------------------------------


def test_an_email_that_is_not_text_is_refused_in_words_not_with_a_server_error(world, client, login_as, db_session):
    from app.models.application_owner import ApplicationOwner

    application = world["only_named"]
    before = ApplicationOwner.query.filter_by(application_id=application.id).count()
    login_as(client, world["account"])
    for bad in (5, ["a@b.example"], {"x": 1}):
        resp = client.post(
            f"/applications/{application.id}/owners",
            json={"person_name": "Pat Person", "person_email": bad, "ownership_type": "technical"},
        )
        assert resp.status_code == 400, (bad, resp.status_code)
        assert "must be text" in resp.get_json()["error"]
    assert ApplicationOwner.query.filter_by(application_id=application.id).count() == before


def test_the_service_refuses_an_email_that_is_not_text(world, db_session):
    from app.services.element_ownership_service import set_named_person_owner

    with pytest.raises(ValueError, match="must be text"):
        set_named_person_owner(
            world["only_named"], "Pat Person", "technical", organization_id=world["org_a"].id, email=5,
        )
