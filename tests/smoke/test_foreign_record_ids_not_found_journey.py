"""An enterprise architect opens only their own organisation's saved answers, canvases and business cases.

Two organisations each hold a small connected model, a Business Model Canvas and a business case,
and each has saved an answer about what breaks if its service fails. The architect of the first
opens their own answer, canvas and business case from the list pages and reads their content.
Then they type the second organisation's answer address, canvas id and business case id into the
address bar: each answers not-found, reads the same as an address or id that was never issued, and
shows nothing of the second organisation. The second organisation's architect still opens their own.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("ISOLATION_WALK_DIR")
NEVER_ISSUED_ADDRESS = "0" * 32
NEVER_ISSUED_ID = 999999999


def _organisation(noun_prefix):
    from app import create_app, db
    from app.models.business_case import BusinessCase
    from app.models.business_model import BusinessModelCanvas

    org = create_fresh_org("enterprise_architect")
    graph = seed_impact_graph(org["org_id"], noun_prefix=noun_prefix)
    app = create_app("testing")
    with app.app_context():
        canvas = BusinessModelCanvas(name="%s canvas" % graph["noun"], organization_id=org["org_id"])
        case = BusinessCase(title="%s business case" % graph["noun"], organization_id=org["org_id"])
        db.session.add_all([canvas, case])
        db.session.commit()
        ids = {"canvas": canvas.id, "case": case.id}
        db.session.remove()
    return {
        "email": org["emails"]["enterprise_architect"], "graph": graph, "names": graph["names"],
        "canvas_name": "%s canvas" % graph["noun"], "case_title": "%s business case" % graph["noun"], **ids,
    }


@pytest.fixture(scope="module")
def two_orgs(live_server):
    return {"a": _organisation("Isofirst"), "b": _organisation("Isosecond")}


@pytest.fixture
def context_factory(browser):
    opened = []

    def _new(width=1440, height=900, scheme="light"):
        ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        ctx.set_default_timeout(PAGE_TIMEOUT)
        ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
        opened.append(ctx)
        return ctx.new_page()

    yield _new
    for ctx in opened:
        ctx.close()


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _main_text(page):
    return re.sub(r"\s+", " ", page.locator("main").first.inner_text())


def _save_answer(page, base, org):
    """Open the saved answer for the organisation's service the way an alert link does, and
    return its permanent address. The answer lists what depends on the service."""
    page.goto("%s/intelligence/answers/element/%s" % (base, org["graph"]["service"]))
    page.wait_for_url("**/intelligence/answers/*")
    address = page.url.rsplit("/", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{32}", address), page.url
    expect(page.locator("[data-answer-rows]")).to_contain_text(org["names"]["gateway"])
    return address


def _opens_own_records(page, base, org, address, label, shots=True):
    """From each list page, the person clicks their own record and reads its content."""
    page.goto(base + "/intelligence/answers")
    page.locator('[data-answer-item="%s"] a' % address).click()
    page.wait_for_url("**/intelligence/answers/%s" % address)
    rows = page.locator("[data-answer-rows]")
    expect(rows).to_contain_text(org["names"]["gateway"])
    expect(rows).to_contain_text(org["names"]["portal"])
    if shots:
        _shot(page, "%s-own-answer" % label)

    page.goto(base + "/business-model/")
    page.get_by_test_id("bmc-canvas-card-%s" % org["canvas"]).click()
    page.wait_for_url("**/business-model/%s" % org["canvas"])
    expect(page.locator("main")).to_contain_text(org["canvas_name"])
    expect(page.locator("[data-canvas-empty]").first).to_be_visible()
    if shots:
        _shot(page, "%s-own-canvas" % label)

    page.goto(base + "/business-case/")
    page.get_by_test_id("bc-row-%s" % org["case"]).locator("a").click()
    page.wait_for_url("**/business-case/%s" % org["case"])
    expect(page.locator("main")).to_contain_text(org["case_title"])
    expect(page.get_by_test_id("canvas-unclassified")).to_be_visible()
    if shots:
        _shot(page, "%s-own-case" % label)


def _not_found_like_never_issued(page, base, path, foreign, never_issued, other):
    """The other organisation's record answers 404 and reads exactly as one never issued,
    apart from the id the person typed, and names nothing of the other organisation."""
    missing = page.goto("%s%s/%s" % (base, path, never_issued))
    assert missing.status == 404, (path, missing.status)
    missing_text = _main_text(page).replace(str(never_issued), "ID")

    response = page.goto("%s%s/%s" % (base, path, foreign))
    assert response.status == 404, (path, response.status)
    foreign_text = _main_text(page)
    for name in list(other["names"].values()) + [other["canvas_name"], other["case_title"], other["graph"]["noun"]]:
        assert name not in foreign_text, (path, name)
    assert foreign_text.replace(str(foreign), "ID") == missing_text, (path, foreign_text, missing_text)
    return foreign_text


def test_an_architect_opens_their_own_records_and_not_another_organisations(
        context_factory, live_server, two_orgs):
    first, second = two_orgs["a"], two_orgs["b"]

    page_b = context_factory()
    sign_in(page_b, live_server, second["email"])
    second_address = _save_answer(page_b, live_server, second)

    page = context_factory()
    sign_in(page, live_server, first["email"])
    first_address = _save_answer(page, live_server, first)
    _opens_own_records(page, live_server, first, first_address, "first-1440-light")

    answer_text = _not_found_like_never_issued(
        page, live_server, "/intelligence/answers", second_address, NEVER_ISSUED_ADDRESS, second)
    assert "couldn't find that page" in answer_text, answer_text
    _shot(page, "first-1440-light-foreign-answer")
    api = page.context.request.get("%s/api/v1/intelligence/answers/%s" % (live_server, second_address))
    assert api.status == 404, api.status
    assert second["names"]["gateway"] not in api.text()

    canvas_text = _not_found_like_never_issued(
        page, live_server, "/business-model", second["canvas"], NEVER_ISSUED_ID, second)
    assert "Canvas not found" in canvas_text, canvas_text
    _shot(page, "first-1440-light-foreign-canvas")
    page.get_by_role("link", name="Back to Business Model Canvas").click()
    page.wait_for_url("**/business-model/")
    expect(page.get_by_test_id("bmc-canvas-card-%s" % first["canvas"])).to_contain_text(first["canvas_name"])
    expect(page.locator("main")).not_to_contain_text(second["canvas_name"])

    case_text = _not_found_like_never_issued(
        page, live_server, "/business-case", second["case"], NEVER_ISSUED_ID, second)
    assert "Business case not found" in case_text, case_text
    _shot(page, "first-1440-light-foreign-case")

    # The answers list, the canvas list and the case list name only the first organisation's records.
    page.goto(live_server + "/intelligence/answers")
    expect(page.locator('[data-answer-item="%s"]' % first_address)).to_be_visible()
    assert page.locator('[data-answer-item="%s"]' % second_address).count() == 0
    page.goto(live_server + "/business-case/")
    expect(page.get_by_test_id("bc-row-%s" % first["case"])).to_contain_text(first["case_title"])
    assert page.get_by_test_id("bc-row-%s" % second["case"]).count() == 0

    # The second organisation's architect still opens their own, and not the first's.
    _opens_own_records(page_b, live_server, second, second_address, "second-1440-light")
    _not_found_like_never_issued(
        page_b, live_server, "/intelligence/answers", first_address, NEVER_ISSUED_ADDRESS, first)
    _not_found_like_never_issued(page_b, live_server, "/business-model", first["canvas"], NEVER_ISSUED_ID, first)
    _not_found_like_never_issued(page_b, live_server, "/business-case", first["case"], NEVER_ISSUED_ID, first)


def test_on_a_phone_the_other_organisations_canvas_is_not_found_and_their_own_opens(
        context_factory, live_server, two_orgs):
    first, second = two_orgs["a"], two_orgs["b"]
    page = context_factory(390, 844, "dark")
    sign_in(page, live_server, first["email"])

    page.goto(live_server + "/business-model/")
    page.get_by_test_id("bmc-canvas-card-%s" % first["canvas"]).click()
    page.wait_for_url("**/business-model/%s" % first["canvas"])
    expect(page.locator("main")).to_contain_text(first["canvas_name"])
    _shot(page, "first-390-dark-own-canvas")

    text = _not_found_like_never_issued(
        page, live_server, "/business-model", second["canvas"], NEVER_ISSUED_ID, second)
    assert "Canvas not found" in text
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    _shot(page, "first-390-dark-foreign-canvas")

    _not_found_like_never_issued(page, live_server, "/business-case", second["case"], NEVER_ISSUED_ID, second)
    _shot(page, "first-390-dark-foreign-case")
