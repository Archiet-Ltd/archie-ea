"""The startup credit: the lapse rule, the six checks, and the one-per-company rule.

The credit is a state of the organisation's one subscriptions row. These tests
cover the rule that ends it (read at use), the checks that grant it, and the
cross-organisation question the sixth check asks.
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.services import billing_offers
from app.services.billing_offers import (
    REFUSAL_DOMAIN_TAKEN,
    REFUSAL_FOUNDED,
    REFUSAL_PAID,
    REFUSAL_RAISED,
    REFUSAL_USED,
    REFUSAL_WEBSITE,
    REFUSAL_WORK_EMAIL,
    grant_startup_credit,
    registrable_domain,
    startup_credit_state,
)
from app.services.billing_plans import effective_plan, offer_lapsed

NOW = datetime(2026, 11, 14, 12, 0, 0)


def _org(make_org, label="credit"):
    return make_org(label)


def _founder(address):
    return SimpleNamespace(id=1, email=address)


def _declaration(domain="acme-robotics.test", **over):
    base = {"website": f"https://{domain}", "founded": "2025-03", "raised": "up_to_1m"}
    base.update(over)
    return base


def _row(**fields):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    base = dict(
        plan=SubscriptionPlan.startup, status=SubscriptionStatus.trialing, seats_purchased=10,
        offer="startup_credit", current_period_end=NOW - timedelta(seconds=1),
    )
    base.update(fields)
    return Subscription(**base)


# --- the lapse rule ---------------------------------------------------------


def test_credit_is_lapsed_only_after_its_end(app):
    assert offer_lapsed(_row(), NOW)
    assert not offer_lapsed(_row(current_period_end=NOW + timedelta(seconds=1)), NOW)
    assert not offer_lapsed(_row(current_period_end=NOW), NOW)


def test_a_row_without_the_offer_never_lapses(app):
    assert not offer_lapsed(_row(offer=None), NOW)
    assert not offer_lapsed(None, NOW)


def test_a_provider_subscription_takes_the_row_out_of_the_rule(app):
    assert not offer_lapsed(_row(stripe_subscription_id="sub_1"), NOW)
    assert not offer_lapsed(_row(monelytics_subscription_id="mon_1"), NOW)


def test_a_contract_plan_over_a_former_credit_is_not_lapsed(app):
    from app.models.subscription import SubscriptionStatus

    assert not offer_lapsed(_row(status=SubscriptionStatus.active), NOW)


def test_effective_plan_reads_the_lapse_at_use(app):
    running = _row(current_period_end=datetime.utcnow() + timedelta(days=5))
    ended = _row(current_period_end=datetime.utcnow() - timedelta(days=1))
    assert effective_plan(running).key == "startup"
    assert effective_plan(ended).key == "free"


def test_capacity_counts_an_ended_credit_as_community(db_session, make_org):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
    from app.models.user import User
    from app.services.billing_plans import PlanLimitReached

    def fill(org, people):
        for _ in range(people):
            person = User(email=f"cap-{uuid.uuid4().hex[:10]}@example.com", first_name="P",
                          last_name="P", organization_id=org.id, confirmed=True)
            try:
                with db_session.begin_nested():  # a refused person leaves nothing pending
                    db_session.add(person)
                    db_session.flush()
            except PlanLimitReached:
                db_session.expunge(person) if person in db_session else None
                raise

    def credit(org, end):
        db_session.add(Subscription(
            organization_id=org.id, plan=SubscriptionPlan.startup, status=SubscriptionStatus.trialing,
            seats_purchased=10, offer="startup_credit", current_period_end=end,
        ))
        db_session.flush()

    running, ended = make_org("running"), make_org("ended")
    credit(running, datetime.utcnow() + timedelta(days=30))
    credit(ended, datetime.utcnow() - timedelta(days=1))
    fill(running, 10)  # ten people fit on the running credit
    with pytest.raises(PlanLimitReached):
        fill(running, 1)  # the eleventh does not
    fill(ended, 3)  # Community's three
    with pytest.raises(PlanLimitReached):
        fill(ended, 1)


# --- the company domain -----------------------------------------------------


def test_registrable_domain_is_one_spelling_for_one_company():
    assert registrable_domain("ana@ACME-Robotics.test") == "acme-robotics.test"
    assert registrable_domain("bob@eu.acme-robotics.test") == "acme-robotics.test"
    assert registrable_domain("x@sales.acme.co.uk") == "acme.co.uk"
    assert registrable_domain("https://www.Acme-Robotics.test/about") == "acme-robotics.test"
    assert registrable_domain("acme-robotics.test") == "acme-robotics.test"
    assert registrable_domain("x@bücher.example") == registrable_domain("x@xn--bcher-kva.example")
    assert registrable_domain("") is None and registrable_domain("nobody") is None


# --- the six checks ---------------------------------------------------------


def test_a_work_email_with_a_matching_site_is_granted_ten_people_for_90_days(db_session, make_org, tenant_ctx):
    org = _org(make_org)
    with tenant_ctx(org.id):
        result = grant_startup_credit(org, _founder("ana@acme-robotics.test"), _declaration(), NOW)
        sub = db_session.execute(text("select 1")) and org.subscription
    assert result.granted, result.reason
    assert sub.offer == "startup_credit" and sub.offer_domain == "acme-robotics.test"
    assert sub.seats_purchased == 10 and sub.status.value == "trialing" and sub.plan.value == "startup"
    assert sub.current_period_end == NOW + timedelta(days=90)
    assert sub.offer_granted_at == NOW
    assert [c["check"] for c in sub.offer_terms["checks"]] == [
        "no_paid_plan", "work_email", "founded", "raised", "website", "domain_free",
    ]
    assert startup_credit_state(org, NOW)["state"] == "active"


@pytest.mark.parametrize("address", ["x@gmail.com", "x@mail.gmail.com", "x@mailinator.com"])
def test_a_public_or_disposable_mailbox_is_refused(db_session, make_org, tenant_ctx, address):
    org = _org(make_org)
    with tenant_ctx(org.id):
        result = grant_startup_credit(org, _founder(address), _declaration("gmail.com"), NOW)
    assert (result.granted, result.reason) == (False, REFUSAL_WORK_EMAIL)
    assert org.subscription is None or org.subscription.offer is None


@pytest.mark.parametrize("founded", ["2023-10", "", "soon", "2027-01"])
def test_a_company_older_than_36_months_or_undated_is_refused(db_session, make_org, tenant_ctx, founded):
    org = _org(make_org)
    with tenant_ctx(org.id):
        result = grant_startup_credit(org, _founder("ana@acme-robotics.test"), _declaration(founded=founded), NOW)
    assert (result.granted, result.reason) == (False, REFUSAL_FOUNDED)


def test_exactly_36_months_is_allowed(db_session, make_org, tenant_ctx):
    org = _org(make_org)
    with tenant_ctx(org.id):
        result = grant_startup_credit(org, _founder("ana@acme-robotics.test"), _declaration(founded="2023-11"), NOW)
    assert result.granted


def test_more_than_one_million_raised_is_refused(db_session, make_org, tenant_ctx):
    org = _org(make_org)
    with tenant_ctx(org.id):
        result = grant_startup_credit(org, _founder("ana@acme-robotics.test"), _declaration(raised="more_than_1m"), NOW)
    assert (result.granted, result.reason) == (False, REFUSAL_RAISED)


def test_a_website_of_another_company_is_refused(db_session, make_org, tenant_ctx):
    org = _org(make_org)
    with tenant_ctx(org.id):
        result = grant_startup_credit(
            org, _founder("x@other.test"), _declaration("acme-robotics.test"), NOW)
    assert (result.granted, result.reason) == (False, REFUSAL_WEBSITE)


def test_a_subdomain_site_of_the_same_company_is_accepted(db_session, make_org, tenant_ctx):
    org = _org(make_org)
    with tenant_ctx(org.id):
        result = grant_startup_credit(
            org, _founder("ana@acme-robotics.test"), _declaration(website="https://www.eu.acme-robotics.test"), NOW)
    assert result.granted


def test_an_organisation_with_a_live_subscription_is_refused(db_session, make_org, tenant_ctx):
    from app.services.billing_plans import ensure_subscription

    org = _org(make_org)
    with tenant_ctx(org.id):
        sub = ensure_subscription(org)
        sub.stripe_subscription_id = "sub_paid"
        db_session.flush()
        result = grant_startup_credit(org, _founder("ana@acme-robotics.test"), _declaration(), NOW)
    assert (result.granted, result.reason) == (False, REFUSAL_PAID)


def test_an_organisation_that_used_the_credit_cannot_claim_again(db_session, make_org, tenant_ctx):
    org = _org(make_org)
    with tenant_ctx(org.id):
        assert grant_startup_credit(org, _founder("ana@acme-robotics.test"), _declaration(), NOW).granted
        again = grant_startup_credit(org, _founder("ana@acme-robotics.test"), _declaration(), NOW + timedelta(days=200))
    assert (again.granted, again.reason) == (False, REFUSAL_USED)


def test_a_second_organisation_on_the_same_domain_is_refused(db_session, make_org, tenant_ctx):
    first, second = _org(make_org, "first"), _org(make_org, "second")
    with tenant_ctx(first.id):
        assert grant_startup_credit(first, _founder("ana@acme-robotics.test"), _declaration(), NOW).granted
    with tenant_ctx(second.id):
        result = grant_startup_credit(second, _founder("bob@eu.acme-robotics.test"), _declaration(), NOW)
        state = startup_credit_state(second, NOW)
    assert (result.granted, result.reason) == (False, REFUSAL_DOMAIN_TAKEN)
    assert state["state"] == "available"


def test_the_domain_check_is_asked_only_after_the_email_and_website_checks(db_session, make_org, tenant_ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(billing_offers, "startup_credit_domain_taken", lambda *a: calls.append(a) or False)
    org = _org(make_org)
    with tenant_ctx(org.id):
        result = grant_startup_credit(org, _founder("x@other.test"), _declaration("acme-robotics.test"), NOW)
        gmail = grant_startup_credit(org, _founder("x@gmail.com"), _declaration("gmail.com"), NOW)
    assert result.reason == REFUSAL_WEBSITE and gmail.reason == REFUSAL_WORK_EMAIL
    assert calls == []


def test_a_lost_race_on_the_domain_is_refused_and_one_row_holds_the_offer(db_session, make_org, tenant_ctx, monkeypatch):
    from app.models.subscription import Subscription

    first, second = _org(make_org, "first"), _org(make_org, "second")
    with tenant_ctx(first.id):
        assert grant_startup_credit(first, _founder("ana@acme-robotics.test"), _declaration(), NOW).granted
    monkeypatch.setattr(billing_offers, "startup_credit_domain_taken", lambda *a: False)
    with tenant_ctx(second.id):
        result = grant_startup_credit(second, _founder("bob@acme-robotics.test"), _declaration(), NOW)
    assert (result.granted, result.reason) == (False, REFUSAL_DOMAIN_TAKEN)
    held = db_session.execute(
        Subscription.__table__.select().where(Subscription.__table__.c.offer_domain == "acme-robotics.test")
    ).fetchall()
    assert len(held) == 1 and held[0].organization_id == first.id


def test_the_state_of_an_ended_credit_says_so(db_session, make_org, tenant_ctx):
    org = _org(make_org)
    with tenant_ctx(org.id):
        grant_startup_credit(org, _founder("ana@acme-robotics.test"), _declaration(), NOW)
        state = startup_credit_state(org, NOW + timedelta(days=91))
    assert state["state"] == "ended" and state["ends_on"] == NOW + timedelta(days=90)


# --- the cross-organisation question, under a fenced table ------------------

_PROBE_ROLE = "startup_credit_probe"


@pytest.fixture
def fenced_probe(db_session):
    """The subscriptions table fenced as the other tenant tables are, and the
    session switched to a role that is not a superuser, does not bypass row-level
    security and does not own the table. All of it is undone with the test's
    transaction."""
    db_session.execute(text(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{_PROBE_ROLE}') "
        f"THEN CREATE ROLE {_PROBE_ROLE} NOSUPERUSER NOBYPASSRLS NOLOGIN; END IF; END $$"
    ))
    db_session.execute(text(f"GRANT SELECT ON subscriptions TO {_PROBE_ROLE}"))
    db_session.execute(text("ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY"))
    db_session.execute(text(
        "CREATE POLICY probe_select ON subscriptions FOR SELECT USING ("
        "organization_id = NULLIF(current_setting('archie.organization_id', true), '')::integer "
        "OR COALESCE(current_setting('archie.platform_scope', true), '') = 'on')"
    ))
    return db_session


def _hold_credit(db_session, org, domain):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    db_session.add(Subscription(
        organization_id=org.id, plan=SubscriptionPlan.startup, status=SubscriptionStatus.trialing,
        seats_purchased=10, offer="startup_credit", offer_domain=domain,
        current_period_end=NOW + timedelta(days=90),
    ))
    db_session.flush()


def test_the_domain_question_under_row_level_security(fenced_probe, make_org, tenant_ctx):
    session = fenced_probe
    a, b = make_org("holder"), make_org("asker")
    _hold_credit(session, a, "acme-robotics.test")
    session.execute(text(f"SET LOCAL ROLE {_PROBE_ROLE}"))
    with tenant_ctx(b.id):
        session.execute(text("SELECT set_config('archie.organization_id', :o, true)"), {"o": str(b.id)})
        role = session.execute(text(
            "SELECT r.rolsuper, r.rolbypassrls, (t.tableowner = current_user) "
            "FROM pg_roles r, pg_tables t WHERE r.rolname = current_user AND t.tablename = 'subscriptions'"
        )).one()
        assert role == (False, False, False)
        # The fence is real for this role: without the scope B sees none of A's row.
        assert session.execute(text("SELECT count(*) FROM subscriptions WHERE offer_domain = 'acme-robotics.test'")).scalar() == 0
        assert billing_offers.startup_credit_domain_taken("acme-robotics.test", b.id) is True
        # The scope is closed the moment the function returns.
        assert session.execute(text("SELECT current_setting('archie.platform_scope', true)")).scalar() != "on"
        assert session.execute(text("SELECT count(*) FROM subscriptions WHERE offer_domain = 'acme-robotics.test'")).scalar() == 0
        assert billing_offers.startup_credit_domain_taken("acme-robotics.test", a.id) is False
        assert billing_offers.startup_credit_domain_taken("other-company.test", b.id) is False
    session.execute(text("RESET ROLE"))


# --- who counts as paying ---------------------------------------------------


def test_an_organisation_on_the_credit_is_not_counted_as_paying(db_session, make_org, tenant_ctx):
    from app.modules.account.services.onboarding_service import on_paying_plan
    from app.services.billing_plans import ensure_subscription, set_contract_plan

    credit, contract, provider, community = (make_org(n) for n in ("credit", "contract", "provider", "community"))
    with tenant_ctx(credit.id):
        assert grant_startup_credit(credit, _founder("ana@acme-robotics.test"), _declaration(), datetime.utcnow()).granted
    assert set_contract_plan(contract, "team", 5)
    ensure_subscription(provider).stripe_subscription_id = "sub_live"
    db_session.flush()
    assert on_paying_plan(credit) is False
    assert on_paying_plan(contract) is True
    assert on_paying_plan(provider) is True
    assert on_paying_plan(community) is False
