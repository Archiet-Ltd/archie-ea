"""An enterprise architect opens the risk register and sees the risks ranked by
how much of the estate each one reaches, opens one, follows it to an element it
touches, and sees the same order after a reload."""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, noun):
    """Wide reaches three elements, Narrow reaches one; a third risk connects to nothing."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.risk import Risk

    app = create_app("testing")
    with app.app_context():
        def element(name):
            row = ArchiMateElement(name=name, type="ApplicationComponent", layer="application",
                                   organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        wide, narrow = element("%s Wide" % noun), element("%s Narrow" % noun)
        for leaf in ("one", "two", "three"):
            target = element("%s leaf %s" % (noun, leaf))
            db.session.add(ArchiMateRelationship(type="Serving", source_id=wide.id, target_id=target.id,
                                                 organization_id=org_id))
        other = element("%s leaf four" % noun)
        db.session.add(ArchiMateRelationship(type="Serving", source_id=narrow.id, target_id=other.id,
                                             organization_id=org_id))
        db.session.commit()
        # An application whose element reaches two more elements, for the mapping step.
        from app.models.application_portfolio import ApplicationComponent

        ledger = ApplicationComponent(name="%s Ledger" % noun, organization_id=org_id)
        db.session.add(ledger)
        db.session.commit()
        for leaf in ("ledger one", "ledger two"):
            db.session.add(ArchiMateRelationship(type="Serving", source_id=ledger.archimate_element_id,
                                                 target_id=element("%s %s" % (noun, leaf)).id,
                                                 organization_id=org_id))
        db.session.commit()
        for title, likelihood, impact, seed in (
            ("%s low risk" % noun, 1, 2, narrow), ("%s big risk" % noun, 3, 3, wide),
            ("%s loose risk" % noun, 5, 5, None),
        ):
            db.session.add(Risk(title=title, likelihood=likelihood, impact=impact, organization_id=org_id,
                                archimate_element_id=seed.id if seed else None))
        db.session.commit()
        db.session.remove()


def _order(page, noun):
    return page.locator("[data-risk-row]").evaluate_all(
        "(rows, noun) => rows.map(r => r.querySelector('a').textContent.trim()).filter(t => t.startsWith(noun))",
        noun,
    )


def test_ranked_risk_list_journey(live_server, browser):
    noun = "Ranker%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    _seed(org["org_id"], noun)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    sign_in(page, base, org["emails"]["enterprise_architect"])

    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("#ranked-risks-rule")).to_contain_text("Ranked by the elements each risk reaches")
    expected = ["%s big risk" % noun, "%s low risk" % noun, "%s loose risk" % noun]
    assert _order(page, noun) == expected
    big = page.locator("[data-risk-row]", has_text="%s big risk" % noun)
    expect(big.locator("[data-risk-reason]")).to_contain_text("reaches 3 elements, likelihood 3 of 5, impact 3 of 5")
    loose = page.locator("[data-risk-row]", has_text="%s loose risk" % noun)
    expect(loose.locator("[data-risk-reason]")).to_contain_text("no linked elements recorded")

    big.get_by_role("link", name="%s big risk" % noun).click()
    page.wait_for_url(lambda url: "/risks/" in url and not url.rstrip("/").endswith("/risks"), timeout=PAGE_TIMEOUT)
    expect(page.locator("#risk-rank-reason")).to_contain_text("reaches 3 elements")
    page.get_by_role("link", name="%s leaf one" % noun).click()
    page.wait_for_url(lambda url: "/archimate/elements/" in url, timeout=PAGE_TIMEOUT)

    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded")
    assert _order(page, noun) == expected

    # Map the loose risk to an application: the ranked card changes without a reload.
    loose_title = "%s loose risk" % noun
    table_row = page.locator("#risk-register-table tbody tr", has_text=loose_title)
    table_row.get_by_role("button", name="Map to…").click()
    page.get_by_label("Search entities to link").fill("%s Ledger" % noun)
    page.get_by_role("button", name="%s Ledger" % noun).click()
    loose = page.locator("[data-risk-row]", has_text=loose_title)
    expect(loose.locator("[data-risk-reason]")).to_contain_text("reaches 2 elements", timeout=PAGE_TIMEOUT)
    expect(loose.locator("[data-risk-reason]")).not_to_contain_text("no linked elements recorded")
    page.reload(wait_until="domcontentloaded")
    loose = page.locator("[data-risk-row]", has_text=loose_title)
    expect(loose.locator("[data-risk-reason]")).to_contain_text("reaches 2 elements")

    # Unmap it again: the card goes back, and stays back after a reload.
    page.locator("#risk-register-table tbody tr", has_text=loose_title).get_by_role("button", name="Map to…").click()
    page.get_by_role("button", name="Unmap").click()
    loose = page.locator("[data-risk-row]", has_text=loose_title)
    expect(loose.locator("[data-risk-reason]")).to_contain_text("no linked elements recorded", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded")
    assert _order(page, noun) == expected
    context.close()
