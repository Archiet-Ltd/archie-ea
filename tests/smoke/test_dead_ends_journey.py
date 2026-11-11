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
from datetime import date

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = (("light", 1440, 900), ("dark", 390, 844))
ALL_VIEWPORTS = (("light", 1440, 900), ("dark", 1440, 900), ("light", 390, 844), ("dark", 390, 844))


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

            status, payload = api(page, "GET", "/api/ea-workflows/sa/application-patterns")
            assert status == 200, (status, payload)
            assert payload["patterns"]["classified"] == 22, payload
            # The time budget is measured on an injected clock in tests/test_dead_end_redirects_and_patterns.py
            # (the server here is a separate process); here the answer must not report being cut short.
            assert not payload["patterns"].get("llm_truncated"), payload["patterns"]

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


SUPPORT_JOURNEY_VALUES = (
    ("Supported", "good"),
    ("Unsupported", "Unsupported by the vendor"),
    ("none", "none"),
    ("Not supported", "Unsupported by the vendor"),
    ("End of support", "Unsupported by the vendor"),
    ("TBC", "TBC"),
)


def test_tech_debt_panel_reads_each_recorded_support_value_as_what_it_is(
    browser, live_server, tmp_path_factory
):
    """An architect imports applications with free-text support values, opens Tech Debt and reads
    each row: only a known supported value is good standing; the rest are shown as what they are."""
    org = create_fresh_org("enterprise_architect", org_admin=True)
    other = create_fresh_org("enterprise_architect", org_admin=True)
    tag = org["suffix"]
    walk = _walk_dir(tmp_path_factory)
    os.makedirs(walk, exist_ok=True)
    rows = [
        {"name": "Support %s %s" % (value, tag), "end_of_life_date": "2040-01-01", "version": "10.2",
         "support_level": value}
        for value, _ in SUPPORT_JOURNEY_VALUES
    ]
    rows.append({"name": "Support blank %s" % tag, "end_of_life_date": "2040-01-01", "version": "10.2"})
    other_context = browser.new_context()
    try:
        other_page = other_context.new_page()
        sign_in(other_page, live_server, other["emails"]["enterprise_architect"])
        other_page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, payload = api(other_page, "POST", "/applications/import-manual", {
            "applications": [{"name": "Other org TBC %s" % tag, "end_of_life_date": "2040-01-01",
                              "version": "10.2", "support_level": "TBC"}]})
        assert status == 200, (status, payload)
    finally:
        other_context.close()
    for scheme, width, height in VIEWPORTS:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            if scheme == "light":
                status, payload = api(page, "POST", "/applications/import-manual", {"applications": rows})
                assert status == 200, (status, payload)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            page.get_by_role("button", name="Tech Debt Analysis").click()
            page.get_by_text("Technology Debt Scores").wait_for(timeout=PAGE_TIMEOUT)
            page.get_by_text("Showing 7 of 7 applications analysed").wait_for(timeout=PAGE_TIMEOUT)
            debt = page.locator("table.dense-table", has_text="Debt Score")

            def row_text(value):
                row = debt.locator("tr", has_text="Support %s %s" % (value, tag))
                assert row.count() == 1, (value, " ".join(debt.inner_text().split())[:1200])
                return row.inner_text()

            good = row_text("Supported")
            assert "0%" in good and "all in good standing" in good, good
            for value, said in SUPPORT_JOURNEY_VALUES[1:]:
                text = row_text(value)
                assert "good standing" not in text, (value, text)
                if said == "Unsupported by the vendor":
                    assert said in text and "80%" in text, (value, text)
                else:
                    assert "support status not recognised (recorded as “%s”)" % said in text, (value, text)
            blank = row_text("blank")
            assert "good standing" not in blank and "support status not recorded" in blank, blank
            assert "Other org TBC" not in debt.inner_text()
            debt.screenshot(path=os.path.join(walk, "%s%d-tech-debt-support.png" % (scheme, width)))
        finally:
            context.close()


LONG_SUPPORT_VALUE = "UnbrokenSupportTextOfThirtyChs"  # 30 characters, no space to wrap at
LONG_NAME_PREFIX = "UnbrokenApplicationNameWithNoSpaceToWrapAtAll"  # 46 characters, only sideways scroll keeps its score reachable

# Every line of the factors text must end at a space or at the end of the text; the quoted value may break.
WORD_SPLIT_JS = """(el) => {
  const node = el.firstChild;
  const text = node.textContent;
  const quoted = text.indexOf('\u201c');
  const limit = quoted === -1 ? text.length : quoted;
  const range = document.createRange();
  const bad = [];
  let prevTop = null;
  for (let i = 0; i < limit; i++) {
    range.setStart(node, i); range.setEnd(node, i + 1);
    const rects = range.getClientRects();
    if (!rects.length) continue;
    const top = Math.round(rects[0].top);
    if (prevTop !== null && top > prevTop + 3 && text[i] !== ' ' && text[i - 1] !== ' ') {
      bad.push(text.slice(Math.max(0, i - 6), i) + '/' + text.slice(i, i + 6));
    }
    prevTop = top;
  }
  return bad;
}"""


