"""An enterprise architect reads a ranked risk and a prioritised action and can see, under each, what it
was worked out from, where that came from, how far to trust it and when it was worked out.

The organisation holds a risk linked to an element, a risk linked to nothing, a critical gap, a late work
package and a work package with no recorded priority. Every result carries all four parts; the result
missing an input reads low confidence and names it; each evidence link opens a page with content.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, noun):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.implementation_migration import Gap
    from app.models.risk import Risk
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    with app.app_context():
        def element(name):
            row = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                                   organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        wide, leaf = element("%s Hub" % noun), element("%s Spoke" % noun)
        db.session.add(ArchiMateRelationship(type="Serving", source_id=wide.id, target_id=leaf.id,
                                             organization_id=org_id))
        db.session.add(Risk(title="%s linked risk" % noun, likelihood=3, impact=3, organization_id=org_id,
                            owner="Ada Lovelace", archimate_element_id=wide.id))
        db.session.add(Risk(title="%s loose risk" % noun, likelihood=5, impact=5, organization_id=org_id))
        db.session.add(Gap(name="%s gap" % noun, severity="critical", organization_id=org_id))
        db.session.add(UnifiedWorkPackage(
            name="%s rollout" % noun, organization_id=org_id, status="in_progress", priority="high",
            progress_percentage=40, end_date=datetime.utcnow() - timedelta(days=5)))
        db.session.add(UnifiedWorkPackage(
            name="%s unrated" % noun, organization_id=org_id, status="in_progress", priority="",
            progress_percentage=10, end_date=datetime.utcnow() - timedelta(days=3)))
        db.session.commit()
        db.session.remove()


def _footer(page, text):
    return page.locator('[data-testid="result-footer"]').filter(has_text=text).first


def _assert_four_parts(footer):
    for part in ("evidence", "source", "confidence", "date"):
        expect(footer.get_by_test_id("result-footer-" + part)).to_be_visible()
    expect(footer.get_by_test_id("result-footer-date")).to_contain_text("Worked out")


def test_ranked_risks_and_actions_carry_the_footer(live_server, browser):
    noun = "Foot%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], noun)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        linked = page.locator("[data-risk-row]", has_text="%s linked risk" % noun)
        footer = linked.get_by_test_id("result-footer")
        _assert_four_parts(footer)
        expect(footer).to_have_attribute("data-confidence", "medium")
        expect(footer.get_by_test_id("result-footer-source")).to_contain_text(
            "Score: entered by the team, Ada Lovelace")
        expect(footer.get_by_test_id("result-footer-source")).to_contain_text("Element: no recorded origin")
        expect(footer.get_by_test_id("result-footer-confidence")).to_contain_text("Score was entered by hand")
        expect(footer.get_by_test_id("result-footer-evidence")).to_contain_text("%s Spoke" % noun)
        loose = page.locator("[data-risk-row]", has_text="%s loose risk" % noun).get_by_test_id("result-footer")
        _assert_four_parts(loose)
        expect(loose).to_have_attribute("data-confidence", "low")
        expect(loose.get_by_test_id("result-footer-confidence")).to_contain_text("Linked elements: none recorded")

        # An evidence link lands on a page about that thing.
        footer.get_by_test_id("result-footer-evidence").get_by_role("link", name="%s Spoke" % noun).click()
        page.wait_for_url(lambda url: "/archimate/" in url, timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("%s Spoke" % noun).first).to_be_visible()

        page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("priority-action-row")
        assert rows.count() == 5
        for index in range(rows.count()):
            _assert_four_parts(rows.nth(index).get_by_test_id("result-footer"))
        late = rows.filter(has_text="%s rollout" % noun).get_by_test_id("result-footer")
        expect(late).to_have_attribute("data-confidence", "medium")
        unrated = rows.filter(has_text="%s unrated" % noun).get_by_test_id("result-footer")
        expect(unrated).to_have_attribute("data-confidence", "low")
        expect(unrated.get_by_test_id("result-footer-confidence")).to_contain_text("Priority not recorded")
        late.get_by_test_id("result-footer-evidence").get_by_role("link").first.click()
        page.wait_for_url(lambda url: "/implementation/work-packages/" in url, timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("%s rollout" % noun).first).to_be_visible()
    finally:
        context.close()
