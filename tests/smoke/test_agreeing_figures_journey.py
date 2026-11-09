"""One number per layer, risk and gap: the same on every screen that states it.

An enterprise architect opens the architecture dashboard and reads each layer tab's
count against the list beneath it, opens the Risk Assessment and reads its figures
against the Risks page, and reads the critical gap figure on the gap analysis page
against the one on the implementation gaps page. A second organisation holds its own
elements, risks and gaps so a leak between organisations would change a number.
"""
import os
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]


def _seed(org_id, noun, *, goals, stray, critical_gaps):
    """Motivation elements (mirrored, unmirrored and one of a type the layer does not hold), risks and gaps."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Plateau
    from app.models.motivation import Goal
    from app.models.risk import Risk, RiskStatus
    from app.services.gap_register_service import create_gap

    with create_app("testing").app_context():
        for index in range(goals):
            db.session.add(ArchiMateElement(
                name="%s architecture goal %d" % (noun, index), type="Goal", layer="Motivation",
                organization_id=org_id))
        for index in range(stray):
            db.session.add(ArchiMateElement(
                name="%s stray node %d" % (noun, index), type="Node", layer="Motivation",
                organization_id=org_id))
        db.session.commit()
        # Goals recorded only in the goal table (no architecture row behind them).
        for index in range(2):
            db.session.add(Goal(name="%s portfolio goal %d" % (noun, index), organization_id=org_id))
        db.session.commit()
        for title, likelihood, impact in ((noun + " severe", 5, 5), (noun + " serious", 4, 4), (noun + " minor", 1, 1)):
            db.session.add(Risk(title=title, description="%s could happen." % title, likelihood=likelihood,
                                impact=impact, status=RiskStatus("open"), owner="Owner",
                                mitigation_plan="Plan.", organization_id=org_id))
        db.session.commit()
        for index in range(critical_gaps):
            create_gap(org_id, "%s critical gap %d" % (noun, index), severity="critical", priority="medium")
        # Rated high on severity but critical on priority: not a critical gap on either page.
        create_gap(org_id, "%s urgent but not critical" % noun, severity="high", priority="critical")
        # A plateau-to-plateau step is not a capability shortfall and is not counted as a gap.
        today = Plateau(name="%s today" % noun, sequence_order=1, organization_id=org_id)
        later = Plateau(name="%s later" % noun, sequence_order=2, organization_id=org_id)
        db.session.add_all([today, later])
        db.session.flush()
        step, _ = create_gap(org_id, "%s plateau step" % noun, severity="critical", priority="critical")
        step.gap_kind = "plateau_transition"
        step.originating_plateau_id, step.target_plateau_id = today.id, later.id
        db.session.commit()
        db.session.remove()


def _db_layer_total(org_id, layer_value):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.motivation import Goal

    with create_app("testing").app_context():
        architecture = ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == org_id,
            ArchiMateElement.type.in_(["Goal", "Assessment"]),  # a risk is mirrored as an assessment
            db.func.lower(ArchiMateElement.layer) == layer_value).count()
        recorded = Goal.query.filter(Goal.organization_id == org_id,
                                     Goal.archimate_element_id.is_(None)).count()
        db.session.remove()
        return architecture + recorded


def _frame(page, name, width, theme):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path="%s/figures-%s-%d-%s.png" % (folder, name, width, theme), full_page=True)


def _number(page, testid):
    figure = page.locator('[data-testid="%s"]' % testid)
    assert figure.count() >= 1, "no %s on %s: %s" % (testid, page.url, page.locator("body").inner_text()[:400])
    return int(re.search(r"\d+", figure.first.inner_text()).group(0))


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_layer_risk_and_gap_figures_agree(browser, live_server, viewport, theme):
    noun = "Fig%s" % uuid.uuid4().hex[:6]
    other = "Rival%s" % uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    _seed(mine["org_id"], noun, goals=3, stray=2, critical_gaps=2)
    _seed(theirs["org_id"], other, goals=7, stray=1, critical_gaps=4)

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    sign_in(page, base, mine["emails"]["enterprise_architect"])
    try:
        # A layer's tab, read from another layer's page (the open tab shows its own list total), against
        # the list beneath that tab and against the database.
        page.goto(base + "/architecture/dashboard?layer=strategy", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        tab = page.locator("#layer-spine-scroll a", has_text="Motivation").locator("span.tabular-nums")
        tab.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        tab_count = int(tab.inner_text())
        page.goto(base + "/architecture/dashboard?layer=motivation", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        footer = page.locator('[data-testid="architecture-pagination"] p').first
        footer.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        list_total = int(re.search(r"of\s+(\d+)", footer.inner_text()).group(1))
        expected = _db_layer_total(mine["org_id"], "motivation")
        # 3 goals + 3 risk assessments + 2 goals held only in the goal table; the 2 nodes are not motivation elements
        assert expected == 8, expected
        assert tab_count == list_total == expected, (tab_count, list_total, expected)
        assert other not in page.locator("body").inner_text()
        _frame(page, "layer", viewport["width"], theme)

        # Risks page, then the Risk Assessment: about risks, with the register's figures.
        page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        register_critical = _number(page, "register-critical")
        register_open = _number(page, "register-open")
        assert (register_critical, register_open) == (2, 3), (register_critical, register_open)
        page.goto(base + "/enterprise/strategic/risk-assessment", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        body = page.locator("body").inner_text()
        assert page.locator("h1").first.inner_text().strip().lower() == "risk assessment"
        assert "Total Gaps" not in body and "Risk Items" not in body, body[:500]
        assert _number(page, "assess-critical-value") == register_critical
        assert _number(page, "assess-open-value") == register_open
        rows = page.locator('[data-testid="risk-assessment-row"]')
        assert rows.count() == register_open
        assert noun in rows.first.inner_text() and other not in body
        _frame(page, "risk-assessment", viewport["width"], theme)

        # Critical gaps: the register page and the implementation page say the same number, and list those gaps.
        page.goto(base + "/enterprise/implementation/gap-analysis", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        register_gaps = _number(page, "gap-stat-critical")
        assert register_gaps == 2, register_gaps
        _frame(page, "gap-analysis", viewport["width"], theme)
        page.goto(base + "/implementation/gaps", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.locator('[data-testid="critical-gap-row"]')
        rows.first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert rows.count() == register_gaps, page.locator("body").inner_text()[:600]
        text = page.locator('[data-testid="critical-gaps-list"]').inner_text()
        assert "urgent but not critical" not in text and "plateau step" not in text and other not in text
        _frame(page, "implementation-gaps", viewport["width"], theme)
    finally:
        context.close()
