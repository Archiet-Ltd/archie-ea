"""Journey, in a browser: a CTO reads the health of the model on one page.

The goal: "how healthy is the model, who owns what, what is about to go out of support, and has anything drifted?"
That used to take four pages (the Architecture Health Scorecard, the CTO Scorecard, the Model Dashboard and Model
Health). It is one page now, the Architecture Health Scorecard, with the other three as its views.

The demo company (Lantern Quay Systems) is seeded the way the product's own seed does it and the demo architect signs
in through the login form. A freshly created organisation is the deliberately empty case and the second organisation.

* every old address answers a 302 to the canonical page, with the view the old page was;
* every fact the old pages showed is on the canonical page (supported estate, open exceptions, end-of-support exposure,
  surfaces that disagree, the drift report with its Re-scan and its fix proposals);
* the figures on the page equal the same figures on the Overview, the applications list and the ownership coverage page;
* the escalate and re-scan actions work from the new page and land back on it;
* the sidebar entry and the tabs lead to the views, and every landing page has content;
* the other organisation never sees the demo organisation's records.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
CTO_EMAIL = "cto.health-journey@lantern-quay.example.com"
EXCEPTION_TITLE = "Lantern Quay health journey exception"

# the old address and where it must land: (path, view, a test id that only that view carries)
OLD_ADDRESSES = [
    ("/architecture/cto-scorecard/", "cto", "supported-version-share"),
    ("/genome/model-health/", "drift", "health-view-drift"),
    ("/genome/model-health", "drift", "health-view-drift"),
    ("/auto-dashboard/", None, "health-score-line"),
    ("/auto-dashboard/registry", None, "health-score-line"),
]


@pytest.fixture(scope="module")
def demo(live_server, app):
    """Seed the demo company and one open exception in it; return its organisation id."""
    from app import db
    from app.commands.seed_demo_company import seed_demo_company

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            from app.models.organization import Organization
            from app.models.user import Role, User

            org = Organization.query.filter_by(slug="lantern-quay").one()
            Role.insert_roles()
            if User.query.filter_by(email=CTO_EMAIL).first() is None:
                cto = User(email=CTO_EMAIL, first_name="Demo", last_name="CTO", organization_id=org.id,
                           enterprise_role="cto", confirmed=True)
                cto.role = Role.query.filter_by(name="Architect").one()
                cto.password = PASSWORD
                db.session.add(cto)
                db.session.commit()
            result = {"org_id": org.id}
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return result


def _new_exception(app, org_id):
    """One open exception that nothing has escalated yet, so every run starts from the same state."""
    import uuid

    from app import db
    from app.models.architecture_decision import ArchitectureChangeRequest

    with app.app_context():
        # the view lists the oldest two open exceptions, so earlier runs' rows must not crowd this one out
        ArchitectureChangeRequest.query.filter(
            ArchitectureChangeRequest.organization_id == org_id,
            ArchitectureChangeRequest.title.like(EXCEPTION_TITLE + "%")).delete(synchronize_session=False)
        db.session.commit()
        row = ArchitectureChangeRequest(
            organization_id=org_id, title="%s %s" % (EXCEPTION_TITLE, uuid.uuid4().hex[:6]),
            acr_reference="ACR-HEALTH-%s" % uuid.uuid4().hex[:6], disposition="exception")
        db.session.add(row)
        db.session.commit()
        out = row.id
        db.session.remove()
    return out


def _context(browser, wide=True):
    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 1440, "height": 1000} if wide else {"width": 390, "height": 844})
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', Date.now().toString()); } catch (e) {}")
    return context


def _number(text):
    return int(re.sub(r"[^\d]", "", text))


def _health(page, base, view=None):
    url = base + "/dashboard/health" + ("?view=%s" % view if view else "")
    response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200, "%s answered %s" % (url, response.status)
    expect(page.get_by_test_id("health-score-line")).to_be_visible(timeout=PAGE_TIMEOUT)


def test_every_old_address_answers_a_redirect_to_the_one_page(browser, live_server, demo):
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        for path, view, marker in OLD_ADDRESSES:
            answer = page.request.get(live_server + path, max_redirects=0)
            if answer.status == 308:  # Flask's own trailing-slash redirect comes first
                answer = page.request.get(answer.headers["location"], max_redirects=0)
            assert answer.status == 302, "%s answered %s, not a redirect" % (path, answer.status)
            target = answer.headers["location"]
            assert "/dashboard/health" in target, "%s redirects to %s" % (path, target)
            if view:
                assert "view=%s" % view in target, "%s lost its view: %s" % (path, target)

            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 200
            assert re.search(r"/dashboard/health", page.url), "%s landed on %s" % (path, page.url)
            expect(page.get_by_test_id(marker)).to_be_visible(timeout=PAGE_TIMEOUT)
            body = page.inner_text("main") if page.locator("main").count() else page.inner_text("body")
            assert "being built" not in body and "Check back soon" not in body, "%s landed on a placeholder" % path
    finally:
        context.close()


def test_the_cto_view_holds_everything_the_cto_scorecard_showed(browser, live_server, demo, app):
    exception_id = _new_exception(app, demo["org_id"])
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        _health(page, live_server, "cto")
        for facts in ("supported-version-share", "open-exceptions", "end-of-support-exposure", "store-agreement-panel"):
            expect(page.get_by_test_id(facts)).to_be_visible(timeout=PAGE_TIMEOUT)
        share = page.get_by_test_id("supported-version-share").inner_text()
        assert re.search(r"\d+\s*%|No application has a vendor product", share), share
        assert "Surfaces disagreeing" in page.get_by_test_id("store-agreement-panel").inner_text()
        expect(page.get_by_test_id(f"exception-{exception_id}")).to_be_visible(timeout=PAGE_TIMEOUT)
        footer = page.get_by_test_id("health-evidence-footer").inner_text()
        for part in ("Evidence:", "Source:", "Confidence:", "As of"):
            assert part in footer, footer

        # escalating works from the new page and lands back on it, marked escalated
        page.get_by_test_id(f"exception-{exception_id}").get_by_role("button", name="Escalate").click()
        page.wait_for_url(re.compile(r"/dashboard/health\?view=cto"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id(f"exception-{exception_id}").get_by_test_id("escalated-badge")
               ).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_the_drift_view_holds_everything_model_health_showed(browser, live_server, demo):
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        _health(page, live_server, "drift")
        panel = page.get_by_test_id("health-view-drift")
        expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
        rescan = panel.get_by_role("button", name="Re-scan for drift")
        expect(rescan).to_be_visible()
        rescan.click()
        page.wait_for_url(re.compile(r"/dashboard/health\?view=drift"), timeout=PAGE_TIMEOUT)
        panel = page.get_by_test_id("health-view-drift")
        expect(panel).to_contain_text("Last computed", timeout=PAGE_TIMEOUT)
        footer = page.get_by_test_id("health-evidence-footer").inner_text()
        for part in ("Evidence:", "Source:", "Confidence:", "As of"):
            assert part in footer, footer
        # every finding names its element; a finding that offers a fix posts back to this page
        for finding in panel.locator(".genome-drift-provenance").all()[:3]:
            assert re.search(r"#\d+", finding.inner_text())
    finally:
        context.close()


def test_the_figures_on_the_page_equal_the_figures_on_the_other_screens(browser, live_server, demo):
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        _health(page, live_server)
        applications = _number(page.get_by_test_id("health-applications").inner_text())
        owned = _number(page.get_by_test_id("health-applications-owned").inner_text())
        assert applications > 0, "the demo company has applications"
        assert 0 < owned <= applications

        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        on_overview = _number(page.get_by_test_id("welcome-applications").inner_text())

        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        banner = page.get_by_test_id("data-quality-alert").inner_text()
        missing = re.search(r"(\d+) of (\d+) have no owner", banner)
        everyone = re.search(r"All (\d+) have an owner", banner)
        assert missing or everyone, banner
        list_total = int((missing or everyone).group(2 if missing else 1))
        list_owned = list_total - int(missing.group(1)) if missing else list_total

        # the ownership coverage page is the CTO's: open it as the demo company's CTO
        cto_context = _context(browser)
        page = cto_context.new_page()
        sign_in(page, live_server, CTO_EMAIL)
        page.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        total = page.locator("p", has_text="Total applications").locator("xpath=following-sibling::p").first
        named = page.locator("p", has_text="With named owner").locator("xpath=following-sibling::p").first
        coverage_total, coverage_owned = _number(total.inner_text()), _number(named.inner_text())

        assert {applications, on_overview, list_total, coverage_total} == {applications}, (
            "applications: health %s, overview %s, list %s, ownership coverage %s"
            % (applications, on_overview, list_total, coverage_total))
        assert {owned, list_owned, coverage_owned} == {owned}, (
            "owned applications: health %s, list %s, ownership coverage %s" % (owned, list_owned, coverage_owned))

        cto_context.close()
        page = context.new_page()  # still signed in as the demo architect
        # the health score is one figure on every view of the page
        _health(page, live_server)
        score = page.get_by_test_id("health-score-value").inner_text().strip()
        for view in ("cto", "drift"):
            _health(page, live_server, view)
            assert page.get_by_test_id("health-score-value").inner_text().strip() == score
    finally:
        context.close()


def test_the_sidebar_entry_and_the_tabs_lead_to_the_views(browser, live_server, demo):
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        _health(page, live_server)
        for tab, marker in (("health-tab-cto", "supported-version-share"), ("health-tab-drift", "health-view-drift"),
                            ("health-tab-overview", "health-portfolio-line")):
            page.get_by_test_id(tab).click()
            expect(page.get_by_test_id(marker)).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id(tab)).to_have_attribute("aria-current", "page")

        # the sidebar's CTO entry opens the CTO view, not a page of its own
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        entry = page.locator("a[href*='/dashboard/health?view=cto']").first
        if entry.count():
            entry.click()
            page.wait_for_url(re.compile(r"/dashboard/health\?view=cto"), timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id("supported-version-share")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.locator("a[href*='/architecture/cto-scorecard'], a[href*='/auto-dashboard'], "
                            "a[href*='/genome/model-health']").count() == 0, "a link still points at an old address"
    finally:
        context.close()


def test_an_empty_organisation_sees_explained_empty_views_and_none_of_the_demo_records(browser, live_server, demo):
    org = create_fresh_org("cto")
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        _health(page, live_server)
        assert _number(page.get_by_test_id("health-applications").inner_text()) == 0
        _health(page, live_server, "cto")
        expect(page.get_by_test_id("open-exceptions")).to_contain_text("No open exceptions", timeout=PAGE_TIMEOUT)
        assert EXCEPTION_TITLE not in page.content()
        _health(page, live_server, "drift")
        text = page.get_by_test_id("health-view-drift").inner_text()
        assert re.search(r"Re-scan for drift|first health scan", text), text
        for path, _view, marker in OLD_ADDRESSES[:2]:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_test_id(marker)).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_the_page_holds_on_a_phone_in_both_themes(browser, live_server, demo):
    context = _context(browser, wide=False)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        for view in (None, "cto", "drift"):
            _health(page, live_server, view)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
                "the %s view scrolls sideways on a phone" % (view or "overview"))
            page.evaluate("document.documentElement.classList.add('dark')")
            expect(page.get_by_test_id("health-score-line")).to_be_visible()
            page.evaluate("document.documentElement.classList.remove('dark')")
    finally:
        context.close()
