"""The health-check report: what a buyer sees before buying, and what an architect produces.

Journey 1, a prospective buyer (a head of architecture, not signed in): reads the health-check
page, clicks "See a sample report", finds the answer first and each of the six promised sections
with its recommendation, checks that the figures in the summary agree with the tables under it,
downloads the PDF and finds the same sections, and clicks "Request the health check" to land
back on the page that sells it.

Journey 2, an enterprise architect (never an administrator): loads the sample company from the
first screen, runs the report command for their organisation, and finds every promised section
in the PDF, with the application count the first screen shows.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("HC_WALK_DIR")

SECTIONS = [
    "Application inventory",
    "What breaks if a critical system fails",
    "Duplicate and overlapping applications",
    "Applications with no recorded owner",
    "What to do with each application",
    "Renewals in the next quarter",
]


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name), full_page=True)


def _words(text):
    return re.sub(r"\s+", " ", text)


def test_a_buyer_reads_the_sample_report_and_every_link_lands_on_content(browser, live_server):
    for width, height, tag in ((1440, 900, "1440"), (390, 844, "390")):
        context = browser.new_context(viewport={"width": width, "height": height})
        context.set_default_timeout(PAGE_TIMEOUT)
        page = context.new_page()
        try:
            page.goto(live_server + "/architecture-health-check", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            _shot(page, "health-check-page-%s.png" % tag)
            page.get_by_role("link", name="See a sample report").first.click()
            page.wait_for_url("**/architecture-health-check/sample", timeout=PAGE_TIMEOUT)
            report = page.get_by_test_id("health-check-report")
            expect(report).to_be_visible()

            # Answer first: the short answer comes before section 1, and says how many applications.
            summary = page.get_by_test_id("hc-summary")
            assert report.locator("section").first.get_attribute("data-testid") == "hc-summary"
            summary_text = _words(summary.inner_text())
            total = int(re.search(r"(\d+) applications are recorded", summary_text).group(1))
            assert total >= 8

            # Every promised section is present, says what it shows, and ends with a recommendation.
            for index, title in enumerate(SECTIONS, start=1):
                heading = report.get_by_role("heading", name="%d. %s" % (index, title))
                expect(heading).to_be_visible()
            for name in ("inventory", "impact", "duplicates", "unowned", "scores", "renewals"):
                section = page.get_by_test_id("hc-" + name)
                assert section.locator("p.text-muted-foreground").first.inner_text().strip(), name
                recommendation = section.get_by_test_id("hc-%s-recommendation" % name).inner_text()
                assert recommendation.startswith("Recommendation: ") and len(recommendation) > 40, name

            # Figures agree with each other: the summary's count is the inventory's, and the rows listed.
            assert _words(page.get_by_test_id("hc-inventory-total").inner_text()).startswith("%d applications" % total)
            inventory_rows = page.get_by_test_id("hc-inventory").locator("tbody").nth(1).locator("tr").count()
            assert inventory_rows == total
            unowned_text = page.get_by_test_id("hc-unowned-count").inner_text()
            assert re.search(r"of %d applications" % total, unowned_text), unowned_text
            assert re.search(r"of %d applications have enough evidence" % total,
                             page.get_by_test_id("hc-scores-count").inner_text())

            # A gap reads as a gap, and a section with no data says what to do next.
            body = _words(report.inner_text())
            assert "Not recorded" in body
            assert re.search(r"(None|null|undefined|NaN)\b", body) is None
            assert page.get_by_test_id("hc-renewals-recommendation").inner_text().strip().endswith(".")
            _shot(page, "sample-report-%s.png" % tag)

            # The PDF link lands on a PDF with the same sections.
            href = page.get_by_test_id("hc-sample-pdf").get_attribute("href")
            response = context.request.get(live_server + href)
            assert response.status == 200 and response.body().startswith(b"%PDF")
            from io import BytesIO

            from pypdf import PdfReader

            text = _words("\n".join(p.extract_text() for p in PdfReader(BytesIO(response.body())).pages))
            for index, title in enumerate(SECTIONS, start=1):
                assert "%d. %s" % (index, title) in text, title
            assert "%d applications are recorded" % total in text

            # "Request the health check" lands on the page that sells it, at the request form.
            page.get_by_test_id("hc-sample-request").click()
            page.wait_for_url("**/architecture-health-check#request-the-health-check", timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", name="Request the health check")).to_be_visible()
            expect(page.get_by_role("heading", name="Architecture health check: a fixed-price enterprise architecture assessment")).to_be_visible()
        finally:
            context.close()


def _architect(live_server):
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="Health check %s" % suffix, slug="hc-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        user = User(email="ea-%s@example.com" % suffix, first_name="Enterprise", last_name="Architect",
                    organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return user.email, user.id, org.id


def test_an_architect_loads_the_sample_runs_the_command_and_finds_every_section_in_the_pdf(browser, live_server, tmp_path):
    email, user_id, org_id = _architect(live_server)

    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.user import User

        signed_in = db.session.get(User, user_id)
        # The persona is the enterprise architect, not an administrator or a platform role.
        assert signed_in.enterprise_role == "enterprise_architect"
        assert not signed_in.is_org_admin and not signed_in.is_platform_admin

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", email)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("first-action-sample").click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        shown = int(page.locator('[data-testid="health-kpi-applications"]').inner_text().strip())
    finally:
        context.close()

    with app.app_context():
        result = app.test_cli_runner().invoke(args=["health-check-report", "--org", str(org_id), "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output

    from pypdf import PdfReader

    pdf = tmp_path / "architecture-health-check.pdf"
    assert (tmp_path / "architecture-health-check.html").is_file() and pdf.is_file()
    text = _words("\n".join(p.extract_text() for p in PdfReader(str(pdf)).pages))
    for index, title in enumerate(SECTIONS, start=1):
        assert "%d. %s" % (index, title) in text, title
    # The report's count is the count the first screen showed.
    assert "%d applications are recorded" % shown in text
    assert re.search(r"(None|null|undefined|NaN)\b", text) is None
