"""Who is shown Connections and Billing, who can open them, and what Billing says.

The people: the architects who bring a source in (GitHub, Jira), the owner who
administers the organisation, and colleagues whose job has nothing to do with
either. The goal of each: find the page they need under Workspace and land on
content, while a colleague is neither shown nor let into a page that is not
theirs. Two real organisations are used so an architect of one never reads the
connectors of the other.
"""
import uuid
from datetime import datetime, timedelta

import pytest

ARCHITECTS = ("enterprise_architect", "solution_architect", "business_architect")
OTHERS = ("security_architect", "data_architect", "cto", "arb_member", "portfolio_manager")


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="Workspace %s %s" % (label, suffix), slug="workspace-%s-%s" % (label, suffix))
    db_session.add(org)
    db_session.flush()
    return org


def _person(db_session, org, persona, *, administrator=False):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator" if administrator else "Viewer").one()
    user = User(
        email="ws-%s@example.com" % uuid.uuid4().hex[:10],
        first_name="Ws",
        last_name="Person",
        organization_id=org.id,
        role=role,
        enterprise_role=persona,
        is_org_admin=administrator,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _labels(user):
    from app.utils.role_access import get_sidebar_zones

    return {
        link["label"]
        for zone in get_sidebar_zones(user)
        if zone["zone"] == "workspace"
        for link in zone["links"]
    }


@pytest.mark.parametrize("persona", ARCHITECTS)
def test_architect_sees_connections_without_billing(app, db_session, persona):
    org = _org(db_session, "arch")
    user = _person(db_session, org, persona)
    assert user.enterprise_role == persona and not user.is_platform_admin
    with app.test_request_context("/"):
        assert _labels(user) == {"Connections"}


@pytest.mark.parametrize("persona", OTHERS)
def test_other_personas_see_no_workspace_links(app, db_session, persona):
    org = _org(db_session, "other")
    user = _person(db_session, org, persona)
    with app.test_request_context("/"):
        assert _labels(user) == set()


def test_owner_with_no_persona_sees_both(app, db_session):
    org = _org(db_session, "owner")
    owner = _person(db_session, org, None, administrator=True)
    assert owner.enterprise_role is None
    with app.test_request_context("/"):
        assert _labels(owner) == {"Connections", "Billing"}


def test_viewer_with_no_persona_sees_neither(app, db_session):
    org = _org(db_session, "viewer")
    viewer = _person(db_session, org, None)
    with app.test_request_context("/"):
        assert _labels(viewer) == set()


@pytest.mark.parametrize("persona,connections", [(p, 200) for p in ARCHITECTS] + [(p, 403) for p in OTHERS])
def test_connections_page_opens_for_exactly_the_personas_shown_the_link(
        app, db_session, client, login_as, persona, connections):
    org = _org(db_session, "page")
    user = _person(db_session, org, persona)
    db_session.commit()
    with app.app_context():
        login_as(client, user)
        assert client.get("/admin/connectors").status_code == connections
        login_as(client, user)
        assert client.get("/admin/billing/").status_code == 403


def test_architect_reads_only_their_own_organisations_connectors(app, db_session, client, login_as):
    from app.models.connector_config import ConnectorConfig

    mine, theirs = _org(db_session, "mine"), _org(db_session, "theirs")
    architect = _person(db_session, mine, "enterprise_architect")
    for org, name in ((mine, "Our source repository"), (theirs, "Their private repository")):
        db_session.add(ConnectorConfig(organization_id=org.id, connector_type="github", name=name,
                                       config={"repo": "x/y"}))
    db_session.commit()
    with app.app_context():
        login_as(client, architect)
        html = client.get("/admin/connectors").get_data(as_text=True)
    assert "Our source repository" in html
    assert "Their private repository" not in html


@pytest.mark.parametrize("path", ["/admin/connectors", "/admin/connectors/github", "/admin/connectors/github/review",
                                  "/admin/connectors/jira", "/admin/connectors/m365", "/admin/connectors/servicenow"])
def test_connectors_pages_offer_an_architect_no_link_to_the_administrators_page(
        app, db_session, client, login_as, path):
    """Every link on the page lands somewhere the architect may open: the
    Admin breadcrumb, which would answer 403, is not offered to them."""
    org = _org(db_session, "crumbs")
    architect = _person(db_session, org, "solution_architect")
    db_session.commit()
    with app.app_context():
        login_as(client, architect)
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        with app.test_request_context("/"):
            from flask import url_for
            admin_home = url_for("admin.index")
    assert 'href="%s"' % admin_home not in html


# --- the Billing page -------------------------------------------------------


def _owner_page(app, db_session, client, login_as, label, **sub_fields):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    org = _org(db_session, label)
    if sub_fields:
        db_session.add(Subscription(
            organization_id=org.id,
            plan=sub_fields.pop("plan", SubscriptionPlan.free),
            status=sub_fields.pop("status", SubscriptionStatus.active),
            seats_purchased=sub_fields.pop("seats_purchased", 3),
            **sub_fields))
    owner = _person(db_session, org, None, administrator=True)
    db_session.commit()
    with app.app_context():
        login_as(client, owner)
        return client.get("/admin/billing/").get_data(as_text=True)


def test_billing_plans_show_the_prices_the_pricing_page_shows(app, db_session, client, login_as):
    import re

    html = _owner_page(app, db_session, client, login_as, "prices")
    pricing = client.get("/pricing").get_data(as_text=True)
    for key, figures in {"free": ["Free"], "startup": ["USD 490 a year", "USD 49 a month"],
                         "team": ["USD 290 per editor a year", "USD 29 per editor a month"],
                         "enterprise": ["From USD 24,000 a year"]}.items():
        card = re.search(r'data-testid="plan-price-%s">(.*?)</div>' % key, html, re.S).group(1)
        text = " ".join(re.sub(r"<[^>]+>", " ", card).split())
        assert text == " ".join(figures), (key, text)
    # The same figures appear on /pricing (which uses $, not USD).
    for figure in ("USD 490", "USD 49", "USD 290", "USD 29", "USD 24,000"):
        assert figure in html, (figure, 'missing from billing page')
    for figure in ("$490", "$49", "$290", "$29", "$24,000"):
        assert figure in pricing, (figure, 'missing from pricing page')


def test_billing_without_a_subscription_says_nothing_to_renew(app, db_session, client, login_as):
    html = _owner_page(app, db_session, client, login_as, "norenewal")
    assert 'data-testid="billing-renewal">Nothing to renew<' in html.replace("\n", "").replace("  ", "")
    assert '<span class="text-muted-foreground">—</span>' not in html


def test_billing_with_a_live_subscription_shows_date_and_amount(app, db_session, client, login_as, monkeypatch):
    from app.models.subscription import SubscriptionPlan
    from app.services.billing_service import BillingService

    monkeypatch.setattr(BillingService, "has_live_subscription", staticmethod(lambda sub: True))
    end = datetime.utcnow() + timedelta(days=40)
    html = _owner_page(app, db_session, client, login_as, "renews", plan=SubscriptionPlan.startup,
                       billing_interval="year", current_period_end=end)
    flat = " ".join(html.split())
    assert end.strftime("%d %b %Y") in flat
    assert "USD 490 a year" in flat


def test_billing_page_keeps_the_wordmark_in_the_sidebar(app, db_session, client, login_as):
    html = _owner_page(app, db_session, client, login_as, "wordmark")
    assert 'sidebar-app-name">Entelim<' in html


def test_billing_page_speaks_plainly_when_payment_is_not_open(app, db_session, client, login_as, monkeypatch):
    for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "MONELYTICS_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    html = _owner_page(app, db_session, client, login_as, "plain")
    assert "Online payment is not open yet" in html
    assert "on this installation" not in html
    assert 'href="/contact"' in html
