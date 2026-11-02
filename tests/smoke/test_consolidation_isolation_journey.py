"""An enterprise architect reads only their own organisation's consolidation work.

Two organisations each hold a duplicate-application candidate, an approved consolidation
opportunity with a saving, and a recorded saving. Each architect opens the candidates page and
the opportunities page and finds their own applications, plan and projected saving, with no name
or figure from the other organisation; every application link on those pages lands on that
application's own page.
"""
import os
import re
import uuid
from datetime import date

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("CONSOLIDATION_WALK_DIR")
SAVING = {"a": 612345.0, "b": 987654.0}


@pytest.fixture(scope="module")
def two_consolidating_orgs(live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.consolidation import (
        ConsolidationCandidate,
        ConsolidationOpportunity,
        SavingsRealization,
    )

    tag = uuid.uuid4().hex[:6]
    out = {"tag": tag}
    orgs = {k: create_fresh_org("enterprise_architect") for k in ("a", "b")}
    app = create_app("testing")
    with app.app_context():
        for key, org in orgs.items():
            org_id = org["org_id"]
            first = ApplicationComponent(name=f"Ledger {key} {tag}", organization_id=org_id)
            second = ApplicationComponent(name=f"Ledgerlite {key} {tag}", organization_id=org_id)
            db.session.add_all([first, second])
            db.session.flush()
            db.session.add(ConsolidationCandidate(
                organization_id=org_id, primary_application_id=first.id,
                duplicate_application_id=second.id, similarity_score=0.93,
                detection_method="name_match", status="pending_review"))
            opp = ConsolidationOpportunity(
                organization_id=org_id, name=f"Retire Ledgerlite {key} {tag}", status="approved",
                target_application_id=first.id, estimated_annual_savings=SAVING[key],
                target_completion_date=date(date.today().year + 1, 6, 30))
            db.session.add(opp)
            db.session.flush()
            db.session.add(SavingsRealization(
                organization_id=org_id, opportunity_id=opp.id, period_start=date(2026, 1, 1),
                period_end=date(2026, 3, 31), realized_savings=4242.0, savings_category="license"))
            db.session.commit()
            out[key] = {"email": org["emails"]["enterprise_architect"], "first": first.name,
                        "second": second.name, "plan": opp.name}
        db.session.remove()
    return out


def _text(page):
    return re.sub(r"\s+", " ", page.locator("main, body").first.inner_text())


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _digits(text):
    return re.sub(r"[^0-9]", "", text)


def _read(browser, live_server, world, mine, theirs, width):
    mobile = width < 600
    page = browser.new_page(viewport={"width": width, "height": 900 if not mobile else 844})
    try:
        sign_in(page, live_server, world[mine]["email"])
        assert page.goto(live_server + "/enterprise/consolidation/candidates",
                         timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_text(world[mine]["first"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
        body = _text(page)
        assert world[mine]["second"] in body
        assert world[theirs]["first"] not in body and world[theirs]["second"] not in body
        _shot(page, f"{mine}-candidates-{width}")

        # The application link lands on that application's own page, with its name.
        page.get_by_role("link", name=world[mine]["first"]).first.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/applications/" in page.url, page.url
        expect(page.get_by_text(world[mine]["first"]).first).to_be_visible(timeout=PAGE_TIMEOUT)

        assert page.goto(live_server + "/enterprise/consolidation/opportunities",
                         timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_text(world[mine]["plan"])).to_be_visible(timeout=PAGE_TIMEOUT)
        body = _text(page)
        assert world[theirs]["plan"] not in body and world[theirs]["first"] not in body
        digits = _digits(body)
        assert str(int(SAVING[mine])) in digits or str(int(SAVING[mine]))[:3] in digits, body
        assert str(int(SAVING[theirs]))[:3] not in digits, body
        _shot(page, f"{mine}-opportunities-{width}")
        page.get_by_role("link", name=world[mine]["first"]).first.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/applications/" in page.url, page.url
        expect(page.get_by_text(world[mine]["first"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_first_organisation_reads_only_its_own(browser, live_server, two_consolidating_orgs, width):
    _read(browser, live_server, two_consolidating_orgs, "a", "b", width)


@pytest.mark.parametrize("width", [1440, 390])
def test_second_organisation_reads_only_its_own(browser, live_server, two_consolidating_orgs, width):
    _read(browser, live_server, two_consolidating_orgs, "b", "a", width)
