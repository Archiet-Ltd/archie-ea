"""Open-place-add-reload journey for the three strategy canvases.

A signed-in architect opens the BCG matrix, the Ansoff matrix and the Blue
Ocean strategy canvas in a seeded organisation. Each opens in its own layout
with the organisation's own products, services, assessments and goals already
in their boxes; an empty box says what to record; a placed element links to a
page that actually renders the element. The architect adds a product from the
canvas and, after a full reload, it is still in its box. A second organisation
sees none of the first's elements.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
ADD_API = re.compile(r"/strategy-canvas/[a-z_]+/add")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def second_org(seeded):
    """A second organisation with its own signed-in-able user and one profiled
    product of its own, for the cross-organisation isolation check."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement
        from app.models.organization import Organization
        from app.models.user import Role, User

        architect_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="Strat second org %s" % suffix,
                           slug="strat-second-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        email = "strat.second.%s@example.com" % suffix
        user = User(email=email, first_name="Second", last_name="Org",
                    organization_id=org.id, enterprise_role="business_architect",
                    confirmed=True)
        user.role = architect_role
        user.password = PASSWORD
        db.session.add(user)
        db.session.add(ArchiMateElement(
            name="Org B only product %s" % suffix, type="Product", layer="Business",
            organization_id=org.id, scope="enterprise", acm_properties={"profile": "dog"},
        ))
        db.session.commit()
        return {"org_id": org.id, "email": email}


@pytest.mark.smoke
@pytest.mark.journey
def test_three_strategy_canvases_open_place_add_and_persist(browser, live_server, seeded):
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])

        # ── The index lists all three, each a working link ──────────────────
        page.goto(live_server + "/strategy-canvas/", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        for key in ("bcg_matrix", "ansoff_matrix", "blue_ocean_strategy"):
            expect(page.get_by_test_id(f"strategy-canvas-link-{key}")).to_be_visible(
                timeout=PAGE_TIMEOUT
            )

        # ── BCG: products already placed in their quadrants ────────────────
        page.goto(live_server + "/strategy-canvas/bcg_matrix",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-matrix")).to_be_visible(timeout=PAGE_TIMEOUT)
        # The two axes a BCG reader expects are labelled.
        expect(page.get_by_test_id("canvas-axis-x")).to_contain_text("market share")
        expect(page.get_by_test_id("canvas-axis-y")).to_contain_text("Market growth")
        # Each seeded product sits in the box the goal needs it in.
        stars = page.get_by_test_id("canvas-box-stars")
        expect(stars.get_by_text(re.compile("Smoke flagship"))).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-question_marks").get_by_text(
            re.compile("Smoke newcomer"))).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-cash_cows").get_by_text(
            re.compile("Smoke earner"))).to_be_visible(timeout=PAGE_TIMEOUT)
        # Dogs was seeded empty: its reason says what to record there.
        dogs_reason = page.get_by_test_id("canvas-box-reason-dogs")
        expect(dogs_reason).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(dogs_reason).to_contain_text("dogs")

        # A placed element links to a page that renders that element.
        star_link = stars.get_by_role("link", name=re.compile("Smoke flagship")).first
        href = star_link.get_attribute("href")
        assert href and "/architecture/" in href, f"star link href unexpected: {href}"
        star_link.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        # The element's own name is the heading of its detail page — a real
        # page with content, not an empty state or a bounce back to the canvas.
        expect(page.get_by_role("heading", name=re.compile("Smoke flagship"))).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
        assert "/strategy-canvas/" not in page.url, "link bounced back to the canvas"

        # ── Add a product to the canvas and prove it survives a reload ──────
        unique = "Smoke added star %s" % uuid.uuid4().hex[:6]
        page.goto(live_server + "/strategy-canvas/bcg_matrix",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        add_name = page.get_by_test_id("canvas-add-name-stars")
        add_name.fill(unique)
        with page.expect_response(
            lambda r: bool(ADD_API.search(r.url)) and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as added:
            page.get_by_test_id("canvas-add-submit-stars").click()
        assert added.value.status < 400, f"add POST failed: {added.value.status}"

        page.goto(live_server + "/strategy-canvas/bcg_matrix",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-stars").get_by_text(unique)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )

        # ── Ansoff: the 2x2 and its seeded quadrants ───────────────────────
        page.goto(live_server + "/strategy-canvas/ansoff_matrix",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-matrix")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-market_penetration").get_by_text(
            re.compile("Smoke upsell"))).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-market_development").get_by_text(
            re.compile("Smoke new region"))).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-reason-diversification")).to_be_visible(
            timeout=PAGE_TIMEOUT
        )

        # ── Blue Ocean: the value curve and the four-action grid ───────────
        page.goto(live_server + "/strategy-canvas/blue_ocean_strategy",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-value-curve-svg")).to_be_visible(timeout=PAGE_TIMEOUT)
        # Both competing factors are on the curve's axis.
        svg = page.get_by_test_id("canvas-value-curve-svg")
        expect(svg.get_by_text(re.compile("Smoke price"))).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(svg.get_by_text(re.compile("Smoke ease of use"))).to_be_visible(timeout=PAGE_TIMEOUT)
        # At least one plotted series (a line) exists.
        expect(page.get_by_test_id("canvas-curve-series-0")).to_be_attached(timeout=PAGE_TIMEOUT)
        # The four actions: seeded eliminate/raise filled, reduce/create empty.
        expect(page.get_by_test_id("canvas-box-eliminate").get_by_text(
            re.compile("Smoke drop paperwork"))).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-raise").get_by_text(
            re.compile("Smoke lift support"))).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-reason-reduce")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-reason-create")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_a_second_organisation_sees_none_of_the_first_orgs_elements(
    browser, live_server, seeded, second_org
):
    """The seeded organisation's products are on its BCG matrix; a user in a
    different organisation opens the same canvas and sees none of them."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    try:
        # First organisation: its flagship is a star.
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(live_server + "/strategy-canvas/bcg_matrix",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-box-stars").get_by_text(
            re.compile("Smoke flagship"))).to_be_visible(timeout=PAGE_TIMEOUT)

        # Second organisation: a fresh context, its own user, the same canvas.
        context.close()
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        _login(page, live_server, second_org["email"])
        page.goto(live_server + "/strategy-canvas/bcg_matrix",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("canvas-matrix")).to_be_visible(timeout=PAGE_TIMEOUT)
        # None of the first org's seeded products are anywhere on this page.
        for name in ("Smoke flagship", "Smoke earner", "Smoke newcomer"):
            expect(page.get_by_text(re.compile(name))).to_have_count(0, timeout=PAGE_TIMEOUT)
        # And every quadrant reads empty for the fresh organisation.
        expect(page.get_by_test_id("canvas-box-reason-stars")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
