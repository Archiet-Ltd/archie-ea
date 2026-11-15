"""Every gap figure says what it counts, on every view and on a phone.

An enterprise architect opens the Gaps page filtered to critical gaps and reads the line about
gaps without a work package: it counts the whole register, so it must not say "these gaps" under
a list that shows only some of them. In an organisation where no gap is rated, the severity cards
are replaced by the page's sentence instead of four zeros that read "Open high" beside rows
marked High. On the change board at 390 wide, the covered figure names open gaps in its visible
label, so it does not read as a disagreement with the Gaps page. A second organisation holds
gaps of its own, so a figure that leaked between organisations would change.
"""
import os
import re

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

REGISTER_PATH = "/enterprise/implementation/gap-analysis"
BOARD_PATH = "/adm-kanban/gap-analysis"


def _org(specs, covered=()):
    """An organisation holding gaps (name, severity); the names in `covered` get a work package."""
    from app import create_app
    from app import db
    from app.services import work_package_service
    from app.services.gap_register_service import create_gap

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        made = {}
        for name, severity in specs:
            gap, _ = create_gap(org["org_id"], "%s %s" % (name, org["suffix"]),
                                priority=severity, severity=severity)
            made[name] = gap
            if severity is None:
                # the columns default to "medium" on insert; this gap is stored unrated
                gap.priority = gap.severity = gap.impact = None
        db.session.flush()
        for name in covered:
            package = work_package_service.create_work_package(
                organization_id=org["org_id"], name="Close %s" % name, status="in_progress")
            work_package_service.link_gap(package.id, made[name].id, organization_id=org["org_id"])
        db.session.commit()
        db.session.remove()
    return org


def _open(page, base, path, ready):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(ready, timeout=PAGE_TIMEOUT)


def _shot(page, name):
    out = os.environ.get("GAP_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/%s.png" % (out, name), full_page=True)


def test_gap_figures_say_what_they_count(browser, live_server):
    rated = _org([("Slow onboarding", "critical"), ("Manual billing", "critical"),
                  ("No audit trail", "low")], covered=("Manual billing",))
    unrated = _org([("Unrated one", None), ("Unrated two", None)])
    other = _org([("Their gap", "critical")])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, live_server, rated["emails"]["enterprise_architect"])
        _open(page, live_server, REGISTER_PATH + "?severity=critical", "[data-testid='gap-unclosed-count-line']")
        line = page.locator("[data-testid='gap-unclosed-count-line']").inner_text()
        text = page.locator("main").inner_text()
        assert page.locator("[data-testid='gap-row']").count() == 2
        assert "these gaps" not in text.lower(), "a sentence points at rows that are not in view"
        assert re.search(r"across all gaps", line, re.I), line
        assert page.locator("[data-testid='gap-unclosed-count']").inner_text().strip() == "2"
        # severity cards say they count severity
        assert "Open, critical severity" in text
        _shot(page, "register-critical-1440")

        # the board's covered figure, at phone width, names open gaps
        page.set_viewport_size({"width": 390, "height": 844})
        _open(page, live_server, BOARD_PATH, "[data-testid='gap-covered']")
        covered = page.locator("[data-testid='gap-covered']").locator("xpath=..")
        assert covered.inner_text().strip().split("\n")[0] == "1"
        assert re.search(r"open", covered.inner_text(), re.I), covered.inner_text()
        assert page.locator("[data-testid='gap-covered']").is_visible()
        _shot(page, "board-390")
        page.get_by_text(re.compile("open, covered", re.I)).first.click()
        page.wait_for_selector("[data-testid='gap-row']", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-testid='gap-row']").count() == 1
    finally:
        context.close()

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        sign_in(page, live_server, unrated["emails"]["enterprise_architect"])
        _open(page, live_server, REGISTER_PATH, "[data-testid='gap-row']")
        text = page.locator("main").inner_text()
        assert not re.search(r"Open (critical|high|medium|low)\b(?! severity)", text), \
            "a card reads Open <level> without the word severity"
        assert page.locator("[data-testid='gap-open-high']").count() == 0
        assert re.search(r"no gap has a severity yet", text, re.I)
        _shot(page, "register-unrated-390")
    finally:
        context.close()

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, live_server, other["emails"]["enterprise_architect"])
        _open(page, live_server, REGISTER_PATH, "[data-testid='gap-row']")
        assert "Slow onboarding" not in page.content()
        assert page.locator("[data-testid='gap-open-critical']").count() == 1
    finally:
        context.close()
