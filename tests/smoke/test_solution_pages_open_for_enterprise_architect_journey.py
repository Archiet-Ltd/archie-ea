"""An Enterprise Architect opens a solution in their own organisation, and every page that belongs to it.

The person: an Enterprise Architect (platform role Architect, not an administrator, not the author, not a named
stakeholder) in the seeded demonstration organisation. The goal: open "Field Calibration Platform" from the
Solutions list, read its record, and follow every link on the record and every page that belongs to the solution
without being refused. A second organisation's Enterprise Architect asks for the same solution and every one of
its pages and gets the plain not-found page; their own solution opens normally.
"""
import os
import pathlib
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SOLUTION = "Field Calibration Platform"
AUTHOR_EMAIL = "demo@lantern-quay.example.com"
ARCHITECT_EMAIL = "ent.architect@lantern-quay.example.com"
SHOTS = pathlib.Path(os.environ.get("SOLACCESS_SHOTS", "/tmp/solaccess-shots"))
TABS = ("overview", "design", "requirements", "options", "risks", "delivery", "review")
# Pages that belong to a solution and that a person reaches from its record or from the pages around it.
SUB_PAGES = (
    "/traceability", "/migration-roadmap", "/sad", "/codegen", "/options-advisor", "/review-packet",
    "/conformance", "/completeness", "/risks/heatmap", "/communications", "/integration-architecture",
    "/edit", "/export.md",
)


@pytest.fixture(scope="module")
def demo(live_server, seeded):
    """Seed the demonstration organisation and add an Enterprise Architect who did not author anything in it."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
            from app.models.solution_models import Solution
            from app.models.user import Role, User

            author = User.query.filter_by(email=AUTHOR_EMAIL).one()
            org_id = author.organization_id
            user = User.query.filter_by(email=ARCHITECT_EMAIL).first()
            if user is None:
                user = User(email=ARCHITECT_EMAIL, first_name="Ent", last_name="Architect", organization_id=org_id,
                            enterprise_role="enterprise_architect", confirmed=True)
                user.role = Role.query.filter_by(name="Architect").one()
                user.password = PASSWORD
                db.session.add(user)
            solution = Solution.query.filter_by(name=SOLUTION, organization_id=org_id).one()
            db.session.commit()
            out = {"solution_id": solution.id, "org_id": org_id}
            db.session.remove()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return out


def _context(browser, width=1440, theme="light"):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context


def _own_links(page, sid):
    """Every address on the page that is the solution's or one of its sub-pages, without the fragment."""
    hrefs = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href'))")
    found = set()
    for href in hrefs:
        match = re.match(r"^(/solutions/%d(?:/[A-Za-z0-9_./-]*)?(?:\?[^#]*)?)(?:#.*)?$" % sid, href or "")
        if match:
            found.add(match.group(1))
    return found


def _get(page, live_server, path):
    if path.endswith(".md"):
        # A download: fetched with the page's own session, and it must carry the solution's text.
        response = page.context.request.get(live_server + path)
        if response.status == 200:
            assert SOLUTION in response.text(), path
        return response.status
    response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return response.status


def test_the_enterprise_architect_opens_the_solution_and_every_page_that_belongs_to_it(browser, live_server, demo):
    sid = demo["solution_id"]
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, ARCHITECT_EMAIL)
        page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=SOLUTION, exact=True).first.click()
        page.wait_for_url(re.compile(r"/solutions/%d(\?|$|#)" % sid), timeout=PAGE_TIMEOUT)
        page.locator("[data-testid=solution-record]").wait_for(state="visible")
        assert page.get_by_test_id("record-owner").inner_text().strip()
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "record-1440.png"), full_page=True)

        # Every link on the record and its seven tabs that stays inside the solution, plus the pages that belong to it.
        links = set()
        for tab in TABS:
            page.goto("%s/solutions/%d?tab=%s" % (live_server, sid, tab), wait_until="domcontentloaded")
            assert page.locator("[data-testid=solution-record]").is_visible(), tab
            links |= _own_links(page, sid)
        links |= {"/solutions/%d%s" % (sid, tail) for tail in SUB_PAGES}
        assert len(links) >= 8, links

        refused = {}
        empty = {}
        for path in sorted(links):
            status = _get(page, live_server, path)
            if status in (403, 404) or status >= 500:
                refused[path] = status
                continue
            if path.endswith(".md"):
                continue
            # A page that answers 200 must have something on it, and must not be the refusal page.
            text = page.locator("body").inner_text()
            if len(text.strip()) < 150 or "Access denied" in text or "not allowed" in text.lower():
                empty[path] = text[:120]
        assert not refused, refused
        assert not empty, empty
        for name, tail in (("codegen", "/codegen"), ("edit", "/edit")):
            _get(page, live_server, "/solutions/%d%s" % (sid, tail))
            if name == "codegen":
                # The workbench loads its content after the page opens; none of those requests may be refused.
                refusals = []
                page.on("response", lambda r: refusals.append((r.url, r.status)) if r.status == 403 else None)
                page.reload(wait_until="networkidle")
                assert not refusals, refusals
                assert "Loading workbench" not in page.locator("main").inner_text(), "the workbench never loaded"
            page.screenshot(path=str(SHOTS / ("%s-1440.png" % name)), full_page=True)
    finally:
        context.close()
    # The same person on a phone, in the dark theme, reads the record and is not refused the workbench.
    context = _context(browser, width=390, theme="dark")
    page = context.new_page()
    try:
        sign_in(page, live_server, ARCHITECT_EMAIL)
        for name, tail in (("record", ""), ("codegen", "/codegen")):
            assert _get(page, live_server, "/solutions/%d%s" % (sid, tail)) == 200
            assert page.evaluate("() => document.body.scrollWidth <= window.innerWidth + 1") or name == "codegen"
            page.screenshot(path=str(SHOTS / ("%s-390-dark.png" % name)), full_page=True)
    finally:
        context.close()


def test_another_organisation_gets_not_found_for_the_solution_and_every_page(browser, live_server, demo):
    sid = demo["solution_id"]
    other = create_fresh_org("enterprise_architect", platform_role="Architect")
    email = other["emails"]["enterprise_architect"]
    context = _context(browser, width=390, theme="dark")
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        for tail in ("",) + SUB_PAGES:
            path = "/solutions/%d%s" % (sid, tail)
            status = _get(page, live_server, path)
            assert status == 404, (path, status)
        page.goto("%s/solutions/%d" % (live_server, sid), wait_until="domcontentloaded")
        body = page.locator("body").inner_text()
        assert SOLUTION not in body
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "other-org-390-dark.png"), full_page=True)
    finally:
        context.close()
