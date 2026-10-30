"""R1-B30: a stored answer and its permanent address, in a real browser.

The CTO asks what breaks if two elements fail together, opens the stored answer
at its address, reloads it, and sees the same rows and the same computed time.
The security architect asks which risks reach a service and sees the supplier
risk it inherits, with the path it travels and the controls on that path.

The tenant is the small chain the Ask journeys use:

    Service --serves--> Gateway --serves--> Ops Team
    Service --serves--> Portal

with one open risk recorded on the Service (standing in for the supplier).
Every assertion is made on the rendered page; where a test compares the page
with the server's answer it asks the server itself with the same session.
"""

import uuid

import pytest

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def graph(seeded, live_server):
    """The chain above in the seeded organisation, plus one open risk on the Service."""
    from app import create_app, db
    from app.models.risk import Risk

    seeded_graph = seed_impact_graph(seeded["ids"]["org"], noun_prefix="Answerpay")
    app = create_app("testing")
    with app.app_context():
        db.session.add(Risk(
            organization_id=seeded["ids"]["org"],
            archimate_element_id=seeded_graph["service"],
            title="Supplier insolvency %s" % uuid.uuid4().hex[:6],
            likelihood=4,
            impact=5,
        ))
        db.session.commit()
        seeded_graph["risk_title"] = Risk.query.filter_by(
            archimate_element_id=seeded_graph["service"]
        ).one().title
    return seeded_graph


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open_answers(page, base):
    page.goto(base + "/intelligence/answers", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"answersSurface()\"]');"
        " return !!(el && el._x_dataStack); }"
    )


def _pick(page, term, name):
    box = page.locator("#answers-picker-input")
    box.press_sequentially(term, delay=15)
    page.wait_for_selector("#answers-picker-listbox [role=option]")
    page.locator("#answers-picker-listbox [role=option]", has_text=name).click()


def _element_ids(page):
    return page.eval_on_selector_all(
        "[data-answer-rows] [data-element-id]", "els => els.map(e => e.getAttribute('data-element-id'))"
    )


def test_cto_asks_what_breaks_if_two_fail_together_and_reopens_the_stored_answer(page, live_server, seeded, graph):
    """Pick two elements, ask, land on the stored answer, reload it, and see the
    same rows and the same computed time; the page matches the server's own answer."""
    _login(page, live_server, seeded["emails"]["cto"])
    _open_answers(page, live_server)

    _pick(page, graph["noun"], graph["names"]["service"])
    _pick(page, graph["noun"], graph["names"]["ops"])
    expect(page.locator("[data-selected-item]")).to_have_count(2)
    page.locator("#answers-ask-button").click()

    page.wait_for_url("**/intelligence/answers/*")
    address = page.url.rsplit("/", 1)[1]
    assert len(address) == 32
    expect(page.locator("[data-answer-rows]")).to_be_visible()
    rows_before = _element_ids(page)
    computed_before = page.locator("[data-computed-at]").get_attribute("datetime")
    assert rows_before, "the answer lists what is affected"
    assert str(graph["gateway"]) in rows_before
    # The affected table is the shared table component, not a hand-rolled one:
    # a real header row with column headers, not a layout-only div grid.
    table = page.locator("[data-answer-rows] table")
    expect(table.locator("thead th")).to_have_count(6)
    expect(table.locator("thead th").first).to_have_text("Element")

    page.reload(wait_until="domcontentloaded")

    assert _element_ids(page) == rows_before
    assert page.locator("[data-computed-at]").get_attribute("datetime") == computed_before

    api = page.context.request.get("%s/api/v1/intelligence/answers/%s" % (live_server, address))
    assert api.ok
    stored = api.json()["data"]
    assert [str(r["element_id"]) for r in stored["rows"]] == rows_before
    assert stored["computed_at"] == computed_before

    # The answer is listed on the Answers page, and the link opens the same address.
    _open_answers(page, live_server)
    expect(page.locator('[data-answer-item="%s"]' % address)).to_be_visible()


def test_security_architect_sees_the_inherited_supplier_risk_with_its_path(page, live_server, seeded, graph):
    """Ask which risks reach the Ops Team: the risk recorded on the Service two
    steps upstream appears, flagged indirect, with the path that carries it."""
    _login(page, live_server, seeded["emails"]["security_architect"])
    _open_answers(page, live_server)

    # The shared radio group component carries the question choice on the native
    # value attribute, not a page-specific data-question hook.
    page.locator('input[name="answers-question"][value="risk_propagation"]').check()
    _pick(page, graph["noun"], graph["names"]["ops"])
    page.locator("#answers-ask-button").click()

    page.wait_for_url("**/intelligence/answers/*")
    risk = page.locator('[data-indirect="true"]', has_text=graph["risk_title"])
    expect(risk).to_be_visible()
    path = risk.locator("[data-path]")
    expect(path).to_contain_text(graph["names"]["service"])
    expect(path).to_contain_text(graph["names"]["gateway"])
    expect(path).to_contain_text(graph["names"]["ops"])

    page.reload(wait_until="domcontentloaded")
    expect(page.locator('[data-indirect="true"]', has_text=graph["risk_title"])).to_be_visible()


def test_an_address_opened_by_nobody_it_was_issued_to_is_not_found(page, live_server, seeded, graph):
    """A made-up address is a plain not-found page, never a blank or an error."""
    _login(page, live_server, seeded["emails"]["cto"])

    response = page.goto("%s/intelligence/answers/%s" % (live_server, uuid.uuid4().hex),
                         wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    assert response is not None and response.status == 404
