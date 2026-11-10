"""An architect opens the pages an inventory sweep flagged and each one is whole.

In a new organisation with a capability that has no importance rating, a
two-level capability tree, and twenty-two applications (one past its end of life, the rest with no
lifecycle data), the architect:

* opens Capability Health and reads a plain "Not rated", never the word None;
* follows the two old dashboard addresses (impact analysis, gap analysis) and
  lands on the live page each name promises;
* opens the vendor mapping tool and its back link lands on the vendor list;
* opens the element list, runs Tech Debt Analysis and reads each application by
  name: the one past its end of life with its real factors, applications with all,
  some or an unreadable part of their inputs recorded each saying which inputs the
  score came from, the others "Not rated" with the reason, and a way to see the
  rows beyond the first twenty; an organisation with no applications reads why the
  panel is empty and follows the link to add some;
* opens the capability tree, clicks "All" and sees every capability drawn, top level
  included, with a count that agrees, and no policy error;
* opens the connectors page and gets a page, not a server error;
* asks for the application patterns and gets an answer in under five seconds.

Each page is walked at 1440 wide in light and 390 wide in dark; no console
error or uncaught script error is allowed on any of them.
"""

import os
import time
from datetime import date

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = (("light", 1440, 900), ("dark", 390, 844))
PATTERNS_BUDGET_SECONDS = 5


def _seed(org_id, tag):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        db.session.add(BusinessCapability(name="Asset Lifecycle %s" % tag, level=1, organization_id=org_id))
        root = UnifiedCapability(name="Tree root %s" % tag, code="DE-R-%s" % tag[:4], level=1, organization_id=org_id)
        db.session.add(root)
        db.session.flush()
        db.session.add(UnifiedCapability(name="Tree leaf %s" % tag, code="DE-L-%s" % tag[:4], level=2,
                                         parent_capability_id=root.id, organization_id=org_id))
        db.session.add(ApplicationComponent(name="Legacy billing %s" % tag, organization_id=org_id,
                                            end_of_life_date=date(2020, 1, 1), version="5.1"))
        later = date(2040, 1, 1)
        db.session.add(ApplicationComponent(name="Debt all three %s" % tag, organization_id=org_id,
                                            end_of_life_date=later, version="10.2", support_level="supported"))
        db.session.add(ApplicationComponent(name="Debt one missing %s" % tag, organization_id=org_id,
                                            end_of_life_date=later, version="10.2"))
        db.session.add(ApplicationComponent(name="Debt two missing %s" % tag, organization_id=org_id,
                                            end_of_life_date=later))
        db.session.add(ApplicationComponent(name="Debt unreadable %s" % tag, organization_id=org_id,
                                            end_of_life_date=later, version="R12", support_level="supported"))
        for number in range(17):
            db.session.add(ApplicationComponent(name="Dead end app %d %s" % (number, tag), organization_id=org_id))
        db.session.commit()
        db.session.remove()


