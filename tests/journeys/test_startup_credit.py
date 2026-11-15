"""Journey: a young company claims 90 days of Startup from the billing page.

One founder claims the credit and sees when it ends, adds ten people and is refused
the eleventh, a second founder on the same company domain and a third on a public
mailbox are refused, the credit ends and the organisation reads as Community with
everyone keeping access, and buying Startup reaches the checkout step.
"""

import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import unquote

import pytest
from sqlalchemy import bindparam
from sqlalchemy.exc import IntegrityError

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def _founded_months_ago(months):
    now = datetime.utcnow()
    index = now.year * 12 + now.month - 1 - months
    return "%04d-%02d" % (index // 12, index % 12 + 1)


def _person(db, org_id, label, email, enterprise_role="platform_admin", role_name="Administrator"):
    from app.models.user import User

    uid = make_user(db, org_id, label, enterprise_role, role_name)
    user = db.session.get(User, uid)
    user.email = email
    user.mfa_enabled = True  # signed in as an enrolled administrator, not sent to enrol
    user.mfa_enrolled_at = datetime.utcnow()
    db.session.commit()
    return uid


def _founder_org(db, label, email, website=None):
    """An organisation with its founder (an administrator) and the website given at sign-up."""
    from app.models.org_role import OrgRole
    from app.models.organization import Organization

    org_id = make_org(db, label)
    org = db.session.get(Organization, org_id)
    if website:
        org.settings = {"onboarding": {"website": {"url": website}}}
    db.session.commit()
    uid = _person(db, org_id, label.replace(" ", ""), email)
    OrgRole.set_role(org_id, uid, "org_admin", granted_by_id=uid)
    db.session.commit()
    return org_id, uid


def _claim(client, website, founded=None, raised="up_to_1m"):
    return client.post(
        "/admin/billing/startup-credit",
        data={"website": website, "founded": founded or _founded_months_ago(8), "raised": raised},
        follow_redirects=True,
    )


def _text(response):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.get_data(as_text=True)))


def _add_person(client, email):
    return client.post(
        "/admin/new-user",
        data={"role": "", "first_name": "Robin", "last_name": "Newcomer", "email": email,
              "password": "Passw0rd!Passw0rd", "password2": "Passw0rd!Passw0rd"},
        follow_redirects=True,
    )


def _remove(app, org_ids):
    """Delete what this test created, by key: its organisations, their people, roles and sessions."""
    from sqlalchemy import text

    from app import db

    with app.app_context():
        ids = tuple(org_ids) or (0,)
        db.session.rollback()
        people = "(SELECT id FROM users WHERE organization_id IN :orgs)"
        for statement in (
            "DELETE FROM user_sessions WHERE user_id IN " + people,
            "DELETE FROM notifications WHERE organization_id IN :orgs OR user_id IN " + people,
            "DELETE FROM org_roles WHERE organization_id IN :orgs OR user_id IN " + people,
            "DELETE FROM users WHERE organization_id IN :orgs",
            "DELETE FROM organizations WHERE id IN :orgs",
        ):
            try:  # the sealed audit trail keeps the rows it references; those people stay, as in the other journeys
                with db.session.begin_nested():
                    db.session.execute(
                        text(statement).bindparams(bindparam("orgs", expanding=True)), {"orgs": list(ids)})
            except IntegrityError:
                continue
        db.session.commit()


