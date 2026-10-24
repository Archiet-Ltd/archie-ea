"""An enterprise architect checks the dashboard's actions, health score and element count.

In an organisation whose gap register repeats names (some stored with a "Gap:"
label already in front), with solutions in several phases and open risks, the
architect opens the dashboard and reads the prioritised actions: no two rows say
the same thing and no name is prefixed twice. They open "How this is worked out"
and add up the weighted parts by hand to the headline score. They read the element
figure, click "Open Composer" and find the same number there. Each action link
lands on a page that shows the thing it named.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("SMOKE_WALK_DIR")


def _seed(org_id, suffix):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Gap
    from app.models.risk import Risk, RiskStatus
    from app.models.solution_models import Solution

    app = create_app("testing")
    with app.app_context():
        for n in range(6):
            db.session.add(ApplicationComponent(name="Ledger %d %s" % (n, suffix), organization_id=org_id))
        for n, phase in enumerate(["D", "A", "E"]):
            solution = Solution(name="Programme %d %s" % (n, suffix), organization_id=org_id, maturity_current=0)
            db.session.add(solution)
            db.session.flush()
            solution.adm_phase = phase
        for severity, name in [
            ("medium", "Gap: Gap: Asset Lifecycle %s" % suffix),
            ("critical", "Gap: Asset Lifecycle %s" % suffix),
            ("low", "Asset Lifecycle %s" % suffix),
            ("high", "Gap: Data Analytics %s" % suffix),
            ("high", "Data Analytics %s" % suffix),
        ]:
            db.session.add(Gap(name=name, severity=severity, organization_id=org_id))
        db.session.add(Risk(title="Single relay %s" % suffix, likelihood=5, impact=5, status=RiskStatus.OPEN,
                            organization_id=org_id))
        db.session.add(Risk(title="Old licence %s" % suffix, likelihood=2, impact=2, status=RiskStatus.OPEN,
                            organization_id=org_id))
        for n, (kind, layer) in enumerate([("Goal", "motivation"), ("BusinessProcess", "business"),
                                           ("ApplicationComponent", "application"), ("Node", "technology")]):
            db.session.add(ElementFor(kind, layer, n, org_id, suffix))
        db.session.commit()
        db.session.remove()


def ElementFor(kind, layer, n, org_id, suffix):
    from app.models.archimate_core import ArchiMateElement

    return ArchiMateElement(name="%s %d %s" % (kind, n, suffix), type=kind, layer=layer, organization_id=org_id)


def _shot(page, name):
    if WALK_DIR:
        Path(WALK_DIR).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(WALK_DIR) / name), full_page=True)


def test_architect_checks_actions_score_and_element_count(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    suffix = org["suffix"]
    _seed(org["org_id"], suffix)
    other = create_fresh_org("enterprise_architect")
    _seed(other["org_id"], other["suffix"])

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # 1. Each action is said once, and no name carries its label twice.
        links = page.get_by_test_id("priority-action-link")
        expect(links.first).to_be_visible()
        actions = [t.strip() for t in links.all_inner_texts()]
        assert len(actions) == len(set(actions)), actions
        assert not [a for a in actions if "Gap:" in a], actions
        mine = [a for a in actions if suffix in a]
        assert "Convert the Asset Lifecycle %s to a work package" % suffix in mine
        assert "Convert the Data Analytics %s to a work package" % suffix in mine
        assert len([a for a in mine if "Asset Lifecycle" in a]) == 1
        assert not [a for a in actions if other["suffix"] in a], "another organisation's action is listed"
        _shot(page, "dashboard-1440-light.png")

        # 2. The score follows from the parts shown.
        page.get_by_test_id("health-score-disclosure-button").click()
        disclosure = page.get_by_test_id("health-score-disclosure")
        expect(disclosure).to_be_visible()
        score = float(page.get_by_test_id("health-score-value").inner_text().strip())
        values = [float(v.rstrip("%")) for v in page.get_by_test_id("health-part-value").all_inner_texts()]
        weights = [float(w.rstrip("%")) for w in page.get_by_test_id("health-part-weight").all_inner_texts()]
        assert len(values) == len(weights) >= 2
        assert abs(sum(weights) - 100) <= 0.2
        assert abs(sum(v * w / 100 for v, w in zip(values, weights)) - score) <= 0.1
        expect(page.get_by_test_id("health-score-sum")).to_contain_text("= %.1f" % score)
        _shot(page, "dashboard-health-open-1440-light.png")

        # 3. The element figure is the Composer's figure.
        page.get_by_test_id("layer-tab-overview").click()
        figure = int(page.get_by_test_id("health-kpi-elements").inner_text().strip())
        assert figure >= 4
        page.get_by_test_id("open-composer").click()
        page.wait_for_url(lambda url: "/archimate/composer" in url, timeout=PAGE_TIMEOUT)
        status = page.get_by_text(re.compile(r"Layered — \d+ elements"))
        expect(status.first).to_be_visible(timeout=PAGE_TIMEOUT)
        shown = int(re.search(r"Layered — (\d+) elements", status.first.inner_text()).group(1))
        assert shown == figure or "not shown" in status.first.inner_text(), status.first.inner_text()

        # 4. An action link lands on the thing it named.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("priority-action-link").filter(has_text="Asset Lifecycle %s" % suffix).click()
        page.wait_for_url(lambda url: "gap-analysis" in url, timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Asset Lifecycle %s" % suffix).first).to_be_visible()

        # 5. The same page reads at phone width and in the dark theme.
        if WALK_DIR:
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                for theme in ("light", "dark"):
                    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
                    page.get_by_test_id("health-score-disclosure-button").click()
                    expect(page.get_by_test_id("health-score-sum")).to_be_visible()
                    _shot(page, "e-dashboard-%d-%s.png" % (width, theme))
                    page.get_by_test_id("layer-tab-overview").click()
                    page.get_by_test_id("priority-action-link").first.scroll_into_view_if_needed()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        context.close()