def _seed_other(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        db.session.add(ApplicationComponent(name="Other org ledger", organization_id=org_id,
                                            end_of_life_date=date(2020, 1, 1), version="1.0"))
        db.session.commit()
        db.session.remove()


def _walk_dir(tmp_path_factory):
    return os.environ.get("DEADEND_WALK_DIR") or str(tmp_path_factory.mktemp("deadend-walk"))


def test_swept_pages_are_whole(browser, live_server, tmp_path_factory):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    tag = org["suffix"]
    _seed(org["org_id"], tag)
    other = create_fresh_org("enterprise_architect", org_admin=True)
    _seed_other(other["org_id"])
    walk = _walk_dir(tmp_path_factory)
    os.makedirs(walk, exist_ok=True)

    for scheme, width, height in VIEWPORTS:
        context = browser.new_context(viewport={"width": width, "height": height},
                                      color_scheme=scheme)
        page = context.new_page()
        problems = []
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        label = "%s%d" % (scheme, width)

        def goto(path):
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        def shot(name):
            page.screenshot(path=os.path.join(walk, "%s-%s.png" % (label, name)), full_page=True)

        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])

            goto("/enterprise/strategic/capability-health")
            body = page.locator("main").inner_text()
            assert "Asset Lifecycle %s" % tag in body
            assert "Not Rated" in body
            assert "None" not in body
            shot("capability-health")

            goto("/dashboard/impact-analysis")
            assert page.url.endswith("/enterprise/analysis/impact-analysis"), page.url
            assert page.locator("main").inner_text().strip()
            assert "Applications Portfolio Dashboard" not in page.content()
            shot("impact-analysis")

            goto("/dashboard/portfolio/gap-analysis")
            assert page.url.endswith("/enterprise/implementation/gap-analysis"), page.url
            assert "Gap" in page.locator("main").inner_text()
            assert "Applications Portfolio Dashboard" not in page.content()
            shot("gap-analysis")

            goto("/vendors/integration/mapping")
            back = page.get_by_role("link", name="Back to Vendors")
            assert back.count() == 1
            back.click()
            page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
            assert page.url.rstrip("/").endswith("/vendors"), page.url
            assert page.locator("main").inner_text().strip()
            shot("vendors")

            goto("/architecture/elements")
            page.get_by_role("button", name="Tech Debt Analysis").click()
            page.get_by_text("Technology Debt Scores").wait_for(timeout=PAGE_TIMEOUT)
            page.get_by_text("Showing 20 of 22 applications analysed").wait_for(timeout=PAGE_TIMEOUT)
            debt = page.locator("table.dense-table", has_text="Debt Score")
            legacy = debt.locator("tr", has_text="Legacy billing %s" % tag)
            assert legacy.count() == 1
            assert legacy.locator("td").nth(2).inner_text().strip() == "100%"
            # What each row says is read as visible text at both widths (the reason sits under the name
            # on the narrow screen), and it never claims an input is in good standing unless it was read.
            legacy_text = legacy.inner_text()
            assert "End of life has passed" in legacy_text
            assert "Major version 5 (version 7 or lower counts as old)" in legacy_text
            assert "Old major version" not in legacy_text
            assert "Scored from the end-of-life date and version only; support status not recorded" in legacy_text

            def row_text(name):
                row = debt.locator("tr", has_text="%s %s" % (name, tag))
                assert row.count() == 1, name
                return row.inner_text()

            full = row_text("Debt all three")
            assert "0%" in full and "all in good standing" in full
            assert "Scored from" not in full
            one_missing = row_text("Debt one missing")
            assert "0%" in one_missing
            assert "Scored from the end-of-life date and version only; support status not recorded" in one_missing
            assert "good standing" not in one_missing
            two_missing = row_text("Debt two missing")
            assert "0%" in two_missing
            assert "Scored from the end-of-life date only; version and support status not recorded" in two_missing
            assert "good standing" not in two_missing
            unreadable = row_text("Debt unreadable")
            assert "0%" in unreadable
            assert "Scored from the end-of-life date and support status only; version could not be read" in unreadable
            assert "good standing" not in unreadable
            assert "Other org ledger" not in debt.inner_text()
            assert "No debt factors recorded" not in debt.inner_text()
            unrated = debt.locator("tr", has_text="Dead end app 0 %s" % tag)
            assert unrated.count() == 0 or "Not rated" in unrated.inner_text()
            page.get_by_test_id("debt-show-all").click()
            page.get_by_text("Showing 22 of 22 applications analysed").wait_for(timeout=PAGE_TIMEOUT)
            unrated = debt.locator("tr", has_text="Dead end app 3 %s" % tag)
            assert unrated.count() == 1
            text = unrated.inner_text()
            assert "Not rated" in text and "0%" not in text.replace("100%", "")
            assert "no end-of-life date, version, support status recorded" in text
            assert page.get_by_text("Could not load debt scores").count() == 0
            shot("tech-debt")

            goto("/capability-map/tree/capability")
            page.get_by_role("button", name="All", exact=True).click()
            page.wait_for_timeout(500)
            drawn = page.locator("#tree-container").inner_text()
            assert "Tree leaf %s" % tag in drawn
            assert "Tree root %s" % tag in drawn, "All must draw the top level too: %r" % drawn
            assert "Asset Lifecycle %s" % tag in drawn, "All must draw every top-level capability: %r" % drawn
            # The count line agrees with what is drawn: three capabilities (two top level, one below), none left out.
            assert page.get_by_text("3 of 3 shown", exact=True).count() == 1, page.locator("main").inner_text()[:400]
            shot("capability-tree")

            response = page.goto(live_server + "/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 200, response.status
            assert page.locator("main").inner_text().strip()
            shot("connectors")

            started = time.monotonic()
            status, payload = api(page, "GET", "/api/ea-workflows/sa/application-patterns")
            elapsed = time.monotonic() - started
            assert status == 200, (status, payload)
            assert payload["patterns"]["classified"] == 22, payload
            assert elapsed < PATTERNS_BUDGET_SECONDS, "patterns took %.1fs" % elapsed

            assert problems == [], problems
        finally:
            context.close()


def test_tech_debt_panel_for_an_organisation_with_no_applications_says_why_and_where_next(
    browser, live_server, tmp_path_factory
):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    walk = _walk_dir(tmp_path_factory)
    os.makedirs(walk, exist_ok=True)
    for scheme, width, height in VIEWPORTS:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            page.get_by_role("button", name="Tech Debt Analysis").click()
            empty = page.get_by_test_id("debt-empty")
            empty.wait_for(timeout=PAGE_TIMEOUT)
            text = empty.inner_text()
            assert "No applications to score yet" in text
            assert "no applications yet" in text and "end-of-life date, version and support status" in text
            assert "No debt scores available" not in page.content()
            page.screenshot(path=os.path.join(walk, "%s%d-tech-debt-empty.png" % (scheme, width)), full_page=True)
            empty.get_by_role("link", name="Add or import applications").click()
            page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
            assert "/applications" in page.url, page.url
            assert page.locator("main").inner_text().strip()
        finally:
            context.close()
