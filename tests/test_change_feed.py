"""The change feed: what changed, who owns it, where it came from.

Every test uses two real organisations. A change is made through the ORM the way
a route would make it, committed, relayed from the outbox into the event log,
and then consumed into bell notifications.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")

SECRET_KEY_PATTERN = re.compile(r"(password|passwd|secret|token|credential|api_?key)", re.I)


# -- helpers ------------------------------------------------------------------


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    user = User(
        email=f"feed-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Owner",
        organization_id=org.id,
        confirmed=True,
        role_id=role.id if role else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _application(db_session, org, name="Ledger", **columns):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id, **columns
    )
    db_session.add(row)
    db_session.flush()
    return row


def _own(db_session, org, application, user, ownership_type="primary"):
    from app.models.application_owner import ApplicationOwner

    row = ApplicationOwner(
        application_id=application.id,
        user_id=user.id,
        organization_id=org.id,
        ownership_type=ownership_type,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _relay():
    from app.services.event_log_service import relay_outbox_batch

    while relay_outbox_batch(500):
        pass
    db.session.commit()  # the relay and the consumer are separate transactions


def _events(org, event_type=None):
    from app.services.event_log_service import read_from_offset

    rows = read_from_offset(org.id, 0, limit=5000)
    if event_type:
        rows = [r for r in rows if r["event_type"] == event_type]
    return rows


def _notifications(org, user=None):
    from app.models.models import Notification

    query = Notification.query.filter(
        Notification.organization_id == org.id,
        Notification.event_type.like("application_component.%"),
    )
    if user is not None:
        query = query.filter(Notification.user_id == user.id)
    return query.all()


def _consume(org):
    from app.services.change_feed_consumer import consume_changes

    return consume_changes(org.id)


def _offset(org):
    from app.models.change_feed_offset import ChangeFeedOffset

    row = ChangeFeedOffset.query.filter_by(organization_id=org.id).first()
    return row.last_ordinal if row else 0


@pytest.fixture
def two_orgs(db_session, make_org):
    return {"A": make_org("feed-a"), "B": make_org("feed-b")}


@pytest.fixture
def lifecycle_change(db_session, two_orgs):
    """Organisation A: an application with two owners whose lifecycle changes.
    Organisation B: an application with its own owner, changed the same way."""
    org_a, org_b = two_orgs["A"], two_orgs["B"]
    owner_1, owner_2 = _user(db_session, org_a, "one"), _user(db_session, org_a, "two")
    bystander = _user(db_session, org_a, "bystander")
    app_a = _application(db_session, org_a, lifecycle_status="planning")
    _own(db_session, org_a, app_a, owner_1)
    _own(db_session, org_a, app_a, owner_2, "backup")

    owner_b = _user(db_session, org_b, "bee")
    app_b = _application(db_session, org_b, lifecycle_status="planning")
    _own(db_session, org_b, app_b, owner_b)
    db_session.commit()

    # Set without reading first: the before value must still be recorded.
    app_a.lifecycle_status = "operational"
    app_b.lifecycle_status = "retired"
    db_session.commit()
    _relay()
    return {
        "orgs": (org_a, org_b),
        "owners": (owner_1, owner_2),
        "bystander": bystander,
        "owner_b": owner_b,
        "apps": (app_a, app_b),
    }


# -- the payload --------------------------------------------------------------


def test_lifecycle_change_event_says_what_changed_who_owns_it_and_where_from(lifecycle_change):
    org_a, _ = lifecycle_change["orgs"]
    app_a, _ = lifecycle_change["apps"]
    owner_1, owner_2 = lifecycle_change["owners"]

    events = [
        e for e in _events(org_a, "application_component.updated")
        if e["entity_id"] == app_a.id
    ]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["changes"] == [
        {"field": "lifecycle_status", "before": "planning", "after": "operational"}
    ]
    assert sorted(payload["owner_user_ids"]) == sorted([owner_1.id, owner_2.id])
    assert payload["source"]["kind"] in {"user", "connector", "import", "system"}


def test_source_names_the_connector_or_import_that_made_the_change(db_session, two_orgs):
    org = two_orgs["A"]
    app_row = _application(db_session, org, lifecycle_status="planning")
    db_session.commit()
    db_session.info["change_source"] = {"kind": "connector", "name": "jira", "origin": "sync-42"}
    try:
        app_row.lifecycle_status = "operational"
        db_session.commit()
    finally:
        db_session.info.pop("change_source", None)
    _relay()
    payload = [
        e for e in _events(org, "application_component.updated") if e["entity_id"] == app_row.id
    ][0]["payload"]
    assert payload["source"] == {"kind": "connector", "name": "jira", "origin": "sync-42"}


def test_a_save_that_changes_nothing_watched_emits_nothing(db_session, two_orgs):
    org = two_orgs["A"]
    app_row = _application(db_session, org, lifecycle_status="planning")
    db_session.commit()
    app_row.lifecycle_status = "operational"
    db_session.commit()
    _relay()
    assert len(_events(org, "application_component.updated")) == 1
    app_row.description = "only prose changed"
    db_session.commit()
    _relay()
    assert len(_events(org, "application_component.updated")) == 1


def test_support_date_cost_owner_and_risk_changes_reach_the_same_feed(db_session, two_orgs):
    from datetime import date

    from app.models.cost_fact import CostFact
    from app.models.risk import Risk

    org = two_orgs["A"]
    owner = _user(db_session, org, "dates")
    app_row = _application(db_session, org, end_of_life_date=date(2027, 1, 1))
    fact = CostFact(
        organization_id=org.id, element_type="application", element_id=app_row.id,
        category="total", kind="actual", amount=Decimal("100"), currency="USD",
        period="annual", source="test",
    )
    risk = Risk(organization_id=org.id, title="Vendor exit", likelihood=2, impact=3)
    db_session.add_all([fact, risk])
    db_session.commit()
    _own(db_session, org, app_row, owner)
    db_session.commit()

    app_row.end_of_life_date = date(2026, 12, 1)
    fact.amount = Decimal("250")
    risk.likelihood = 5
    db_session.commit()
    _relay()

    def changes(event_type):
        rows = _events(org, event_type)
        assert rows, event_type
        return rows[-1]["payload"]["changes"]

    assert {"field": "end_of_life_date", "before": "2027-01-01", "after": "2026-12-01"} in changes(
        "application_component.updated"
    )
    assert {"field": "amount", "before": "100.0000", "after": "250"} in changes("cost_fact.updated") or {
        "field": "amount", "before": "100", "after": "250"
    } in changes("cost_fact.updated")
    assert {"field": "likelihood", "before": 2, "after": 5} in changes("risk.updated")
    assert _events(org, "application_owner.created"), "an owner assignment is a change too"


def test_no_payload_holds_a_credential_token_or_password(db_session, two_orgs):
    from app.models.archimate_core import ArchiMateElement
    from app.services.change_feed_payload import scrub_payload

    org = two_orgs["A"]
    element = ArchiMateElement(
        name="Gateway", type="ApplicationComponent", layer="Application",
        organization_id=org.id,
        custom_properties={"api_key": "s3cr3t-value", "password": "hunter2"},
    )
    db_session.add(element)
    db_session.commit()
    element.name = "Gateway v2"
    element.custom_properties = {"api_key": "rotated-s3cr3t", "password": "hunter3"}
    db_session.commit()
    _relay()

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, inner in value.items():
                assert not SECRET_KEY_PATTERN.search(str(key)), f"{path}/{key}"
                walk(inner, f"{path}/{key}")
        elif isinstance(value, list):
            for inner in value:
                walk(inner, path)
        else:
            assert "s3cr3t" not in str(value) and "hunter" not in str(value), path

    rows = _events(org)
    assert rows
    for row in rows:
        walk(row["payload"])

    cleaned = scrub_payload(
        {"changes": [{"field": "access_token", "before": "a", "after": "b"}], "password": "x", "name": "ok"}
    )
    assert cleaned == {"changes": [], "name": "ok"}


# -- the consumer -------------------------------------------------------------


def test_one_bell_notification_for_each_owner_linking_to_the_cited_answer(lifecycle_change):
    org_a, _ = lifecycle_change["orgs"]
    app_a, _ = lifecycle_change["apps"]
    owner_1, owner_2 = lifecycle_change["owners"]

    _consume(org_a)

    for owner in (owner_1, owner_2):
        rows = _notifications(org_a, owner)
        assert len(rows) == 1
        note = rows[0]
        assert note.event_type == "application_component.updated"
        assert note.entity_type == "application_component"
        assert note.entity_id == app_a.id
        assert "planning" in note.message and "operational" in note.message
        assert note.url.startswith("/intelligence/")
        assert note.read is False
    assert _notifications(org_a, lifecycle_change["bystander"]) == []


def test_running_the_consumer_twice_writes_no_second_notification(lifecycle_change):
    org_a, _ = lifecycle_change["orgs"]
    _consume(org_a)
    first = len(_notifications(org_a))
    assert first == 2
    _consume(org_a)
    assert len(_notifications(org_a)) == first


def test_replaying_the_same_range_writes_nothing_new(db_session, lifecycle_change):
    from app.models.change_feed_offset import ChangeFeedOffset

    org_a, _ = lifecycle_change["orgs"]
    _consume(org_a)
    first = len(_notifications(org_a))
    advanced = _offset(org_a)
    assert advanced > 0

    ChangeFeedOffset.query.filter_by(organization_id=org_a.id).update({"last_ordinal": 0})
    db_session.commit()
    _consume(org_a)

    assert len(_notifications(org_a)) == first
    assert _offset(org_a) == advanced


def test_offset_advances_only_after_the_notifications_commit(db_session, lifecycle_change, monkeypatch):
    org_a, _ = lifecycle_change["orgs"]
    assert _offset(org_a) == 0

    def refuse(*_args, **_kwargs):
        raise RuntimeError("commit refused")

    monkeypatch.setattr(db_session, "commit", refuse)
    with pytest.raises(RuntimeError):
        _consume(org_a)
    monkeypatch.undo()
    db_session.rollback()

    assert _offset(org_a) == 0
    assert _notifications(org_a) == []

    _consume(org_a)
    assert _offset(org_a) > 0
    assert len(_notifications(org_a)) == 2


def test_another_organisations_change_never_notifies_this_ones_users(db_session, lifecycle_change):
    org_a, org_b = lifecycle_change["orgs"]
    owner_b = lifecycle_change["owner_b"]
    owner_1, owner_2 = lifecycle_change["owners"]

    _consume(org_a)
    _consume(org_b)

    assert [n.user_id for n in _notifications(org_b)] == [owner_b.id]
    assert {n.user_id for n in _notifications(org_a)} == {owner_1.id, owner_2.id}
    assert _notifications(org_a, owner_b) == []
    assert _notifications(org_b, owner_1) == []


def test_an_owner_id_from_another_organisation_in_a_payload_is_not_notified(db_session, two_orgs):
    from app.services.outbox import emit_event

    org_a, org_b = two_orgs["A"], two_orgs["B"]
    stranger = _user(db_session, org_b, "stranger")
    insider = _user(db_session, org_a, "insider")
    emit_event(
        organization_id=org_a.id,
        event_type="application_component.updated",
        entity_type="application_component",
        entity_id=999,
        payload={
            "action": "updated",
            "id": 999,
            "name": "Crafted",
            "changes": [{"field": "lifecycle_status", "before": "a", "after": "b"}],
            "owner_user_ids": [stranger.id, insider.id],
            "source": {"kind": "system"},
        },
    )
    db_session.commit()
    _relay()
    _consume(org_a)

    from app.models.models import Notification

    assert Notification.query.filter_by(user_id=stranger.id).count() == 0
    assert Notification.query.filter_by(user_id=insider.id).count() == 1


# -- the rules file -----------------------------------------------------------


def test_rules_are_plain_data_and_only_fire_on_their_field():
    from app.services import change_rules

    assert change_rules.RULES, "the starter rule must exist"
    for rule in change_rules.RULES:
        assert set(rule) >= {"event_type", "when_field_changed", "tell", "message"}
        assert all(not callable(value) for value in rule.values())

    quiet = {
        "event_type": "application_component.updated",
        "entity_type": "application_component",
        "entity_id": 7,
        "payload": {
            "name": "Ledger",
            "changes": [{"field": "end_of_life_date", "before": None, "after": "2040-01-01"}],
            "owner_user_ids": [1],
        },
    }
    assert change_rules.candidates_for(quiet) == []

    loud = {**quiet, "payload": {**quiet["payload"], "archimate_element_id": 55, "changes": [
        {"field": "lifecycle_status", "before": "planning", "after": "operational"}]}}
    candidates = change_rules.candidates_for(loud)
    assert [c["user_id"] for c in candidates] == [1]
    assert "Ledger" in candidates[0]["message"]
    assert candidates[0]["url"] == "/intelligence/traceability?element=55"


def test_the_consumer_is_scheduled_as_a_declared_tenant_job():
    from app.jobs.tenant_safe_job import TENANT_JOBS

    assert "change_feed_consumer" in TENANT_JOBS
