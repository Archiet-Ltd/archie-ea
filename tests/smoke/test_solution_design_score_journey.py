"""Journey, in a browser: a solution architect sees one design score, the same everywhere.

The demonstration organisation is seeded by the product's own seed. The person who signs in is a solution architect
of that organisation (persona solution_architect, platform role Architect, not an administrator). They:

* open "Field Calibration Platform" from the Solutions list and read the same "n of 10" on its row, on the
  solution page, on the review packet and in the solution document, and n is what the record supports;
* link a data object to the design from the solution page's data section;
* read one more on every one of those four surfaces, and the review packet stops listing data as missing.

The seed links two data objects to the solution; the journey starts with those links removed so that adding
one is the step a person takes. A second test proves another organisation never sees this score.
"""
import os
import pathlib
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SOLUTION = "Field Calibration Platform"
DATA_OBJECT = "Calibration result"
ARCHITECT_EMAIL = "sol.architect@lantern-quay.example.com"
SHOTS = pathlib.Path(os.environ.get("SOLS1_SHOTS", "/tmp/sols1-shots"))
SCORE = re.compile(r"(\d+) of 10")


@pytest.fixture(scope="module")
def demo(live_server, seeded):
    """Seed the demonstration organisation, add a solution architect to it and
    start the solution without its data objects."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
            from app.models.archimate_core import ArchiMateElement
            from app.models.solution_models import Solution, SolutionArchiMateElement
            from app.models.user import Role, User

            org_id = User.query.filter_by(email="demo@lantern-quay.example.com").one().organization_id
            user = User.query.filter_by(email=ARCHITECT_EMAIL).first()
            if user is None:
                user = User(email=ARCHITECT_EMAIL, first_name="Sol", last_name="Architect",
                            organization_id=org_id, enterprise_role="solution_architect", confirmed=True)
                user.role = Role.query.filter_by(name="Architect").one()
                user.password = PASSWORD
                db.session.add(user)
            solution = Solution.query.filter_by(name=SOLUTION, organization_id=org_id).one()
            data_ids = [e.id for e in ArchiMateElement.query.filter_by(organization_id=org_id, type="DataObject")]
            SolutionArchiMateElement.query.filter(
                SolutionArchiMateElement.solution_id == solution.id,
                SolutionArchiMateElement.element_id.in_(data_ids),
            ).delete(synchronize_session=False)
            db.session.commit()
            out = {"solution_id": solution.id, "org_id": org_id,
                   "role": user.enterprise_role, "platform_role": user.role.name,
                   "is_org_admin": bool(user.is_org_admin)}
            db.session.remove()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return out


def _expected(solution_id):
    """What the record supports: the checks passed, from the one service."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution
        from app.services.solution_design_score_service import compute

        score = compute(db.session.get(Solution, solution_id))
        db.session.remove()
        return score.passed, {c["key"] for c in score.missing}


def _score_on(page, scope=None):
    """The "n of 10" a surface shows, read from its design score element."""
    root = scope if scope is not None else page
    el = root.locator("[data-design-score]").first
    el.wait_for(state="attached", timeout=PAGE_TIMEOUT)
    match = SCORE.search(el.get_attribute("data-design-score") or "")
    assert match, "no design score on %s" % page.url
    return int(match.group(1))


def _four_surfaces(page, base, solution_id):
    """Row, page, review packet, document: the score each one shows."""
    shown = {}
    page.goto(base + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("tr", has=page.get_by_role("link", name=SOLUTION, exact=True)).first
    shown["row"] = _score_on(page, row)
    row.get_by_role("link", name=SOLUTION, exact=True).click()
    page.wait_for_url(re.compile(r"/solutions/%d(\?|$|#)" % solution_id), timeout=PAGE_TIMEOUT)
    shown["page"] = _score_on(page)
    page.goto("%s/solutions/%d/review-packet" % (base, solution_id), wait_until="domcontentloaded")
    shown["packet"] = _score_on(page)
    data_missing = page.locator('[data-check="data"][data-passed="false"]').count() > 0
    page.goto("%s/solutions/%d/sad" % (base, solution_id), wait_until="domcontentloaded")
    shown["document"] = _score_on(page)
    return shown, data_missing


def test_the_architect_sees_one_design_score_and_adds_a_data_object(browser, live_server, demo):
    assert demo["role"] == "solution_architect"
    assert demo["platform_role"] not in ("Administrator", "Platform Admin") and not demo["is_org_admin"]
    sid = demo["solution_id"]
    before, missing = _expected(sid)
    assert "data" in missing, "the journey needs the data check open: %s" % missing

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        sign_in(page, live_server, ARCHITECT_EMAIL)
        shown, packet = _four_surfaces(page, live_server, sid)
        assert shown == {k: before for k in shown}, shown
        assert packet, "the review packet should list data as missing"

        # Link a data object from the solution page's data section.
        page.goto("%s/solutions/%d" % (live_server, sid), wait_until="networkidle", timeout=PAGE_TIMEOUT)
        section = page.locator("#data_information")
        section.scroll_into_view_if_needed()
        link = section.get_by_role("button", name=re.compile(r"^\s*Link\s*$")).first
        if not link.is_visible():
            section.locator("button[aria-expanded]").first.click()
        link.click()
        dialog = page.locator("#bp-link-modal")
        dialog.get_by_placeholder("Search elements by name…").fill(DATA_OBJECT)
        result = dialog.locator("div.group", has_text=DATA_OBJECT).first
        result.click()
        result.get_by_text("Linked").wait_for(timeout=PAGE_TIMEOUT)

        after, missing_after = _expected(sid)
        assert after == before + 1 and "data" not in missing_after
        shown, packet = _four_surfaces(page, live_server, sid)
        assert shown == {k: before + 1 for k in shown}, shown
        assert not packet, "the review packet still lists data as missing"
        assert not errors, errors
    finally:
        context.close()


def test_another_organisation_never_sees_the_score(browser, live_server, demo):
    other = create_fresh_org("solution_architect")
    email = other["emails"]["solution_architect"]
    sid = demo["solution_id"]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        for path in ("/solutions/%d" % sid, "/solutions/%d/review-packet" % sid,
                     "/solutions/%d/sad" % sid, "/api/solutions/%d/completeness" % sid):
            response = page.goto(live_server + path, wait_until="domcontentloaded")
            assert response.status in (403, 404), (path, response.status)
            assert page.locator("[data-design-score]").count() == 0, path
            assert SOLUTION not in page.content(), path
        page.goto(live_server + "/solutions/", wait_until="domcontentloaded")
        assert SOLUTION not in page.content()
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_walk_screenshots(browser, live_server, demo, width, theme):
    """Screenshots of the list, the page and the review packet for the walk."""
    sid = demo["solution_id"]
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, ARCHITECT_EMAIL)
        SHOTS.mkdir(parents=True, exist_ok=True)
        for name, path in (("list", "/solutions/"), ("page", "/solutions/%d" % sid),
                           ("review-packet", "/solutions/%d/review-packet" % sid)):
            page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            if theme == "dark":
                page.evaluate("document.documentElement.classList.add('dark')")
            assert page.locator("[data-design-score]").count() > 0, path
            page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, theme, width))), full_page=True)
    finally:
        context.close()
