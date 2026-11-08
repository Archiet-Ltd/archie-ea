"""An enterprise architect asks where the estate's exposure sits and what the model looks like.

On the risk register the matrix sits beside the ranked list and counts the same risks; each risk in the list
opens its own page. On the element catalogue the diagram view draws the model in bands by layer, joined by the
relationships recorded between its elements. An organisation with no risks is told so.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .test_ranked_risks_journey import _seed

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _page(live_server, browser, org):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    return page


def _counts(page):
    return page.locator("[data-testid=risk-cell]").evaluate_all(
        "(cells) => Object.fromEntries(cells.filter(c => c.dataset.count !== '0')"
        ".map(c => [c.getAttribute('aria-label').match(/Probability (\\d), impact (\\d)/).slice(1).join(','), Number(c.dataset.count)]))")


def test_matrix_sits_beside_the_ranked_list_and_counts_the_same_risks(live_server, browser):
    noun = "Matrix%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], noun)
    page = _page(live_server, browser, org)

    page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    expect(page.locator("#risk-matrix-card")).to_contain_text("Risks by probability and impact")
    # likelihood, impact -> count: low risk (1, 2), big risk (3, 3), loose risk (5, 5)
    assert _counts(page) == {"1,2": 1, "3,3": 1, "5,5": 1}
    listed = page.locator("[data-risk-row]").count()
    assert sum(_counts(page).values()) == listed == 3
    ranked, matrix = page.locator("#ranked-risks").bounding_box(), page.locator("#risk-matrix-card").bounding_box()
    assert matrix["x"] > ranked["x"] + ranked["width"] - 1 and abs(matrix["y"] - ranked["y"]) < 40

    page.locator("[data-risk-row]", has_text="%s big risk" % noun).get_by_role("link", name="%s big risk" % noun).click()
    page.wait_for_url(lambda url: "/risks/" in url and not url.rstrip("/").endswith("/risks"), timeout=PAGE_TIMEOUT)
    expect(page.locator("#risk-rank-reason")).to_contain_text("reaches 3 elements")


def test_organisation_with_no_risks_is_told_so(live_server, browser):
    org = create_fresh_org("enterprise_architect")
    page = _page(live_server, browser, org)

    page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    expect(page.locator("main")).to_contain_text("No risks recorded yet")
    assert page.locator("[data-risk-row]").count() == 0


def test_diagram_view_draws_the_model_in_layer_bands_with_its_relationships(live_server, browser):
    noun = "Layers%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], noun)
    page = _page(live_server, browser, org)

    page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Diagram View").click()
    band = page.locator("#elements-viewpoint-diagram [data-band=application]")
    band.wait_for(state="attached", timeout=PAGE_TIMEOUT)

    header = page.locator("div.rounded-lg", has=page.locator("#elements-viewpoint-diagram")).first
    expect(header).to_contain_text("Application layer")
    expect(header).to_contain_text("relationships")
    # the elements drawn are the ones the relationships join, so the picture has lines in it
    drawn = page.locator("#elements-viewpoint-diagram g.joint-link").count()
    assert drawn >= 3, "the layered diagram drew %d relationships" % drawn
    expect(page.locator("#elements-viewpoint-diagram")).to_contain_text(noun)


def test_on_a_phone_the_matrix_comes_before_the_long_list(live_server, browser):
    noun = "Phone%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], noun)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])

    page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    ranked, matrix = page.locator("#ranked-risks").bounding_box(), page.locator("#risk-matrix-card").bounding_box()
    assert matrix["y"] < ranked["y"], "the matrix sits below the ranked list"
    assert matrix["width"] <= 390


# What a person sees of a band (its fill laid over the first opaque colour behind it) and the fill the diagram
# painted. The paper itself is transparent, so the page behind a band changes with the theme on its own; only the
# painted fill shows whether the diagram drew its bands again.
SEEN_BAND = """() => {
    const band = document.querySelector('#elements-viewpoint-diagram [data-band=application] rect');
    const parse = (text) => text.match(/[\\d.]+/g).map(Number);
    const fill = parse(getComputedStyle(band).fill);
    let behind = null;
    for (let el = band.parentElement; el && !behind; el = el.parentElement) {
        const c = parse(getComputedStyle(el).backgroundColor);
        if (c.length === 3 || c[3] > 0.9) behind = c;
    }
    behind = behind || [255, 255, 255];
    const alpha = fill.length === 4 ? fill[3] : 1;
    const seen = [0, 1, 2].map((i) => Math.round(fill[i] * alpha + behind[i] * (1 - alpha)));
    return { seen, painted: fill.slice(0, 3).map(Math.round) };
}"""


def _open_diagram(live_server, browser, org, *, dark):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1000},
                                  color_scheme="dark" if dark else "light")
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
                            "localStorage.setItem('theme', '%s');" % ("dark" if dark else "light"))
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.evaluate("document.documentElement.classList.toggle('dark', %s)" % ("true" if dark else "false"))
    page.get_by_role("button", name="Diagram View").click()
    page.locator("#elements-viewpoint-diagram [data-band=application]").wait_for(state="attached", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(400)
    return page


def test_diagram_bands_are_dark_on_a_dark_page(live_server, browser):
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], "Dark%s" % uuid.uuid4().hex[:6])
    page = _open_diagram(live_server, browser, org, dark=True)

    band = page.evaluate(SEEN_BAND)
    assert max(band["painted"]) < 120, "a band is painted light on a dark page: rgb%s" % (tuple(band["painted"]),)
    assert max(band["seen"]) < 120, "a band looks light on a dark page: rgb%s" % (tuple(band["seen"]),)


def test_diagram_repaints_when_the_theme_changes_after_it_opens(live_server, browser):
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], "Swap%s" % uuid.uuid4().hex[:6])
    page = _open_diagram(live_server, browser, org, dark=False)
    light = page.evaluate(SEEN_BAND)
    assert min(light["painted"]) > 180 and min(light["seen"]) > 180, "a band is not light on a light page: %s" % light

    page.evaluate("document.documentElement.classList.add('dark')")
    page.wait_for_timeout(500)
    dark = page.evaluate(SEEN_BAND)
    assert max(dark["painted"]) < 120, "the open diagram kept its light bands after the page went dark: %s" % dark
    assert max(dark["seen"]) < 120, "the open diagram looks light after the page went dark: %s" % dark

    page.evaluate("document.documentElement.classList.remove('dark')")
    page.wait_for_timeout(500)
    assert page.evaluate(SEEN_BAND) == light, "the open diagram did not return to its light bands"