def test_tech_debt_table_keeps_every_debt_score_reachable_with_a_long_support_value(
    browser, live_server, tmp_path_factory
):
    """An architect imports an application whose support value is 30 unbroken characters, next to a fully
    scored 0% application and a partly scored one, and reads the Tech Debt table at both widths."""
    assert len(LONG_SUPPORT_VALUE) == 30
    org = create_fresh_org("enterprise_architect", org_admin=True)
    tag = org["suffix"]
    walk = _walk_dir(tmp_path_factory)
    os.makedirs(walk, exist_ok=True)
    rows = [
        {"name": "Long support %s" % tag, "end_of_life_date": "2040-01-01", "version": "10.2",
         "support_level": LONG_SUPPORT_VALUE},
        {"name": "Fully scored %s" % tag, "end_of_life_date": "2040-01-01", "version": "10.2",
         "support_level": "Supported"},
        {"name": "Partly scored %s" % tag, "end_of_life_date": "2040-01-01", "version": "10.2"},
        {"name": "Old partly %s" % tag, "end_of_life_date": "2010-01-01"},
        {"name": LONG_NAME_PREFIX + tag, "end_of_life_date": "2040-01-01", "version": "10.2",
         "support_level": "Supported"},
    ]
    for scheme, width, height in ALL_VIEWPORTS:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.goto(live_server + "/architecture/elements", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            if (scheme, width) == ("light", 1440):
                status, payload = api(page, "POST", "/applications/import-manual", {"applications": rows})
                assert status == 200, (status, payload)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
            page.get_by_role("button", name="Tech Debt Analysis").click()
            page.get_by_text("Showing 5 of 5 applications analysed").wait_for(timeout=PAGE_TIMEOUT)
            debt = page.locator("table.dense-table", has_text="Debt Score")
            for name in ("Long support", "Fully scored", "Partly scored", "Old partly", LONG_NAME_PREFIX):
                row = debt.locator("tr", has_text=name if name == LONG_NAME_PREFIX else "%s %s" % (name, tag))
                assert row.count() == 1, name
                badge = row.locator("td").nth(2).locator("span[data-partly-scored]")
                badge.scroll_into_view_if_needed()
                box = badge.bounding_box()
                # The score is on screen after scrolling the table sideways, never cut off or gone.
                assert box and box["width"] > 0 and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (name, box)
            body_overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert body_overflow <= 1, "page scrolls sideways by %s px" % body_overflow
            long_row = debt.locator("tr", has_text="Long support %s" % tag)
            compact = long_row.get_by_test_id("debt-detail-compact") if width < 1024 else long_row.get_by_test_id("debt-detail")
            shown = compact.get_by_test_id("debt-detail-text").inner_text()
            assert LONG_SUPPORT_VALUE not in shown, shown
            assert "…" in shown, shown
            assert LONG_SUPPORT_VALUE in compact.get_attribute("title")
            assert compact.get_attribute("aria-label") is None
            # A screen-reader user hears the full value from visually hidden text, not from a title.
            hidden_full = compact.get_by_test_id("debt-detail-full")
            assert "sr-only" in hidden_full.get_attribute("class")
            assert LONG_SUPPORT_VALUE in hidden_full.text_content()
            # No ordinary word of any row's factors text is split across lines.
            for factors in debt.locator("tr").locator(
                    "[data-testid=debt-detail-text]:visible").element_handles():
                assert factors.evaluate(WORD_SPLIT_JS) == [], factors.inner_text()
            # The wrapper scrolls sideways when a row is wider than it, and the score is there after scrolling.
            wrapper = debt.locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]")
            long_name_row = debt.locator("tr", has_text=LONG_NAME_PREFIX)
            long_badge = long_name_row.locator("span[data-partly-scored]")
            if width < 1024:
                dims = wrapper.evaluate("e => [e.scrollWidth, e.clientWidth, getComputedStyle(e).overflowX]")
                assert dims[0] > dims[1] and dims[2] in ("auto", "scroll"), dims
                wrapper.evaluate("e => e.scrollTo({left: e.scrollWidth, behavior: 'instant'})")
                long_badge.scroll_into_view_if_needed()
                assert wrapper.evaluate("e => e.scrollLeft") > 0
                wbox = wrapper.bounding_box()
                bbox = long_badge.bounding_box()
                assert bbox and bbox["x"] >= wbox["x"] - 1 and bbox["x"] + bbox["width"] <= wbox["x"] + wbox["width"] + 1, (bbox, wbox)
                assert bbox["x"] >= 0 and bbox["x"] + bbox["width"] <= width, (bbox, width)
                assert long_badge.inner_text().strip() == "0%"
                # Nothing is drawn over the score: the badge is the top element at its own centre.
                covered_by = page.evaluate(
                    "([x, y]) => { const e = document.elementFromPoint(x, y); return e && e.closest('[data-partly-scored]') ? null : (e && e.textContent.slice(0, 60)); }",
                    [bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2])
                assert covered_by is None, "the Debt Score is covered by: %r" % covered_by
                page.screenshot(path=os.path.join(walk, "%s%d-tech-debt-long-name-scrolled.png" % (scheme, width)), clip=wbox)
                wrapper.evaluate("e => { e.scrollLeft = 0; }")

            def badge_of(name):
                return debt.locator("tr", has_text="%s %s" % (name, tag)).locator("span[data-partly-scored]")

            full = badge_of("Fully scored")
            assert full.inner_text().strip() == "0%" and full.get_attribute("data-partly-scored") == "false"
            assert "bg-success/10" in full.get_attribute("class")
            partly = badge_of("Partly scored")
            assert partly.get_attribute("data-partly-scored") == "true"
            assert "bg-success/10" not in partly.get_attribute("class")
            assert "border-border" in partly.get_attribute("class")
            old = badge_of("Old partly")
            assert old.get_attribute("data-partly-scored") == "true"
            assert old.inner_text().strip() == "100%", old.inner_text()
            assert "bg-destructive/10" in old.get_attribute("class")
            page.screenshot(path=os.path.join(walk, "%s%d-tech-debt-long-support.png" % (scheme, width)), full_page=True)
        finally:
            context.close()
