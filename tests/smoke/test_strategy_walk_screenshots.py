"""Walk every strategy framework at 1440 and 390, light and dark, and save a
screenshot of each under the seat's walk directory.

This is the recorded walk a human reviews: it proves each framework keeps its
recognisable shape at phone width (nothing stacks into one list) and renders
in both themes. It is opt-in — run with STRATEGY_WALK=1 — because it writes
files outside the repository and is a capture, not an assertion suite.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke]

WALK_DIR = "/home/ubuntu/verify/seats/strat01b/walk"

FRAMEWORKS = [
    "swot", "pestle", "vrio", "mckinsey_7s",
    "porters_five_forces", "bcg_matrix", "ansoff_matrix", "blue_ocean_strategy",
]


@pytest.fixture(scope="module")
def walk_enabled():
    if os.environ.get("STRATEGY_WALK") != "1":
        pytest.skip("set STRATEGY_WALK=1 to capture the walk screenshots")


def _seed():
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {}
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="Walk Org %s" % suffix, slug="walk-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id
        email = "walk.arch.%s@example.com" % suffix
        user = User(
            email=email, first_name="Walk", last_name="Arch",
            organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        )
        user.role = architect_role
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        out["email"] = email

        from flask import g
        g.current_org_id = org.id
        from app.services import strategy_draft_service as engine

        # A few seeded elements across the families so boxes are not all empty.
        engine.add_element("bcg_matrix", "stars", "Flagship %s" % suffix, org.id, element_type="Product")
        engine.add_element("ansoff_matrix", "market_penetration", "Core market %s" % suffix,
                           org.id, element_type="Product")
        engine.add_element("vrio", "valuable", "Brand %s" % suffix, org.id, element_type="Assessment")
        engine.add_element("porters_five_forces", "competitive_rivalry",
                           "Crowded market %s" % suffix, org.id, element_type="Assessment")
        engine.add_element("mckinsey_7s", "shared_values", "Customer first %s" % suffix,
                           org.id, element_type="Driver")
        engine.add_element("blue_ocean_strategy", "competing_factors", "Price %s" % suffix,
                           org.id, element_type="Assessment", offering_level=3, player="Us")
        db.session.commit()
        db.session.remove()
    return out


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def test_capture_every_framework_at_both_widths_and_themes(browser, live_server, walk_enabled):
    base = live_server
    org = _seed()
    os.makedirs(WALK_DIR, exist_ok=True)

    for theme in ("light", "dark"):
        for width, tag in ((1440, "1440"), (390, "390")):
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": width, "height": 900}
            )
            context.add_init_script(
                "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
                "localStorage.setItem('theme', '%s');" % theme
            )
            page = context.new_page()
            _login(page, base, org["email"])
            for fw in FRAMEWORKS:
                page.goto(base + "/strategy/" + fw, wait_until="networkidle", timeout=PAGE_TIMEOUT)
                expect(page.get_by_test_id("strategy-grid-" + fw)).to_be_visible(timeout=PAGE_TIMEOUT)
                page.screenshot(
                    path=os.path.join(WALK_DIR, "%s-%s-%s.png" % (fw, tag, theme)),
                    full_page=True,
                )
            context.close()
