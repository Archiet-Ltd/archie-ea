"""A new ordinary member sees no spend and no administration.

The account stores the persona that used to be the default for every new account
(platform_admin), holds an ordinary member role and no platform-admin flag, in an
organisation whose vendors carry costs. The journey asserts what that person is
shown: no cost total and no cost figures on the vendors page, a refusal on the
spend page, and no administration links in the menu.
"""
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def member_world(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        for name, cost in ((f"Billing {tag}", 41000), (f"Payroll {tag}", 17000)):
            db.session.add(ApplicationComponent(
                name=name, organization_id=org_id, vendor_name=f"Vendor {name}",
                total_cost_of_ownership=cost, lifecycle_status="operational"))
        template = User.query.filter_by(email=seeded["emails"]["solution_architect"]).one()
        email = f"member-{tag}@example.com"
        member = User(email=email, first_name="New", last_name="Member", organization_id=org_id,
                      enterprise_role="platform_admin", confirmed=True)
        member.role = template.role
        member.is_platform_admin = False
        member.is_org_admin = False
        member.password = PASSWORD
        db.session.add(member)
        db.session.commit()
    return {"email": email, "tag": tag}


def _text(page):
    return re.sub(r"\s+", " ", page.locator("body").inner_text())


def test_new_member_with_the_old_default_persona_sees_no_spend_and_no_admin(browser, live_server, member_world):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, member_world["email"])

        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        page.wait_for_timeout(1500)
        text = _text(page)
        assert "Annual cost across vendors" not in text
        assert not re.search(r"[£$€]\s?\d", text), "a currency figure is shown to an ordinary member"
        assert "41,000" not in text and "17,000" not in text

        admin_links = page.eval_on_selector_all(
            "a[href^='/admin']", "els => els.map(e => e.getAttribute('href'))")
        assert admin_links == [], f"administration links shown to an ordinary member: {admin_links}"

        spend = page.goto(live_server + "/procurement/licenses", timeout=PAGE_TIMEOUT)
        assert spend.status == 403, f"spend page answered {spend.status} to an ordinary member"
    finally:
        page.close()
