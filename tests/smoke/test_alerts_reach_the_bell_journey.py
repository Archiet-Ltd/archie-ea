"""Journey: an application owner moves a support date closer and the product speaks first.

The owner, who is not an administrator, edits their application's end-of-life
date to 90 days ahead on the application form. The change feed turns the edit
into an alert: the bell's badge counts one, the bell lists one alert that says
what happened and why they received it, and opening it lands on the element's
cited answer at its own address.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_owned_application(org):
    """One application with its ArchiMate element, owned by the signed-in person,
    and a second application they do not own (it must stay quiet)."""
    from app import create_app, db
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        owner = db.session.get(User, org["user_ids"]["solution_architect"])
        assert not owner.is_org_admin and not owner.is_admin(), "the owner must not be an administrator"
        out = {}
        for name, owned in (("Card Payments", True), ("Ledger Archive", False)):
            element = ArchiMateElement(name=name, type="ApplicationComponent", layer="Application",
                                       organization_id=org["org_id"],
                                       description=f"{name} for the journey")
            db.session.add(element)
            db.session.flush()
            row = ApplicationComponent(name=name, organization_id=org["org_id"],
                                       archimate_element_id=element.id,
                                       end_of_life_date=date.today() + timedelta(days=900))
            db.session.add(row)
            db.session.flush()
            if owned:
                db.session.add(ApplicationOwner(application_id=row.id, user_id=owner.id,
                                                organization_id=org["org_id"],
                                                ownership_type="primary"))
            out[name] = row.id
            out[name + " element"] = element.id
        # Linked data: checkout is served by card payments.
        checkout = ArchiMateElement(name="Checkout", type="ApplicationComponent", layer="Application",
                                    organization_id=org["org_id"], description="Online checkout")
        db.session.add(checkout)
        db.session.flush()
        db.session.add(ArchiMateRelationship(type="serving", source_id=out["Card Payments element"],
                                             target_id=checkout.id, organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()
    return out


def _run_the_change_feed(org):
    """What the scheduler does every few seconds: relay the outbox into the
    event log, then turn new events into notifications."""
    from app import create_app, db
    from app.services.change_feed_consumer import consume_changes
    from app.services.event_log_service import relay_outbox_batch

    app = create_app("testing")
    with app.app_context():
        while relay_outbox_batch(500):
            pass
        db.session.commit()
        consume_changes(org["org_id"])
        db.session.remove()


def test_an_owner_moves_a_support_date_and_the_bell_opens_the_cited_answer(browser, live_server):
    org = create_fresh_org("solution_architect")
    apps = _seed_owned_application(org)
    new_date = date.today() + timedelta(days=90)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])

        # 1. The owner moves the support date on the application form.
        page.goto(live_server + "/applications/%d/edit" % apps["Card Payments"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#end_of_life_date").fill(new_date.isoformat())
        page.locator("form:has(#end_of_life_date) [type=submit]").first.click()
        page.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)

        _run_the_change_feed(org)

        # 2. The bell counts one alert and lists it, with what happened and why.
        page.goto(live_server + "/applications/%d/edit" % apps["Ledger Archive"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#notif-badge-count")).to_have_text("1", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Notifications").click()
        items = page.locator("#notif-list .notif-item")
        expect(items).to_have_count(1, timeout=PAGE_TIMEOUT)
        alert = items.first
        expect(alert).to_contain_text("Card Payments reaches end of support on")
        expect(alert).to_contain_text(f"{new_date.day} {new_date:%B %Y}")
        expect(alert).to_contain_text("because you own it")

        # 3. Opening it lands on the element's cited answer, at its own address.
        alert.click()
        page.wait_for_url(re.compile(r".*/intelligence/answers/[0-9a-f]{32}(#.*)?$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_have_text(
            "What breaks if Card Payments fails", timeout=PAGE_TIMEOUT)
    finally:
        context.close()
