"""Launch gate 10: funnel counts recorded once per organisation, read by platform admins only."""

import uuid
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models.usage_event import UsageEvent


def _user(db_session, org, *, platform=False, admin=False):
    from app.models.user import Role, User

    name = "Administrator" if (platform or admin) else "Architect"
    role = Role.query.filter_by(name=name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=name).first()
    user = User(
        email=f"funnel-{uuid.uuid4().hex[:8]}@example.com",
        first_name="T",
        last_name="U",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _count(org_id, event_type):
    return UsageEvent.query.filter_by(organization_id=org_id, event_type=event_type).count()


EVENTS = [
    UsageEvent.EVENT_ORGANISATION_SIGNED_UP,
    UsageEvent.EVENT_FIRST_ANSWER_SHOWN,
]


@pytest.mark.parametrize("event_type", EVENTS)
def test_record_once_first_call_records_second_does_not(db_session, make_org, event_type):
    from app.services.funnel_events import record_once

    org = make_org("once")
    assert record_once(org.id, event_type) is True
    assert record_once(org.id, event_type) is False
    assert _count(org.id, event_type) == 1


def test_other_event_types_remain_repeatable(db_session, make_org):
    org = make_org("repeat")
    for _ in range(2):
        db_session.add(UsageEvent(organization_id=org.id, event_type=UsageEvent.EVENT_USER_LOGIN))
    db_session.flush()
    assert _count(org.id, UsageEvent.EVENT_USER_LOGIN) == 2


def test_record_once_never_raises(db_session, make_org):
    from app.services.funnel_events import record_once

    # An organisation that does not exist violates the foreign key; the call reports False.
    assert record_once(987654321, UsageEvent.EVENT_ORGANISATION_SIGNED_UP) is False
    org = make_org("after")
    assert record_once(org.id, UsageEvent.EVENT_ORGANISATION_SIGNED_UP) is True


def test_sign_up_records_one_event(app, db_session):
    from app.models import Organization, User
    from app.modules.account.services.account_service import AccountService

    email = f"signup-{uuid.uuid4().hex[:8]}@example.com"
    with app.test_request_context("/"):
        user = AccountService.register_user("Fun", "Nel", email, "Passw0rd!x1", confirmed=True)
    assert _count(user.organization_id, UsageEvent.EVENT_ORGANISATION_SIGNED_UP) == 1


def _patch_answer(monkeypatch, citations):
    from app.modules.intelligence.services import answer_envelope as envelope

    monkeypatch.setattr(
        envelope, "ask_and_store",
        lambda kind, params, user_id=None: SimpleNamespace(citations=citations),
    )
    monkeypatch.setattr(envelope, "present", lambda record, **kw: {"ok": True})


def test_answer_with_source_records_once(client, db_session, make_org, login_as, monkeypatch):
    org = make_org("ans")
    user = _user(db_session, org)
    _patch_answer(monkeypatch, [{"source": "x"}])
    for _ in range(2):
        login_as(client, user)
        resp = client.post("/api/v1/intelligence/answers", json={"question": "q"})
        assert resp.status_code == 201
    assert _count(org.id, UsageEvent.EVENT_FIRST_ANSWER_SHOWN) == 1


def test_answer_without_source_records_none(client, db_session, make_org, login_as, monkeypatch):
    org = make_org("nosrc")
    user = _user(db_session, org)
    _patch_answer(monkeypatch, [])
    login_as(client, user)
    resp = client.post("/api/v1/intelligence/answers", json={"question": "q"})
    assert resp.status_code == 201
    assert _count(org.id, UsageEvent.EVENT_FIRST_ANSWER_SHOWN) == 0


def _inquire(client, offer, slug, email):
    data = {"offer": offer, "family": "site", "slug": slug, "email": email, "consent": "1"}
    resp = client.post("/offers/inquire", data=data)
    assert resp.status_code == 200


def _hc_email():
    return f"buyer-{uuid.uuid4().hex[:8]}@example.com"


def test_anonymous_health_check_request_is_counted(client, db_session):
    from app.services.funnel_events import funnel_counts

    before = funnel_counts()["health_check"]
    _inquire(client, "architecture_health_check", "architecture-health-check", _hc_email())
    assert funnel_counts()["health_check"] == before + 1


def test_signed_in_health_check_request_adds_one(client, db_session, make_org, login_as):
    from app.services.funnel_events import funnel_counts

    org = make_org("hc")
    user = _user(db_session, org)
    before = funnel_counts()["health_check"]
    login_as(client, user)
    _inquire(client, "architecture_health_check", "architecture-health-check", _hc_email())
    assert funnel_counts()["health_check"] == before + 1


def test_other_offer_is_not_counted(client, db_session):
    from app.services.funnel_events import funnel_counts

    before = funnel_counts()["health_check"]
    _inquire(client, "team_annual_onboarding", "team-annual-onboarding", _hc_email())
    assert funnel_counts()["health_check"] == before


def test_two_organisations_counted_separately_and_summed(db_session, make_org):
    from app.services.funnel_events import funnel_counts, record_once

    before = funnel_counts()
    a, b = make_org("a"), make_org("b")
    record_once(a.id, UsageEvent.EVENT_ORGANISATION_SIGNED_UP)
    record_once(b.id, UsageEvent.EVENT_ORGANISATION_SIGNED_UP)
    record_once(a.id, UsageEvent.EVENT_FIRST_ANSWER_SHOWN)
    after = funnel_counts()
    assert after["signed_up"] - before["signed_up"] == 2
    assert after["first_answer"] - before["first_answer"] == 1
    assert after["health_check"] == before["health_check"]
    assert _count(a.id, UsageEvent.EVENT_ORGANISATION_SIGNED_UP) == 1
    assert _count(b.id, UsageEvent.EVENT_ORGANISATION_SIGNED_UP) == 1


def test_platform_admin_sees_section(client, db_session, make_org, login_as):
    from app.services.funnel_events import record_once

    org = make_org("pa")
    admin = _user(db_session, org, platform=True)
    record_once(org.id, UsageEvent.EVENT_ORGANISATION_SIGNED_UP)
    login_as(client, admin)
    resp = client.get("/usage-analytics/dashboard")
    assert resp.status_code == 200
    assert b'data-testid="funnel-counts"' in resp.data
    login_as(client, admin)
    api = client.get("/usage-analytics/api/funnel-counts")
    assert api.status_code == 200
    assert api.get_json()["signed_up"] >= 1


def test_org_admin_is_refused(client, db_session, make_org, login_as):
    org = make_org("oa")
    admin = _user(db_session, org, admin=True)
    login_as(client, admin)
    api = client.get("/usage-analytics/api/funnel-counts")
    assert api.status_code == 403
    login_as(client, admin)
    page = client.get("/usage-analytics/dashboard")
    assert b'data-testid="funnel-counts"' not in page.data
