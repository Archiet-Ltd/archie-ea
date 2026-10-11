"""A persisted solution without an ADM phase is visible in the real dashboard."""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_unclassified_solution_survives_dashboard_reload_and_pipeline_navigation(browser, live_server, seeded):
    from app import create_app, db

    app = create_app("testing")
    org_id = seeded["ids"]["org"]
    with app.app_context():
        solution_count = db.session.execute(db.text(
            "SELECT COUNT(*) FROM solutions WHERE organization_id=:org"
        ), {"org": org_id}).scalar_one()
        assert solution_count > 0, "seed must create at least one solution"
        prior_phases = dict(db.session.execute(db.text(
            "SELECT id, adm_phase FROM solutions WHERE organization_id=:org"
        ), {"org": org_id}).fetchall())
        db.session.execute(db.text(
            "UPDATE solutions SET adm_phase=NULL WHERE organization_id=:org"
        ), {"org": org_id})
        db.session.commit()
    context = browser.new_context()
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["solution_architect"])
        response = page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        for reload in (False, True):
            if reload:
                assert page.reload(timeout=PAGE_TIMEOUT).status == 200
            # Solution architects land on the Application layer, not Overview.
            # Every reload restores that persona default; navigate visibly.
            page.get_by_role("button", name="Overview", exact=True).click()
            card = page.get_by_role("heading", name="Solution Pipeline", exact=True).locator("../..")
            expect(card).to_contain_text(
                "%d solution%s without a recorded ADM phase"
                % (solution_count, "s" if solution_count != 1 else "")
            )
            expect(card).not_to_contain_text("No solutions yet")
        assert page.goto(live_server + "/dashboard/health", timeout=PAGE_TIMEOUT).status == 200
        maturity = page.locator('[data-slot="card"]').filter(has=page.locator('[data-slot="card-description"]').get_by_text("Avg Solution Maturity", exact=True))
        expect(maturity.locator('[data-slot="card-title"]')).to_have_text("—")
        distribution = page.get_by_role("heading", name="ADM Phase Distribution", exact=True).locator("../..")
        unknown_row = distribution.get_by_text("Unclassified", exact=True).locator("..")
        expect(unknown_row).to_contain_text("100.0%")
        vision_row = distribution.get_by_text("A: Vision", exact=True).locator("..")
        expect(vision_row).to_contain_text("0.0%")
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        expect(maturity.locator('[data-slot="card-title"]')).to_have_text("—")
        assert page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT).status == 200
        page.get_by_role("button", name="Overview", exact=True).click()
        with page.expect_navigation(timeout=PAGE_TIMEOUT) as navigation:
            card.get_by_role("link", name="Open the pipeline", exact=True).click()
        assert navigation.value.status == 200
        expect(page.locator(f'a[href="/solutions/{seeded["ids"]["solution"]}"]').first).to_be_visible()
    finally:
        context.close()
        with app.app_context():
            for solution_id, phase in prior_phases.items():
                db.session.execute(db.text(
                    "UPDATE solutions SET adm_phase=:phase WHERE id=:id AND organization_id=:org"
                ), {"id": solution_id, "org": org_id, "phase": phase})
            db.session.commit()
