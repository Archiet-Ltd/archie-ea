"""Security architect journey: record a finding, follow it to a passing re-test, publish it.

Clicks the real controls and reloads after each step, so what is asserted is what
was saved, not what the page echoed.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login, _visit
from .test_archetype_journeys import page  # noqa: F401  (shared browser context fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TRACKER = "/trust-centre/security-findings/"



@pytest.fixture
def _remove_created_findings(app):
    """Findings are platform-level and the live server commits them to the shared
    database, so remove what this journey created, whatever its outcome."""
    created = []
    yield created
    from sqlalchemy import text

    from app import db

    with app.app_context():
        with db.engine.begin() as conn:
            for title in created:
                conn.execute(text("DELETE FROM security_findings WHERE title = :t"), {"t": title})


def test_security_architect_takes_a_finding_from_record_to_published_summary(  # noqa: F811
    page, live_server, seeded, _remove_created_findings
):
    title = "Session cookie missing Secure flag %s" % uuid.uuid4().hex[:8]
    _remove_created_findings.append(title)
    _login(page, live_server, seeded["emails"]["security_architect"])
    _visit(page, live_server, TRACKER)

    assert page.get_by_test_id("finding-status-summary").count() == 1

    page.fill("#finding-title", title)
    page.select_option("#finding-severity", "medium")
    page.select_option("#finding-source", "manual_checklist")
    page.fill("#finding-reference", "ASVS 3.4.1")
    page.fill("#finding-discovered", "2026-09-01")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id("record-finding-submit").click()

    _visit(page, live_server, TRACKER)  # reload
    item = page.locator("li[data-testid^='finding-']", has_text=title)
    assert item.count() == 1
    finding_id = item.get_attribute("data-testid").split("-")[-1]
    assert page.get_by_test_id(f"finding-status-{finding_id}").inner_text().strip() == "Open"

    page.fill(f"#fix-{finding_id}", "https://github.com/example/repo/pull/42")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id(f"link-fix-{finding_id}").click()
    page.fill(f"#schedule-{finding_id}", "2026-10-15")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id(f"schedule-retest-{finding_id}").click()

    _visit(page, live_server, TRACKER)  # reload
    assert "pull/42" in page.get_by_test_id(f"finding-fix-{finding_id}").inner_text()
    assert "2026-10-15" in page.get_by_test_id(f"finding-scheduled-{finding_id}").inner_text()
    assert page.get_by_test_id(f"finding-unretested-{finding_id}").count() == 1
    assert page.get_by_test_id("unretested-flag").count() == 1

    page.fill(f"#retest-date-{finding_id}", "2026-10-15")
    page.select_option(f"#retest-result-{finding_id}", "passed")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id(f"record-retest-{finding_id}").click()

    _visit(page, live_server, TRACKER)  # reload
    assert "Closed" in page.get_by_test_id(f"finding-status-{finding_id}").inner_text()
    assert page.get_by_test_id(f"finding-unretested-{finding_id}").count() == 0

    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_test_id(f"publish-{finding_id}").click()
    _visit(page, live_server, TRACKER)  # reload
    assert page.get_by_test_id(f"finding-published-{finding_id}").inner_text().strip() == "Published"

    _visit(page, live_server, "/trust-centre/closed-findings")
    listed = page.get_by_test_id(f"closed-finding-{finding_id}")
    assert listed.count() == 1 and title in listed.inner_text()
    assert "Manual checklist" in page.get_by_test_id("closed-by-source").inner_text()
