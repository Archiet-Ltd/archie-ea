"""Alerts that reach the owners concerned, in the bell and in one daily email.

Five rules: a support date falls within 180 days, an owner is removed, a cost
changes by more than 20 per cent, a risk reaches a revenue-critical system (off
until its input exists, with the reason in the rules file), and a decision
condition becomes overdue. Every test uses two real organisations; a change is
made through the ORM the way a route makes it, relayed into the event log and
consumed into bell notifications, or found by the daily date check.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")


# -- helpers ------------------------------------------------------------------


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    user = User(
        email=f"alert-{label}-{uuid.uuid4().hex[:8]}@example.com",
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
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=f"{name} element", type="ApplicationComponent", layer="Application",
        organization_id=org.id,
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
    db.session.commit()


def _consume(org):
    from app.services.change_feed_consumer import consume_changes

    return consume_changes(org.id)


def _alerts(org, user=None, rule=None):
    from app.models.models import Notification

    query = Notification.query.filter(
        Notification.organization_id == org.id,
        Notification.event_type.like("alert.%"),
    )
    if user is not None:
        query = query.filter(Notification.user_id == user.id)
    if rule is not None:
        query = query.filter(Notification.event_type == f"alert.{rule}")
    return query.all()


def _rule(name):
    from app.services import change_rules

    return next(rule for rule in change_rules.RULES if rule.get("name") == name)


@pytest.fixture
def two_orgs(db_session, make_org):
    return {"A": make_org("alert-a"), "B": make_org("alert-b")}


@pytest.fixture
def owned_app(db_session, two_orgs):
    """Organisation A: an application with two owners and a bystander.
    Organisation B: a user who owns nothing in A."""
    org_a, org_b = two_orgs["A"], two_orgs["B"]
    owner_1, owner_2 = _user(db_session, org_a, "one"), _user(db_session, org_a, "two")
    bystander = _user(db_session, org_a, "bystander")
    outsider = _user(db_session, org_b, "outsider")
    app_row = _application(db_session, org_a, end_of_life_date=date.today() + timedelta(days=900))
    _own(db_session, org_a, app_row, owner_1)
    _own(db_session, org_a, app_row, owner_2, "backup")
    db_session.commit()
    return {
        "orgs": (org_a, org_b),
        "owners": (owner_1, owner_2),
        "bystander": bystander,
        "outsider": outsider,
        "app": app_row,
    }


# -- 1. support date within 180 days -------------------------------------------


def test_a_support_date_moved_within_180_days_alerts_each_owner_once(db_session, owned_app):
    org_a, _ = owned_app["orgs"]
    app_row = owned_app["app"]
    new_date = date.today() + timedelta(days=90)

    app_row.end_of_life_date = new_date
    db_session.commit()
    _relay()
    _consume(org_a)
    _consume(org_a)  # a second run writes nothing new

    for owner in owned_app["owners"]:
        rows = _alerts(org_a, owner, "support_date_within_180_days")
        assert len(rows) == 1
        note = rows[0]
        assert app_row.name in note.message
        assert new_date.strftime("%-d %B %Y") in note.message
        assert "because you own it" in note.message
        assert note.url.startswith(f"/intelligence/answers/element/{app_row.archimate_element_id}")
        assert not re.search(rf"(?<!\d){app_row.id}(?!\d)", note.message), "no record ids in what a person reads"
    assert _alerts(org_a, owned_app["bystander"]) == []


def test_a_support_date_beyond_180_days_alerts_no_one(db_session, owned_app):
    org_a, _ = owned_app["orgs"]
    owned_app["app"].end_of_life_date = date.today() + timedelta(days=400)
    db_session.commit()
    _relay()
    _consume(org_a)
    assert _alerts(org_a) == []


def test_the_daily_check_alerts_a_date_that_crossed_without_an_edit(db_session, owned_app):
    from app.services.date_alerts import scan_date_alerts

    org_a, _ = owned_app["orgs"]
    app_row = owned_app["app"]
    # Written the way an import writes it: no change event for the date.
    db_session.execute(
        db.text("UPDATE application_components SET end_of_life_date = :d WHERE id = :id"),
        {"d": date.today() + timedelta(days=170), "id": app_row.id},
    )
    db_session.commit()

    scan_date_alerts(org_a.id)
    scan_date_alerts(org_a.id)  # the next day's run does not repeat it

    for owner in owned_app["owners"]:
        assert len(_alerts(org_a, owner, "support_date_within_180_days")) == 1
    assert _alerts(org_a, owned_app["bystander"]) == []


def test_the_edit_and_the_daily_check_make_one_alert_not_two(db_session, owned_app):
    from app.services.date_alerts import scan_date_alerts

    org_a, _ = owned_app["orgs"]
    owned_app["app"].end_of_life_date = date.today() + timedelta(days=60)
    db_session.commit()
    _relay()
    _consume(org_a)
    scan_date_alerts(org_a.id)

    owner_1, _ = owned_app["owners"]
    assert len(_alerts(org_a, owner_1, "support_date_within_180_days")) == 1


# -- 2. owner removed ------------------------------------------------------------


def test_removing_an_owner_tells_the_owners_who_remain(db_session, two_orgs):
    from app.models.application_owner import ApplicationOwner

    org = two_orgs["A"]
    stays, leaves = _user(db_session, org, "stays"), _user(db_session, org, "leaves")
    app_row = _application(db_session, org)
    _own(db_session, org, app_row, stays)
    leaving = _own(db_session, org, app_row, leaves, "backup")
    db_session.commit()
    _relay()
    _consume(org)  # assignments themselves are not alerts

    db_session.delete(db_session.get(ApplicationOwner, leaving.id))
    db_session.commit()
    _relay()
    _consume(org)

    rows = _alerts(org, stays, "owner_removed")
    assert len(rows) == 1
    assert app_row.name in rows[0].message
    assert "removed" in rows[0].message
    assert rows[0].url.startswith(f"/intelligence/answers/element/{app_row.archimate_element_id}")
    assert _alerts(org, leaves) == []


# -- 3. cost change over 20 per cent ----------------------------------------------


def test_a_cost_change_over_20_per_cent_alerts_and_a_smaller_one_does_not(db_session, two_orgs):
    from app.models.cost_fact import CostFact

    org = two_orgs["A"]
    owner = _user(db_session, org, "cfo")
    app_row = _application(db_session, org)
    _own(db_session, org, app_row, owner)
    fact = CostFact(
        organization_id=org.id, element_type="application", element_id=app_row.id,
        category="total", kind="actual", amount=Decimal("1000"), currency="USD",
        period="annual", source="test",
    )
    db_session.add(fact)
    db_session.commit()

    fact.amount = Decimal("1150")  # 15 per cent
    db_session.commit()
    _relay()
    _consume(org)
    assert _alerts(org, owner, "cost_change_over_20_per_cent") == []

    fact.amount = Decimal("1500")  # 30 per cent
    db_session.commit()
    _relay()
    _consume(org)
    rows = _alerts(org, owner, "cost_change_over_20_per_cent")
    assert len(rows) == 1
    assert "1,150" in rows[0].message and "1,500" in rows[0].message
    assert app_row.name in rows[0].message
    assert rows[0].url.startswith(f"/intelligence/answers/element/{app_row.archimate_element_id}")


# -- 4. risk reaches a revenue-critical system ------------------------------------


def test_the_revenue_critical_risk_rule_ships_off_with_its_reason():
    from app.services import change_rules

    rule = _rule("risk_reaches_revenue_critical_system")
    assert rule["enabled"] is False
    assert "revenue-critical" in rule["disabled_reason"]
    event = {
        "event_id": 1,
        "event_type": rule["event_type"],
        "entity_type": "risk",
        "entity_id": 3,
        "payload": {"name": "Vendor exit", "owner_user_ids": [1],
                    "changes": [{"field": "likelihood", "before": 2, "after": 5}]},
    }
    assert change_rules.candidates_for(event) == []


# -- 5. decision condition overdue -------------------------------------------------


def _condition(db_session, org, responsible, *, due, status="pending", **lifecycle):
    from app.models.arb_decision_event import ARBCondition
    from app.models.architecture_review_board import ARBReviewItem

    review = ARBReviewItem(
        organization_id=org.id, review_number=f"REV-{uuid.uuid4().hex[:10]}",
        title="Payments platform move", review_type="solution_architecture",
        submitter_id=responsible.id,
    )
    db_session.add(review)
    db_session.flush()
    # The decision and cycle rows are not what this rule reads; skip their
    # foreign keys for this transaction only.
    db_session.execute(db.text("SET LOCAL session_replication_role = replica"))  # session-replication-role-ok: decision_event_id and review_cycle_id name rows this rule never reads (only due_date/status/responsible_id matter here); no real caller writes a condition without a real decision and cycle, so building one is not worth a full typed ARB graph in every alert-rule test
    condition = ARBCondition(
        organization_id=org.id, decision_event_id=999999, review_cycle_id=review.id,
        review_item_id=review.id, condition_number="C-1",
        description="Add a rollback plan", due_date=due, status=status,
        responsible_id=responsible.id, **lifecycle,
    )
    db_session.add(condition)
    db_session.flush()
    db_session.execute(db.text("SET LOCAL session_replication_role = origin"))  # session-replication-role-ok: restores the switch flipped above
    db_session.commit()
    return review, condition


def test_an_overdue_condition_alerts_the_person_responsible_once(db_session, two_orgs):
    from app.services.date_alerts import scan_date_alerts

    org = two_orgs["A"]
    responsible, other = _user(db_session, org, "lead"), _user(db_session, org, "other")
    review, _ = _condition(db_session, org, responsible, due=date.today() - timedelta(days=2))
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    _condition(db_session, org, other, due=date.today() - timedelta(days=2), status="fulfilled",
               fulfilled_at=now, fulfilled_by_id=other.id, verified_at=now, verified_by_id=other.id,
               legacy_lifecycle_provenance={"classification": "pre_c3_fulfilment",
                                            "legacy_fulfilment_evidence_id": None})
    _condition(db_session, org, other, due=date.today() + timedelta(days=5))

    scan_date_alerts(org.id)
    scan_date_alerts(org.id)

    rows = _alerts(org, responsible, "decision_condition_overdue")
    assert len(rows) == 1
    assert "Payments platform move" in rows[0].message
    assert "responsible" in rows[0].message
    assert rows[0].url.startswith(f"/arb/reviews/{review.id}")
    assert _alerts(org, other) == []


# -- the daily email ----------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_send(app, subject, recipients, html_body):
        calls.append({"subject": subject, "recipients": list(recipients), "html": html_body})
        return True

    monkeypatch.setattr("app._bootstrap._digest_emails._safe_send_email", fake_send)
    return calls


def _five_alerts(db_session, owned_app):
    from app.models.cost_fact import CostFact

    org_a, _ = owned_app["orgs"]
    app_row = owned_app["app"]
    fact = CostFact(
        organization_id=org_a.id, element_type="application", element_id=app_row.id,
        category="total", kind="actual", amount=Decimal("100"), currency="USD",
        period="annual", source="test",
    )
    db_session.add(fact)
    db_session.commit()
    for days, amount in ((150, "200"), (120, "300"), (90, "450"), (60, "700"), (30, "1100")):
        app_row.end_of_life_date = date.today() + timedelta(days=days)
        fact.amount = Decimal(amount)
        db_session.commit()
    _relay()
    _consume(org_a)


def test_five_changes_in_one_day_make_one_email_per_person(db_session, owned_app, sent):
    from app._bootstrap._digest_emails import send_owner_digest

    org_a, _ = owned_app["orgs"]
    owner_1, owner_2 = owned_app["owners"]
    _five_alerts(db_session, owned_app)
    assert len(_alerts(org_a, owner_1)) >= 5

    send_owner_digest(org_a.id)
    send_owner_digest(org_a.id)  # a second run the same day sends nothing

    to_owner_1 = [c for c in sent if c["recipients"] == [owner_1.email]]
    to_owner_2 = [c for c in sent if c["recipients"] == [owner_2.email]]
    assert len(to_owner_1) == 1 and len(to_owner_2) == 1
    assert to_owner_1[0]["html"].count("<li>") >= 5


def test_turning_the_alert_email_off_stops_the_email_and_keeps_the_bell(db_session, owned_app, sent):
    from app._bootstrap._digest_emails import send_owner_digest

    org_a, _ = owned_app["orgs"]
    owner_1, owner_2 = owned_app["owners"]
    owner_1.set_notification_preferences({"alert_email": False})
    db_session.commit()
    _five_alerts(db_session, owned_app)

    send_owner_digest(org_a.id)

    assert [c for c in sent if owner_1.email in c["recipients"]] == []
    assert len(_alerts(org_a, owner_1)) >= 5, "the bell keeps every alert"
    assert len([c for c in sent if owner_2.email in c["recipients"]]) == 1


def test_the_alert_email_still_goes_when_only_the_digest_is_off(db_session, owned_app, sent):
    from app._bootstrap._digest_emails import send_owner_digest

    org_a, _ = owned_app["orgs"]
    owner_1, _ = owned_app["owners"]
    owner_1.set_notification_preferences({"weekly_digest": False, "alert_email": True})
    db_session.commit()
    _five_alerts(db_session, owned_app)

    send_owner_digest(org_a.id)

    assert len([c for c in sent if owner_1.email in c["recipients"]]) == 1


# -- isolation ------------------------------------------------------------------------


def test_a_second_organisation_receives_nothing(db_session, owned_app, sent):
    from app._bootstrap._digest_emails import send_owner_digest
    from app.models.models import Notification
    from app.services.date_alerts import scan_date_alerts

    org_a, org_b = owned_app["orgs"]
    owned_app["app"].end_of_life_date = date.today() + timedelta(days=90)
    db_session.commit()
    _relay()
    _consume(org_a)
    _consume(org_b)
    scan_date_alerts(org_a.id)
    scan_date_alerts(org_b.id)
    send_owner_digest(org_b.id)

    assert _alerts(org_b) == []
    assert Notification.query.filter_by(user_id=owned_app["outsider"].id).all() == []
    assert [c for c in sent if owned_app["outsider"].email in c["recipients"]] == []


# -- the link and the switch -----------------------------------------------------------


def test_the_alert_link_opens_the_elements_cited_answer(app, db_session, owned_app, login_as):
    org_a, _ = owned_app["orgs"]
    owner_1, _ = owned_app["owners"]
    element_id = owned_app["app"].archimate_element_id

    client = app.test_client()
    login_as(client, owner_1)
    response = client.get(f"/intelligence/answers/element/{element_id}")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/intelligence/answers/" in location
    address = location.rstrip("/").rsplit("/", 1)[-1]
    assert len(address) == 32
    page = client.get(f"/intelligence/answers/{address}")
    assert page.status_code == 200

    outsider = app.test_client()
    login_as(outsider, owned_app["outsider"])
    assert outsider.get(f"/intelligence/answers/element/{element_id}").status_code == 404


def test_the_alert_email_switch_saves_and_shows(app, db_session, owned_app, login_as):
    from app.models.user import User

    from app.models.organization_signal import OrganizationSignal

    # The account page reads this table; the test schema is built by
    # create_all, which only knows the models already imported.
    OrganizationSignal.__table__.create(db.session.connection(), checkfirst=True)
    owner_1, _ = owned_app["owners"]
    client = app.test_client()
    login_as(client, owner_1)

    page = client.get("/account/manage").get_data(as_text=True)
    assert "Daily alert email" in page

    client.post("/account/manage/preferences", data={"form_type": "notifications", "weekly_digest": "on"})
    db.session.expire_all()
    assert db.session.get(User, owner_1.id).get_notification_preference("alert_email") is False

    client.post("/account/manage/preferences", data={"form_type": "notifications", "alert_email": "on"})
    db.session.expire_all()
    assert db.session.get(User, owner_1.id).get_notification_preference("alert_email") is True


def test_the_daily_check_is_scheduled_as_a_declared_tenant_job():
    from app.jobs.tenant_safe_job import TENANT_JOBS

    assert "date_alerts" in TENANT_JOBS
