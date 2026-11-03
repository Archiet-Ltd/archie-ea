"""A portfolio manager reads totals nobody recorded, and is not shown an invented £0.

The consolidation list and the rationalization overview add up costs and savings. When no row carries
a figure the total is unknown, and the cards say "Not recorded" in words. Once a row carries a saving
the same card reads that saving, and the figure nobody recorded still says so. Real counts stay numbers.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def world(request, ai_protocol_stub, live_server):
    seeded = _seed_standard_org(request, ai_protocol_stub)
    return {"seeded": seeded, "tag": uuid.uuid4().hex[:6]}


def _card(page, label):
    card = page.locator("div.bg-card", has=page.get_by_text(label, exact=True)).first
    return re.sub(r"\s+", " ", card.inner_text()).strip()


def _open_consolidation(page, base):
    # The cards fill from the entries request; read them only once it has answered.
    with page.expect_response(lambda r: "/consolidation-list/api/entries" in r.url and r.status == 200, timeout=PAGE_TIMEOUT):
        page.goto(base + "/consolidation-list/", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Portfolio Annual Cost", exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_consolidation_totals_say_not_recorded_until_a_row_carries_a_figure(browser, live_server, world, viewport):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.consolidation_list import ConsolidationListEntry

    email = world["seeded"]["emails"]["portfolio_manager"]
    org_id = world["seeded"]["ids"]["org"]
    page = browser.new_page(viewport=viewport)
    try:
        _login(page, live_server, email)
        _open_consolidation(page, live_server)
        for label in ("Portfolio Annual Cost", "Projected Savings", "Migration Investment"):
            reading = _card(page, label)
            assert "Not recorded" in reading, (label, reading)
            assert "£0" not in reading and "$0" not in reading, (label, reading)
        # A real count of nothing still reads 0.
        assert _card(page, "In Progress").startswith("In Progress 0"), _card(page, "In Progress")

        app = create_app("testing")
        with app.app_context():
            application = ApplicationComponent(name=f"Ledger {world['tag']}-{viewport['width']}", organization_id=org_id,
                                               lifecycle_status="operational")
            db.session.add(application)
            db.session.commit()
            entry = ConsolidationListEntry(application_id=application.id,
                                           estimated_savings=1200, source_type="manual", status="identified")
            db.session.add(entry)
            db.session.commit()
            entry_id = entry.id
        try:
            _open_consolidation(page, live_server)
            assert re.search(r"1,200", _card(page, "Projected Savings")), _card(page, "Projected Savings")
            assert "Not recorded" in _card(page, "Migration Investment")
            assert "Not recorded" in _card(page, "Portfolio Annual Cost")
        finally:
            with app.app_context():
                ConsolidationListEntry.query.filter_by(id=entry_id).delete()
                ApplicationComponent.query.filter_by(name=f"Ledger {world['tag']}-{viewport['width']}").delete()
                db.session.commit()
    finally:
        page.close()


def test_rationalization_savings_say_not_recorded_with_their_own_explanation(browser, live_server, world):
    email = world["seeded"]["emails"]["portfolio_manager"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/applications/rationalization", timeout=PAGE_TIMEOUT)
        savings = page.get_by_test_id("stat-savings")
        expect(savings).to_be_visible(timeout=PAGE_TIMEOUT)
        reading = re.sub(r"\s+", " ", savings.inner_text())
        assert "Not recorded" in reading, reading
        assert "Appears once duplicates carry a cost" in reading, reading
        assert "£0" not in reading, reading
    finally:
        page.close()
