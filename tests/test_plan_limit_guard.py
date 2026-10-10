"""The plan's people limit is enforced once, when a person is saved.

Every path that adds someone to an organisation — the admin forms, an
invitation, single sign-on provisioning, moving a person between
organisations, making a read-only member an editor — passes the same
flush-time check (billing_plans.check_capacity), which locks the
organisation's row before counting so two additions of the last place cannot
both succeed.
"""

import threading
import uuid

import pytest

from app.services.billing_plans import PlanLimitReached


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Limit {label} {suffix}", slug=f"limit-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _plan(db_session, org, plan, seats):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    db_session.add(Subscription(
        organization_id=org.id, plan=SubscriptionPlan[plan],
        status=SubscriptionStatus.active, seats_purchased=seats,
    ))
    db_session.flush()


def _person(org_id=None, **fields):
    from app.models.user import User

    return User(
        email=f"limit-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Pat",
        last_name="Person",
        organization_id=org_id,
        confirmed=True,
        **fields,
    )


def _fill(db_session, org, n):
    people = []
    for _ in range(n):
        person = _person(org.id)
        db_session.add(person)
        db_session.flush()
        people.append(person)
    return people


def _count(db_session, org):
    from app.models.user import User

    return db_session.query(User).filter(User.organization_id == org.id).count()


# --------------------------------------------------------------------------- #
# Each path that adds a person                                                 #
# --------------------------------------------------------------------------- #


def test_a_fourth_person_on_community_is_refused_whatever_creates_them(db_session):
    org = _org(db_session, "direct")
    _fill(db_session, org, 3)

    db_session.add(_person(org.id))
    with pytest.raises(PlanLimitReached) as refused:
        db_session.flush()
    db_session.rollback()
    assert "Community plan admits 3 people and it already has 3" in str(refused.value)


def test_sso_just_in_time_provisioning_at_the_limit_is_refused(db_session):
    from app.models.user import User
    from app.services.sso_service import SSOService

    org = _org(db_session, "sso")
    _fill(db_session, org, 3)
    email = f"sso-{uuid.uuid4().hex[:8]}@example.com"

    with pytest.raises(PlanLimitReached):
        SSOService().provision_user(org, {"email": email, "sub": "idp-1"})
    db_session.rollback()
    assert User.query.filter_by(email=email).first() is None


def test_sso_callback_at_the_limit_tells_the_person_why(app, db_session, client, monkeypatch):
    from app.models.sso_config import SSOConfig
    from app.models.user import User
    from app.modules.auth import sso_routes

    org = _org(db_session, "ssoroute")
    db_session.add(SSOConfig(organization_id=org.id, protocol="oidc", enabled=True,
                             client_id="c", idp_metadata_url="https://idp.example/.well-known"))
    _fill(db_session, org, 3)
    db_session.commit()
    email = f"sso-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(sso_routes._svc, "handle_oidc_callback",
                        lambda *a, **k: {"email": email, "sub": "idp-2"})

    with client.session_transaction() as sess:
        sess["sso_state"] = "state-limit"
        sess["sso_nonce"] = "nonce-limit"
        sess["sso_org_id"] = org.id
    with app.app_context():
        resp = client.get("/auth/sso/callback/oidc?code=abc&state=state-limit")

    assert resp.status_code == 302 and "/account/login" in resp.headers["Location"]
    with client.session_transaction() as sess:
        messages = [m for _, m in sess.get("_flashes", [])]
    assert any("Your account could not be created." in m and "Community plan admits 3" in m
               for m in messages), messages
    assert User.query.filter_by(email=email).first() is None


@pytest.mark.parametrize("service", [
    "app.modules.admin.services.admin_user_service:AdminUserService",
    "app.modules.admin.v2.services.admin_user_service_v2:AdminUserService",
])
def test_admin_create_and_invite_services_are_refused_at_the_limit(db_session, service):
    import importlib

    from app.models.user import Role

    module, cls = service.split(":")
    svc = getattr(importlib.import_module(module), cls)
    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    org = _org(db_session, "svc")
    _fill(db_session, org, 3)
    db_session.commit()

    with pytest.raises(PlanLimitReached):
        svc.create_user("A", "B", f"a-{uuid.uuid4().hex[:8]}@example.com",
                        uuid.uuid4().hex, role, organization_id=org.id)
    db_session.rollback()
    with pytest.raises(PlanLimitReached):
        svc.invite_user("A", "B", f"i-{uuid.uuid4().hex[:8]}@example.com",
                        role, organization_id=org.id)
    db_session.rollback()
    assert _count(db_session, org) == 3


def test_moving_a_person_into_a_full_organisation_is_refused(db_session):
    full = _org(db_session, "full")
    elsewhere = _org(db_session, "elsewhere")
    _fill(db_session, full, 3)
    (mover,) = _fill(db_session, elsewhere, 1)

    mover.organization_id = full.id
    with pytest.raises(PlanLimitReached):
        db_session.flush()
    db_session.rollback()


