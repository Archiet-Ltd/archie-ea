"""An architect opens a capability's fact sheet and every gap says what to do.

The goal: read what is known about one capability and see, for each fact that is
missing, the words "Not set" (or "Not scored yet") with the sentence that says where
it is recorded, a formatted assessment date, and a strip of what the record still needs
whose chips each land on the field they name.

* a capability with an owner and a maturity assessment and nothing else shows no lone dash;
* each empty field names where it is recorded;
* the assessment date reads as a date, never a raw timestamp;
* every chip in "Missing to reach a complete record" links once to its field.
"""

import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DASHES = ("—", "-", "–", "−")

SCORE_PLACE = "Set it with Edit on the capability list."
NO_SCREEN = "No screen records this score yet."
IMPORT_PLACE = "Recorded through the capability import or API."

EXPECTED = {
    "strategic-importance": ("Not scored yet", SCORE_PLACE),
    "business-value": ("Not scored yet", SCORE_PLACE),
    "roi-score": ("Not scored yet", NO_SCREEN),
    "performance-score": ("Not scored yet", NO_SCREEN),
    "code": ("Not set", IMPORT_PLACE),
    "business-domain": ("Not set", IMPORT_PLACE),
    "it-owner": ("Not set", IMPORT_PLACE),
}

# The strip names these fields; each chip must land on the element carrying this id.
CHIPS = {
    "Description": "description",
    "Business domain": "business-domain",
    "IT owner": "it-owner",
    "Strategic importance": "strategic-importance",
    "Business value": "business-value",
}


def _make_world(app):
    from app import db
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Cap %s" % suffix, slug="cap-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()
        user = User(
            email="cap.%s@example.com" % suffix, first_name="Cap", last_name="Reader",
            organization_id=org.id, enterprise_role="platform_admin", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Administrator").one()
        user.is_platform_admin = True
        user.is_org_admin = True
        user.password = PASSWORD
        user.onboarding_completed_at = datetime.utcnow()
        db.session.add(user)
        cap = BusinessCapability(
            name="Order to cash %s" % suffix, organization_id=org.id, level=1,
            business_owner="Casey Owner", current_maturity_level=2, target_maturity_level=4,
            maturity_assessment_date=datetime(2026, 5, 1, 0, 0, 0),
        )
        db.session.add(cap)
        db.session.commit()
        return {"email": user.email, "cap": cap.id}


@pytest.fixture
def world(app, live_server):
    return _make_world(app)


def _context(browser, width, height, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    return ctx


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open(page, base, cap_id):
    response = page.goto("%s/enterprise/capabilities/%s/fact-sheet" % (base, cap_id),
                         wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == 200
    page.wait_for_selector("main h1")
    page.wait_for_timeout(800)


def _lone_dashes(page):
    return page.evaluate(
        """(dashes) => [...document.querySelectorAll('main *, [role=main] *')]
            .filter(e => e.children.length === 0 && dashes.includes((e.textContent || '').trim()))
            .map(e => e.outerHTML.slice(0, 120))""",
        list(DASHES),
    )


def _text(locator):
    return " ".join(locator.inner_text().split())


def test_empty_fields_say_where_they_are_recorded(browser, live_server, world):
    ctx = _context(browser, 1440, 900)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, world["cap"])

        assert _lone_dashes(page) == []

        for key, (lead, place) in EXPECTED.items():
            cell = page.locator("#field-%s" % key)
            assert cell.count() == 1, key
            text = _text(cell)
            assert text.startswith(lead), (key, text)
            assert place in text, (key, text)

        # The importance and value sentences lead to the screen where they are set.
        for key in ("strategic-importance", "business-value"):
            link = page.locator("#field-%s a" % key)
            assert link.count() == 1, key
            assert link.get_attribute("href") == "/enterprise/capability-map/capabilities", key

        # The owner and the maturity assessment that are recorded still show.
        body = _text(page.locator("main"))
        assert "Casey Owner" in body

        # The assessment date reads as a date, with no raw timestamp.
        assessed = _text(page.locator("#field-maturity-assessed"))
        assert "1 May 2026" in assessed, assessed
        assert "00:00:00" not in body and "2026-05-01" not in body
    finally:
        ctx.close()


def test_each_chip_in_the_strip_links_once_to_its_field(browser, live_server, world):
    ctx = _context(browser, 1440, 900)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, world["cap"])

        strip = page.locator("#missing-record")
        assert strip.is_visible()
        assert "Missing to reach a complete record" in _text(strip)

        for label, key in CHIPS.items():
            chips = strip.get_by_role("link", name=label, exact=True)
            assert chips.count() == 1, label
            assert chips.get_attribute("href") == "#field-%s" % key, label
            assert page.locator("#field-%s" % key).count() == 1, key

        # No link to the strip is repeated per row.
        assert page.locator('main a[href="#missing-record"]').count() == 0

        # Clicking a chip lands on its field, which is on screen.
        strip.get_by_role("link", name="IT owner", exact=True).click()
        page.wait_for_timeout(300)
        assert page.url.endswith("#field-it-owner")
        assert page.locator("#field-it-owner").is_visible()
    finally:
        ctx.close()


def test_fact_sheet_holds_at_phone_width_in_dark(browser, live_server, world):
    ctx = _context(browser, 390, 844, "dark")
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, world["cap"])
        assert _lone_dashes(page) == []
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, overflow
        assert "Not set" in _text(page.locator("#field-it-owner"))
    finally:
        ctx.close()
