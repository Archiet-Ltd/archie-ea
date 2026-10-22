"""An architect opens the risk register. A risk linked to an application lists
what it touches (never the risk's own element), and every link lands on a page
with content for that element. A risk linked to nothing says so in plain words
and its action opens the existing link picker."""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, noun):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    app = create_app("testing")
    with app.app_context():
        def element(name):
            row = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                                   organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        billing = ApplicationComponent(name="%s Billing" % noun, organization_id=org_id)
        db.session.add(billing)
        db.session.commit()
        for leaf in ("Invoices", "Payments"):
            db.session.add(ArchiMateRelationship(type="Serving", source_id=billing.archimate_element_id,
                                                 target_id=element("%s %s" % (noun, leaf)).id,
                                                 organization_id=org_id))
        db.session.commit()
        linked_mirror = element("%s Linked risk mirror" % noun)
        loose_mirror = element("%s Loose risk mirror" % noun)
        linked = Risk(title="%s linked risk" % noun, likelihood=3, impact=3, organization_id=org_id,
                      archimate_element_id=linked_mirror.id)
        loose = Risk(title="%s loose risk" % noun, likelihood=4, impact=4, organization_id=org_id,
                     archimate_element_id=loose_mirror.id)
        db.session.add_all([linked, loose])
        db.session.commit()
        db.session.add(RiskEntityLink(organization_id=org_id, risk_id=linked.id, entity_type="application",
                                      entity_id=billing.id))
        db.session.commit()
        db.session.remove()


def test_risk_register_touches_journey(live_server, browser):
    noun = "Toucher%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], noun)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    sign_in(page, base, org["emails"]["enterprise_architect"])

    def register():
        page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        return page.locator("[data-risk-row]", has_text="%s linked risk" % noun)

    row = register()
    touches = row.locator("[data-risk-touches]")
    expect(touches).to_be_visible()
    # Never the risk's own element.
    assert "mirror" not in touches.inner_text()
    # The count is said once per row: in the ranking sentence, not also as a chip.
    assert row.inner_text().lower().count("reaches 2 elements") == 1, row.inner_text()
    # What each is: the linked application is named under "Linked to"; the
    # reach number equals the reached elements named beside it.
    linked = row.locator("[data-risk-linked]")
    reached = row.locator("[data-risk-reached]")
    assert linked.inner_text().strip().rstrip(";") == "Linked to %s Billing" % noun, linked.inner_text()
    assert linked.locator("a").all_inner_texts() == ["%s Billing" % noun]
    reached_names = reached.locator("a").all_inner_texts()
    assert sorted(reached_names) == ["%s Invoices" % noun, "%s Payments" % noun]
    shown = int(reached.locator(".tabular-nums").inner_text())
    assert shown == len(reached_names) == 2
    assert "reaches 2 more" in touches.inner_text().lower()
    assert "reaches 2 elements" in row.locator("[data-risk-reason]").inner_text().lower()
    names = touches.locator("a").all_inner_texts()
    assert sorted(names) == sorted(["%s Billing" % noun, "%s Invoices" % noun, "%s Payments" % noun]), names

    # Click every Touches link: the landing page shows content for that element.
    for name in names:
        row = register()
        row.locator("[data-risk-touches]").get_by_role("link", name=name, exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        body = page.locator("body")
        expect(body).not_to_contain_text("Page not found")
        if "/impact" in page.url:
            # The element's blast radius is drawn, not the empty state.
            expect(page.locator("#impact-counts")).not_to_contain_text("Loading", timeout=PAGE_TIMEOUT)
            expect(page.locator("#impact-empty")).to_be_hidden()
            expect(page.locator("#impact-svg")).to_contain_text(
                "Billing" if name.endswith(("Invoices", "Payments")) else "Invoices")
        else:
            expect(body).to_contain_text(name, timeout=PAGE_TIMEOUT)

    # The unlinked risk says so, and its action opens the link picker.
    register()
    loose = page.locator("[data-risk-row]", has_text="%s loose risk" % noun)
    expect(loose.locator("[data-risk-unlinked]")).to_contain_text("Not linked to any application, solution or programme yet")
    expect(loose.locator("[data-risk-touches]")).to_have_count(0)
    expect(loose).not_to_contain_text("Reaches —")
    assert "mirror" not in loose.inner_text()
    loose.get_by_role("button", name="Link it").click()
    expect(page.get_by_label("Search entities to link")).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("dialog", name="Risk detail")).to_contain_text("%s loose risk" % noun)
    context.close()