def test_a_young_company_claims_90_days_and_afterwards_is_on_community_with_nothing_lost(app, client):
    from app import db
    from app.models.subscription import Subscription

    domain = "acme-%s.test" % uuid.uuid4().hex[:8]
    org_ids = []
    with app.app_context():
        a_org, a_admin = _founder_org(db, "Credit Alpha", "ana@%s" % domain.upper(), "https://%s" % domain)
        b_org, b_admin = _founder_org(db, "Credit Bravo", "bob@eu.%s" % domain, "https://eu.%s" % domain)
        c_org, c_admin = _founder_org(db, "Credit Gamma", "founder-%s@gmail.com" % uuid.uuid4().hex[:6], "https://gmail.com")
        org_ids = [a_org, b_org, c_org]
    try:
        # 1. The pricing page offers the credit and carries it through sign-in to the credit card.
        page = client.get("/pricing")
        assert page.status_code == 200
        match = re.search(r'href="([^"]+)"[^>]*data-testid="credit-startup"', page.get_data(as_text=True))
        assert match, "the pricing page has no 'Start with 90 days free' link"
        assert "offer=credit" in unquote(match.group(1).replace("&amp;", "&"))
        login(client, a_admin)
        arrive = client.get("/account/register?plan=startup&interval=year&offer=credit")
        assert arrive.status_code == 302 and arrive.headers["Location"].endswith("/admin/billing/#startup-credit")

        # 2. The billing page shows the credit card with the website filled in; the person declares and claims.
        billing = client.get("/admin/billing/")
        html = billing.get_data(as_text=True)
        assert "90 days of Startup, free" in html
        assert 'value="https://%s"' % domain in html
        assert "Start 90 days free" in html
        done = _claim(client, "https://%s" % domain)
        assert "Your 90 days of Startup have started." in _text(done)

        # 3. Startup, Free for 90 days, and the end date 90 days ahead.
        text = _text(client.get("/admin/billing/"))
        ends = (datetime.utcnow() + timedelta(days=90)).strftime("%d %b %Y").lstrip("0")
        assert "Free for 90 days" in text
        assert "Free until %s" % ends in text
        assert "Buy Startup before then to keep ten people" in text
        assert "Buying now starts billing today." in text
        assert "90 days of Startup, free" not in text, "the claim form is still offered during the credit"
        with app.app_context():
            for i in range(9):
                _person(db, a_org, "Member%d" % i, "m%d@%s" % (i, domain), "business_architect", "User")
        assert "10 of 10 people" in _text(client.get("/admin/billing/"))
        eleventh = _text(_add_person(client, "eleventh@%s" % domain))
        assert "Startup plan admits 10 people and 10 are in use" in eleventh

        # 4. A second founder on the same company domain is refused and still sees the Startup buy button.
        second = app.test_client()
        login(second, b_admin)
        refused = _claim(second, "https://eu.%s" % domain)
        refused_text = _text(refused)
        assert "This company has already used its 90 days." in refused_text
        assert 'data-testid="plan-choose-startup"' in refused.get_data(as_text=True)
        assert "Free for 90 days" not in refused_text

        # 5. A founder on a public mailbox is told to use a work email.
        third = app.test_client()
        login(third, c_admin)
        gmail = _claim(third, "https://gmail.com")
        assert "Use your work email to claim the credit." in _text(gmail)
        assert 'data-testid="plan-choose-startup"' in gmail.get_data(as_text=True)

        # 4b. The second organisation's credit state was never read or changed by the first's actions.
        with app.app_context():
            for org_id, held in ((a_org, True), (b_org, False), (c_org, False)):
                row = Subscription.query.filter_by(organization_id=org_id).first()
                assert bool(row and row.offer == "startup_credit") is held

        # 6. The end moves into the past: the same state the calendar produces.
        with app.app_context():
            row = Subscription.query.filter_by(organization_id=a_org).one()
            row.current_period_end = datetime.utcnow() - timedelta(days=1)
            db.session.commit()

        # 7. Community, the end alert, everyone still signs in, and one more person is refused.
        after = client.get("/admin/billing/")
        after_text = _text(after)
        assert re.search(r'data-testid="billing-current-plan">\s*Community', after.get_data(as_text=True))
        assert "Your 90 days of Startup ended on" in after_text
        assert "Buy Startup to add people again." in after_text
        assert "Free for 90 days" not in after_text
        with app.app_context():
            from app.models.user import User

            member_ids = [u.id for u in User.query.filter(User.organization_id == a_org).all()]
        assert len(member_ids) == 10
        for uid in member_ids:
            walker = app.test_client()
            login(walker, uid)
            assert walker.get("/dashboard/", follow_redirects=False).status_code in (200, 302)
            assert "/account/login" not in (walker.get("/dashboard/").headers.get("Location") or "")
        again = _text(_add_person(client, "twelfth@%s" % domain))
        assert "Community plan admits 3 people and 10 are in use" in again

        # 8. Buy Startup lands on the checkout step with Startup selected.
        link = re.search(r'href="([^"]*plan=startup[^"]*#checkout)"', after.get_data(as_text=True))
        assert link, "no Buy Startup link after the credit ended"
        landing = client.get(link.group(1).replace("&amp;", "&").split("#")[0])
        landing_html = landing.get_data(as_text=True)
        assert 'id="checkout"' in landing_html
        assert re.search(r'id="checkout".*?Startup', landing_html, re.S)
    finally:
        _remove(app, org_ids)
