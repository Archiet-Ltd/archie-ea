"""An architect reads the same figures on every page that shows them.

The person: an enterprise architect whose organisation holds twenty Implementation and migration
elements (three read from a GitHub repository), a gap entered in the gap register, 350 capabilities
and a risk with nothing linked to it. A second organisation holds its own capabilities and risk.

The goal: the model map and the Implementation page give the layer one count, and the page names the
one record it lists that the map does not draw; the investment matrix's Total Capabilities is the
organisation's 350 under the sidebar's trail; the risk register and the simulations page show the risk
with one confidence and one reason; and every link followed lands on a page about the thing it named.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from tests.test_implementation_layer_one_count import seed_implementation_layer

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]
CAPABILITIES = 350


def _shot(page, name, viewport, scheme):
    """Save a screenshot when SMOKE_WALK_DIR names a folder; the journey asserts either way."""
    walk = os.environ.get("SMOKE_WALK_DIR")
    if walk:
        os.makedirs(walk, exist_ok=True)
        page.screenshot(path=os.path.join(walk, "figures-%s-%d-%s.png" % (name, viewport["width"], scheme)),
                        full_page=True)


def _seed(label, capabilities, risk_title):
    from app import create_app, db
    from app.models.risk import Risk
    from app.models.unified_capability import UnifiedCapability

    org = create_fresh_org("enterprise_architect")
    app = create_app("testing")
    with app.app_context():
        seed_implementation_layer(org["org_id"], "%s %s" % (label, org["suffix"]))
        db.session.add_all(
            UnifiedCapability(name="%s capability %03d %s" % (label, n, org["suffix"]), level=1,
                              code="%s-%s-%03d" % (label[:3].upper(), org["suffix"][:6], n),
                              organization_id=org["org_id"], scope="tenant")
            for n in range(capabilities))
        db.session.add(Risk(title="%s %s" % (risk_title, org["suffix"]), likelihood=4, impact=4,
                            organization_id=org["org_id"], owner="Delivery lead"))
        db.session.commit()
        db.session.remove()
    org["risk"] = "%s %s" % (risk_title, org["suffix"])
    return org


@pytest.fixture(scope="module")
def orgs():
    """Two organisations, plus capability rows with no owner left by the second (as old rows are)."""
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    mine = _seed("Mine", CAPABILITIES, "Generated code is unverified")
    theirs = _seed("Theirs", 7, "Their supplier may fail")
    app = create_app("testing")
    with app.app_context():
        unowned = [UnifiedCapability(name="Unowned capability %d %s" % (n, theirs["suffix"]), level=1,
                                     code="UNO-%s-%d" % (theirs["suffix"][:6], n), organization_id=None,
                                     source_org_id=theirs["org_id"]) for n in range(4)]
        db.session.add_all(unowned)
        db.session.commit()
        ids = [row.id for row in unowned]
        db.session.remove()
    yield {"mine": mine, "theirs": theirs}
    with app.app_context():
        UnifiedCapability.query.filter(UnifiedCapability.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        db.session.remove()


def _number(text):
    return int(re.search(r"\d+", text).group(0))


def _footer_after(page, row):
    footer = row.get_by_test_id("result-footer").first
    return footer.get_attribute("data-confidence"), " ".join(
        footer.get_by_test_id("result-footer-confidence").inner_text().split())


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_the_figures_agree_on_every_page_and_with_the_seed(browser, live_server, orgs, viewport, scheme):
    mine, theirs = orgs["mine"], orgs["theirs"]
    # The counts arrive after the page: every assertion waits as long as a page may take to load.
    expect.set_options(timeout=PAGE_TIMEOUT)
    context = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    if scheme == "dark":
        context.add_init_script("document.addEventListener('DOMContentLoaded', () => "
                                "document.documentElement.classList.add('dark'));")
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])

        # The model map: the layer's button reads the twenty model elements.
        page.goto(live_server + "/archimate/model-map", wait_until="domcontentloaded")
        layer_button = page.get_by_test_id("model-map-layer-implementation")
        expect(layer_button).to_contain_text("(20)")
        on_map = _number(layer_button.inner_text().split("(")[-1])
        _shot(page, "model-map", viewport, scheme)

        # Its button opens the layer on the map with those twenty drawn.
        layer_button.click()
        page.wait_for_url(re.compile(r"layer=implementation"))
        page.locator("#impact-svg[data-rendered-ms]").wait_for(state="attached")
        assert int(page.locator("#impact-svg").get_attribute("data-node-count")) == on_map

        # The Implementation page: tab, header and Elements card read one count, and name the gap the map
        # does not draw, so the two pages' figures add up.
        page.goto(live_server + "/architecture/", wait_until="domcontentloaded")
        page.get_by_test_id("layer-spine").get_by_role("link", name=re.compile("^Implementation")).click()
        page.wait_for_url(re.compile(r"layer=implementation"))
        header = page.get_by_test_id("layer-count-label")
        expect(header).to_have_text("21 elements in this layer, 1 of them not on the model map")
        tab = page.get_by_test_id("layer-spine").locator('a[aria-current="page"] span.rounded-full')
        expect(tab).to_have_text("21")
        card = page.get_by_test_id("layer-elements-card")
        expect(card).to_contain_text("21")
        expect(card).to_contain_text("1 entered in a register and not on the model map")
        expect(page.get_by_test_id("layer-list-total")).to_have_text("21 total")
        assert 21 - 1 == on_map
        _shot(page, "implementation", viewport, scheme)

        # A search narrows the list, not the layer's count; what it finds is the elements a file wrote.
        page.get_by_test_id("filter-search").fill("Imported work")
        expect(page.get_by_test_id("layer-list-total")).to_have_text("2 of 21 match")
        expect(header).to_have_text("21 elements in this layer, 1 of them not on the model map")
        # The table at 1440 and the cards at 390 list the two found, each by its name.
        expect(page.locator('[data-field="element-name"]:visible').filter(has_text="Imported work")).to_have_count(2)
        assert theirs["suffix"] not in page.locator("main").inner_text()

        # The investment matrix: the organisation's 350 capabilities, under Home, Spend, Investments.
        page.goto(live_server + "/strategic/investment-matrix", wait_until="domcontentloaded")
        total = page.get_by_test_id("total-capabilities")
        expect(total).to_contain_text(str(CAPABILITIES))
        assert _number(total.locator('[data-slot="card-title"]').inner_text()) == CAPABILITIES
        expect(page.get_by_test_id("total-capabilities-scope")).to_have_text("Your organisation's capabilities")
        crumbs = page.locator('nav[aria-label="Breadcrumb"]').first
        expect(crumbs).to_contain_text("Spend")
        expect(crumbs).to_contain_text("Investments")
        expect(crumbs).not_to_contain_text("Strategic Planning")
        expect(page.locator("main")).not_to_contain_text("Prioritization")
        assert "Unowned capability" not in page.content()
        _shot(page, "investment-matrix", viewport, scheme)

        # The risk register: the risk's confidence and its reason.
        page.goto(live_server + "/risks/", wait_until="domcontentloaded")
        ranked = page.locator("#ranked-risks li", has_text=mine["risk"])
        expect(ranked).to_have_count(1)
        on_register = _footer_after(page, ranked)
        assert on_register[0] == "low"
        assert "Linked elements: none recorded" in on_register[1]
        expect(page.locator("main")).not_to_contain_text(theirs["risk"])
        ranked.scroll_into_view_if_needed()
        _shot(page, "risks", viewport, scheme)
        ranked.get_by_role("link", name=mine["risk"]).click()
        page.wait_for_url(re.compile(r"/risks/\d+$"))
        expect(page.locator("main")).to_contain_text(mine["risk"])

        # The simulations page carries no ranked list; it links to the one on Impact Analysis.
        page.goto(live_server + "/simulations", wait_until="domcontentloaded")
        link = page.get_by_test_id("priority-actions-link")
        expect(link).to_be_visible()
        expect(page.get_by_test_id("priority-action-row")).to_have_count(0)
        expect(page.locator("main")).not_to_contain_text(theirs["risk"])
        _shot(page, "simulations", viewport, scheme)
        link.click()

        # Impact Analysis: the same risk, the same confidence, the same reason.
        page.wait_for_url(re.compile(r"/enterprise/analysis/impact-analysis#priority-actions$"))
        action = page.get_by_test_id("priority-action-row").filter(has_text=mine["risk"])
        expect(action).to_have_count(1)
        assert _footer_after(page, action) == on_register
        expect(page.locator("main")).not_to_contain_text(theirs["risk"])
        action.scroll_into_view_if_needed()
        _shot(page, "impact-analysis", viewport, scheme)
        action.get_by_test_id("priority-action-link").click()
        page.wait_for_url(re.compile(r"/risks/\d+"))
        expect(page.locator("main")).to_contain_text(mine["risk"])
    finally:
        context.close()