def test_making_a_reader_an_editor_past_the_team_seats_is_refused(db_session):
    from app.models.org_role import OrgRole

    org = _org(db_session, "team")
    _plan(db_session, org, "team", 1)
    # The Team plan counts editors: a new person takes an editor seat until
    # made read-only, so the reader is added and made read-only first.
    (reader,) = _fill(db_session, org, 1)
    role = OrgRole(organization_id=org.id, user_id=reader.id, role="viewer")
    db_session.add(role)
    db_session.flush()
    _fill(db_session, org, 1)  # the editor, in the one seat

    role.role = "architect"
    with pytest.raises(PlanLimitReached) as refused:
        db_session.flush()
    db_session.rollback()
    assert "admits 1 editors" in str(refused.value)


def test_a_full_organisation_does_not_affect_another(db_session):
    full = _org(db_session, "full2")
    roomy = _org(db_session, "roomy")
    _fill(db_session, full, 3)

    db_session.add(_person(roomy.id))
    db_session.flush()  # not refused
    assert _count(db_session, roomy) == 1


def test_a_paid_plan_and_an_unlimited_plan_admit_more(db_session):
    startup = _org(db_session, "startup")
    _plan(db_session, startup, "startup", 1)
    _fill(db_session, startup, 10)
    db_session.add(_person(startup.id))
    with pytest.raises(PlanLimitReached):
        db_session.flush()
    db_session.rollback()

    enterprise = _org(db_session, "ent")
    _plan(db_session, enterprise, "enterprise", 0)
    _fill(db_session, enterprise, 12)
    assert _count(db_session, enterprise) == 12


def test_a_cancelled_subscription_is_back_to_community(db_session):
    from app.models.subscription import Subscription, SubscriptionStatus

    org = _org(db_session, "cancelled")
    _plan(db_session, org, "startup", 1)
    _fill(db_session, org, 3)
    Subscription.query.filter_by(organization_id=org.id).one().status = SubscriptionStatus.cancelled
    db_session.add(_person(org.id))
    with pytest.raises(PlanLimitReached):
        db_session.flush()
    db_session.rollback()


# --------------------------------------------------------------------------- #
# Atomicity                                                                    #
# --------------------------------------------------------------------------- #


def test_two_concurrent_additions_of_the_last_place_admit_exactly_one(app):
    """Two sessions each add a person to an organisation one place short.

    The first holds the organisation's row lock until it commits; the second
    waits for it, counts afterwards, and is refused. Real commits, so this
    test cleans up after itself.
    """
    from app import db
    from app.models.organization import Organization
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name=f"Race {suffix}", slug=f"race-{suffix}")
        db.session.add(org)
        db.session.flush()
        org_id = org.id
        for _ in range(2):
            db.session.add(_person(org_id))
        db.session.commit()

    first_holds_lock = threading.Event()
    outcomes = {}

    def first():
        with app.app_context():
            try:
                db.session.add(_person(org_id))
                db.session.flush()  # takes the lock and counts 2
                first_holds_lock.set()
                threading.Event().wait(1.0)  # the second is now blocked on the lock
                db.session.commit()
                outcomes["first"] = "added"
            except PlanLimitReached:
                db.session.rollback()
                outcomes["first"] = "refused"
            finally:
                first_holds_lock.set()
                db.session.remove()

    def second():
        first_holds_lock.wait(10)
        with app.app_context():
            try:
                db.session.add(_person(org_id))
                db.session.commit()
                outcomes["second"] = "added"
            except PlanLimitReached:
                db.session.rollback()
                outcomes["second"] = "refused"
            finally:
                db.session.remove()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        with app.app_context():
            members = User.query.filter_by(organization_id=org_id).count()
        assert outcomes == {"first": "added", "second": "refused"}
        assert members == 3
    finally:
        with app.app_context():
            User.query.filter_by(organization_id=org_id).delete()
            Organization.query.filter_by(id=org_id).delete()
            db.session.commit()


# --------------------------------------------------------------------------- #
# The shared fallback organisation                                            #
# --------------------------------------------------------------------------- #


def test_the_shared_fallback_organisation_has_no_plan(db_session):
    """Sign-ups with no organisation land in "default", which is not a customer."""
    for _ in range(4):
        db_session.add(_person(None))
        db_session.flush()


def test_removing_a_reader_with_their_role_is_not_refused(db_session):
    from app.models.org_role import OrgRole

    org = _org(db_session, "leave")
    _plan(db_session, org, "team", 1)
    (reader,) = _fill(db_session, org, 1)
    role = OrgRole(organization_id=org.id, user_id=reader.id, role="viewer")
    db_session.add(role)
    db_session.flush()
    _fill(db_session, org, 1)  # the editor, in the one seat

    db_session.delete(role)
    db_session.delete(reader)
    db_session.flush()  # the reader leaves; no seat is taken
    assert _count(db_session, org) == 1
