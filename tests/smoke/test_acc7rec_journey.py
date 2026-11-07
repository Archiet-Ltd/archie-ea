"""Recommendations: a signed-in person reads a real, ranked list drawn from their own model.

One step for each thing that was wrong:
  1. /recommendations shows the person's recommendations (not the public waiting list), each with its reason,
     the record it acts on and an evidence footer, and every record link lands on a page with content.
  2. Architecture Intelligence states the same recommendation count, and the same application total as the
     application list and the overview.
  3. Each severity's "View gaps" link opens the gap register filtered to that severity, with the filter shown.
An organisation with an empty model gets an empty state that says why, and never sees another organisation's records.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
_SHOT_DIR = os.environ.get("JOURNEY_SHOT_DIR")


@pytest.fixture(scope="module")
def demo(live_server, app):
    from app.commands.seed_demo_company import seed_demo_company
    from tests.smoke.conftest import PASSWORD

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            from app import db
            from app.models.organization import Organization

            org = Organization.query.filter_by(slug="lantern-quay").one()
            found = {"org_id": org.id}
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return found


def _context(browser, width=1440, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": 1000 if width > 600 else 900},
                                  color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context


def _shot(page, name):
    if _SHOT_DIR:
        os.makedirs(_SHOT_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(_SHOT_DIR, name + ".png"))


def _number(text):
    return int(re.search(r"\d[\d,]*", text).group(0).replace(",", ""))


def test_recommendations_are_ranked_with_reason_record_and_evidence(browser, live_server, demo):
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body = page.locator("body").inner_text()
        assert "waiting list" not in body.lower(), "a signed-in person was shown the public waiting list"
        rows = page.get_by_test_id("recommendation-row")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        shown = rows.count()
        assert 5 <= shown <= 50, shown

        # the stated count agrees with the rows
        stated = _number(page.get_by_test_id("recommendations-count").inner_text())
        assert stated >= shown

        categories = set()
        for index in range(shown):
            row = rows.nth(index)
            assert row.get_by_test_id("recommendation-title").inner_text().strip()
            assert row.get_by_test_id("recommendation-reason").inner_text().strip()
            footer = row.get_by_test_id("result-footer")
            assert "Evidence" in footer.inner_text() and "Source" in footer.inner_text()
            assert row.get_by_test_id("recommendation-record").get_attribute("href")
            categories.add(row.locator("td").nth(2).inner_text().strip())
        # drawn from more than one part of the model
        assert {"Gap", "Risk", "Cost"} <= categories, categories
        assert len(categories) >= 3, categories
        _shot(page, "recommendations-1440-light")

        # every kind's record link lands on a page with content, not on a list, an error or the waiting list
        seen = {}
        for index in range(shown):
            row = rows.nth(index)
            seen.setdefault(row.locator("td").nth(2).inner_text().strip(), index)
        for category, index in sorted(seen.items()):
            link = rows.nth(index).get_by_test_id("recommendation-record")
            name = link.get_attribute("aria-label").split(": ", 1)[1].strip()
            link.click()
            page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
            landed = page.locator("body").inner_text()
            assert "waiting list" not in landed.lower(), (category, page.url)
            assert "not found" not in page.title().lower(), (category, page.url)
            assert name.split(":")[-1].strip()[:20] in landed, (category, name, page.url)
            page.go_back(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_phone_dark_recommendations_read_without_clipping(browser, live_server, demo):
    context = _context(browser, width=390, scheme="dark")
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.get_by_test_id("recommendation-row").first
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "page scrolls sideways"
        assert row.get_by_test_id("recommendation-reason").is_visible()
        _shot(page, "recommendations-390-dark")
    finally:
        context.close()


def test_architecture_intelligence_agrees_with_the_other_pages(browser, live_server, demo):
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("recommendation-row")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        listed = _number(page.get_by_test_id("recommendations-count").inner_text())

        page.goto(live_server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("ai-analysis-recommendations")
        assert _number(card.inner_text()) == listed, (card.inner_text(), listed)
        assert listed > 0
        _shot(page, "architecture-intelligence-1440-light")

        # the application total it states is the one the application list states
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        found = re.search(r"All (\d+) have an owner|\d+ of (\d+) have no owner", page.locator("body").inner_text())
        listed_applications = int(found.group(1) or found.group(2))
        page.goto(live_server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        held = page.get_by_test_id("ai-analysis-recommendations-link")
        expect(held).to_be_visible()
        held_text = page.locator("body").inner_text()
        stated = int(re.search(r"holds (\d+) application", held_text).group(1))
        assert stated == listed_applications, (stated, listed_applications)
    finally:
        context.close()


def test_view_gaps_opens_the_register_filtered_to_that_severity(browser, live_server, demo):
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        links = {sev: page.get_by_test_id("view-gaps-%s" % sev) for sev in ("critical", "high", "medium", "low")}
        counts = {}
        for sev, link in links.items():
            if link.count():
                counts[sev] = _number(link.locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]").inner_text())
        assert len(counts) >= 2, counts

        for sev, expected in counts.items():
            page.goto(live_server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded",
                      timeout=PAGE_TIMEOUT)
            page.get_by_test_id("view-gaps-%s" % sev).click()
            page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
            assert "severity=%s" % sev in page.url, page.url
            note = page.get_by_test_id("gap-severity-filter")
            expect(note).to_be_visible(timeout=PAGE_TIMEOUT)
            assert sev in note.inner_text().lower()
            gap_links = page.locator("[data-testid^='gap-link-'], [data-testid^='not-addressed-gap-link-']")
            expect(gap_links.first).to_be_visible(timeout=PAGE_TIMEOUT)
            severities = {item.get_attribute("data-severity") for item in page.locator("[data-testid^='gap-link-']").all()}
            assert severities == {sev}, (sev, severities)
            assert page.locator("[data-testid^='gap-link-']").count() == expected, (sev, expected)
            if sev == "high":
                _shot(page, "gaps-filtered-high-1440-light")

        # the filter can be cleared
        page.get_by_test_id("gap-severity-filter-clear").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "severity=" not in page.url
        assert page.locator("[data-testid^='gap-link-']").count() == sum(counts.values()) or \
            page.locator("[data-testid^='gap-link-']").count() >= sum(counts.values())
    finally:
        context.close()


def test_an_empty_model_says_why_and_never_shows_another_organisations_records(browser, live_server, demo):
    fresh = create_fresh_org("enterprise_architect", org_admin=True)
    context = _context(browser)
    page = context.new_page()
    try:
        sign_in(page, live_server, fresh["emails"]["enterprise_architect"])
        page.goto(live_server + "/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        empty = page.get_by_test_id("recommendations-empty")
        expect(empty).to_be_visible(timeout=PAGE_TIMEOUT)
        text = empty.inner_text()
        assert "Nothing to recommend yet" in text and "no records" in text
        assert page.get_by_test_id("recommendation-row").count() == 0
        assert "Lantern Quay" not in page.locator("body").inner_text()
        link = empty.get_by_role("link")
        link.first.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/applications" in page.url, page.url
        _shot(page, "recommendations-empty-1440-light")
    finally:
        context.close()


def test_a_visitor_who_is_not_signed_in_still_gets_the_waiting_list(browser, live_server):
    context = _context(browser)
    page = context.new_page()
    try:
        page.goto(live_server + "/recommendations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "waiting list" in page.locator("body").inner_text().lower()
    finally:
        context.close()
