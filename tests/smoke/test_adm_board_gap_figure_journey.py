"""The change board's gap figures are the recorded gaps, the same as the register.

An enterprise architect opens Changes > Gaps in an organisation with three recorded
gaps (one of them with a work package) and an application that maps to no capability.
Total gaps is the number of recorded gaps, the register shows the same number, Covered
and Unclosed add up to it, and the computed signal is labelled as a signal and kept
out of the total. A second organisation with one gap reads its own figure.
"""
import re

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BOARD_PATH = "/adm-kanban/gap-analysis"
REGISTER_PATH = "/enterprise/implementation/gap-analysis"


def _org_with_gaps(names, covered_name=None, with_signal=True):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.services import work_package_service
    from app.services.gap_register_service import create_gap

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        gaps = {}
        for name in names:
            gap, _ = create_gap(org["org_id"], "%s %s" % (name, org["suffix"]))
            gaps[name] = gap
        if covered_name:
            package = work_package_service.create_work_package(
                organization_id=org["org_id"], name="Close %s" % covered_name, status="in_progress")
            work_package_service.link_gap(package.id, gaps[covered_name].id, organization_id=org["org_id"])
        if with_signal:
            db.session.add(ApplicationComponent(
                name="Unmapped App %s" % org["suffix"], organization_id=org["org_id"],
                lifecycle_status="operational", description="Mapped to no capability."))
        db.session.commit()
        db.session.remove()
    return org


def _figure(page, testid):
    return int(re.search(r"\d+", page.locator('[data-testid="%s"]' % testid).inner_text()).group(0))


def _frame(page, name, viewport):
    import os

    out = os.environ.get("GAP_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/%s-%d.png" % (out, name, viewport["width"]), full_page=True)


def _open(page, base, path, ready):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_the_board_total_is_the_recorded_gap_count(browser, live_server, viewport):
    mine = _org_with_gaps(["Slow onboarding", "Manual billing", "No audit trail"], covered_name="Manual billing")
    theirs = _org_with_gaps(["Their only gap"], with_signal=False)

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        _open(page, live_server, REGISTER_PATH, "[data-testid='gap-stat-recorded']")
        recorded = _figure(page, "gap-stat-recorded")
        assert recorded == 3

        _open(page, live_server, BOARD_PATH, "[data-testid='gap-total']")
        total, unclosed, covered = (_figure(page, t) for t in ("gap-total", "gap-unclosed", "gap-covered"))
        assert total == recorded == 3
        assert (unclosed, covered) == (2, 1)
        assert total == unclosed + covered

        # The empty table does not deny the recorded gaps above it.
        table_text = page.locator('[data-testid="gaps-table"]').inner_text()
        assert "No gap record has been made yet" not in table_text
        assert "3 recorded gaps are in the gap register" in table_text
        _frame(page, "board-gaps", viewport)
        # The computed signal is named as one and is not part of the total.
        signal = page.locator('[data-testid="gap-signals"]')
        assert signal.count() == 1
        assert re.search(r"signal", page.locator("main").inner_text(), re.I)
        board_signals = _figure(page, "gap-signals")
        assert board_signals >= 1
        assert "text-destructive" in page.locator('[data-testid="gap-unclosed"]').get_attribute("class")

        for name, path, ready in (
            ("roadmap", "/capability-roadmap", "[data-testid='rdm-recorded-gaps']"),
            ("capability-health", "/strategic/capability-health", "[data-testid='health-recorded-gaps']"),
        ):
            _open(page, live_server, path, ready)
            page.wait_for_load_state("networkidle")
            assert _figure(page, ready.split("'")[1]) == 3
            if name == "roadmap":
                # The not-recorded signals are the board's figure, to the digit.
                assert _figure(page, "rdm-gap-signals") == board_signals
            else:
                page.get_by_test_id("health-gap-register-link").click()
                page.wait_for_selector("[data-testid='gap-row']", timeout=PAGE_TIMEOUT)
                assert page.locator("[data-testid='gap-row']").count() == 3
            _frame(page, name, viewport)
        _open(page, live_server, BOARD_PATH, "[data-testid='gap-total']")

        # The register page, reached from the board, lists the gaps the total counts.
        page.get_by_role("link", name=re.compile("Gap register", re.I)).first.click()
        page.wait_for_selector("[data-testid='gap-row']", timeout=PAGE_TIMEOUT)
        landing = page.locator("main").inner_text()
        assert page.locator("[data-testid='gap-row']").count() == total
        for name in ("Slow onboarding", "Manual billing", "No audit trail"):
            assert name in landing
    finally:
        context.close()

    other = browser.new_context(ignore_https_errors=True, viewport=viewport)
    p2 = other.new_page()
    try:
        sign_in(p2, live_server, theirs["emails"]["enterprise_architect"])
        _open(p2, live_server, BOARD_PATH, "[data-testid='gap-total']")
        assert _figure(p2, "gap-total") == 1
        assert _figure(p2, "gap-covered") == 0
        assert "Slow onboarding" not in p2.content()
    finally:
        other.close()
