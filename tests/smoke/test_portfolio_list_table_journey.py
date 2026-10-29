"""Portfolio list: a portfolio manager reads, searches, sorts, hides a column and pages
through the transformation initiatives, and organisation B's initiatives never show up
on organisation A's page.

app/templates/portfolio/index.html was converted from a hand-written <table> to the
shared components/list_table.html macro (T-list-table-01-b01). This drives the real
controls the macro renders — the toolbar search, a column-sort menu, the column-view
checkbox and the rows-per-page control — and asserts the outcome a portfolio manager
needs: the initiatives they were given are on screen with their real figures, search
narrows the list, a column can be hidden and a page can be turned, before asserting
nothing from the other organisation leaks in.
"""
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# enterprise_initiatives.code is globally unique, so every run (including a CI
# re-run against the same persistent test database) needs its own codes.
_RUN = uuid.uuid4().hex[:8]

# More than the macro's default toolbar threshold (8) so the toolbar, search and
# column-view menu are present by default, and more than 10 so a 10-per-page
# setting produces a second page.
_ORG_A_INITIATIVES = [
    ("Core banking replatform", "INIT-A01", "red", 1_500_000, 1_650_000, "Design", 35, "Amara Okafor"),
    ("Cloud migration wave 2", "INIT-A02", "amber", 900_000, 450_000, "Build", 55, "Priya Nair"),
    ("Branch network consolidation", "INIT-A03", "green", 2_000_000, 1_900_000, "Rollout", 90, "Diego Fuentes"),
    ("Payments modernisation", "INIT-A04", "green", 3_200_000, 1_000_000, "Build", 40, "Amara Okafor"),
    ("Data platform renewal", "INIT-A05", "amber", 1_100_000, 1_300_000, "Rollout", 70, "Priya Nair"),
    ("Contact centre upgrade", "INIT-A06", "green", 450_000, 200_000, "Design", 15, "Diego Fuentes"),
    ("Fraud detection uplift", "INIT-A07", "red", 800_000, 950_000, "Build", 48, "Amara Okafor"),
    ("Regulatory reporting refit", "INIT-A08", "green", 600_000, 580_000, "Rollout", 96, "Priya Nair"),
    ("Identity and access overhaul", "INIT-A09", "amber", 700_000, 710_000, "Build", 60, "Diego Fuentes"),
    ("Branch digital kiosks", "INIT-A10", "green", 250_000, 90_000, "Design", 20, "Amara Okafor"),
    ("Treasury systems retirement", "INIT-A11", "red", 1_800_000, 2_100_000, "Rollout", 80, "Priya Nair"),
]
_ORG_B_INITIATIVE = ("Org B only initiative", "INIT-B01", "green", 100_000, 50_000, "Build", 50, "Other Org Sponsor")


def _seed_initiatives(org_id, rows):
    from app import create_app, db
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    with app.app_context():
        for name, code, health, budget, spent, phase, pct, sponsor in rows:
            db.session.add(EnterpriseInitiative(
                organization_id=org_id, name=name, code=f"{code}-{_RUN}", health_status=health,
                approved_budget=budget, spent_to_date=spent, current_phase=phase,
                completion_percentage=pct, executive_sponsor=sponsor, status="active",
            ))
        db.session.commit()
        db.session.remove()


def _seed_second_org():
    """A second, isolated organisation with its own initiative, for the cross-org assertion."""
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        org = Organization(name="Portfolio list-table isolation check org B",
                            slug=f"portfolio-list-table-org-b-{_RUN}")
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        db.session.remove()
    _seed_initiatives(org_id, [_ORG_B_INITIATIVE])
    return org_id


def test_portfolio_manager_reads_searches_sorts_hides_and_pages(browser, live_server, seeded):
    ids = seeded["ids"]
    _seed_initiatives(ids["org"], _ORG_A_INITIATIVES)
    _seed_second_org()

    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["portfolio_manager"])
        page.goto(live_server + "/portfolio/", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("portfolio-table")
        table.wait_for(timeout=PAGE_TIMEOUT)

        # --- the initiatives given are on screen with their real figures
        body_text = page.locator("main, body").first.inner_text()
        for name, code, *_ in _ORG_A_INITIATIVES:
            assert name in body_text, body_text
        assert "1,500,000" in body_text  # a real approved budget, not a placeholder

        # --- organisation B's initiative never shows up here
        assert _ORG_B_INITIATIVE[0] not in body_text

        # --- the row's own action: the initiative name is a working link to its record
        link = page.get_by_test_id("portfolio-link").filter(has_text=_ORG_A_INITIATIVES[0][0])
        href = link.get_attribute("href")
        assert href and "/portfolio/" in href

        # --- search narrows the list (toolbar present because there are 11 rows)
        search = page.locator('[data-slot="data-table"] [data-table-filter]').first
        search.wait_for(timeout=PAGE_TIMEOUT)
        search.fill("Fraud detection")
        page.wait_for_function(
            "document.querySelectorAll('[data-table-id=\"portfolio-table\"] [data-row]:not([hidden])').length === 1",
            timeout=PAGE_TIMEOUT,
        )
        narrowed = page.locator("main, body").first.inner_text()
        assert "Fraud detection uplift" in narrowed
        assert "Core banking replatform" not in narrowed
        reset_btn = page.locator('[data-slot="data-table"] [data-table-reset]').first
        reset_btn.click()
        page.wait_for_function(
            "document.querySelectorAll('[data-table-id=\"portfolio-table\"] [data-row]:not([hidden])').length > 1",
            timeout=PAGE_TIMEOUT,
        )

        # --- sort by Budget ascending
        page.locator('[data-table-sort-trigger="3"]').click()
        page.get_by_role("menuitem", name="Ascending").click()
        first_row = page.locator('[data-table-id="portfolio-table"] [data-row="0"]')
        first_row.wait_for(timeout=PAGE_TIMEOUT)
        assert "Branch digital kiosks" in first_row.inner_text()  # smallest approved budget

        # --- hide the Sponsor column
        page.get_by_role("button", name="View").click()
        page.locator('[data-table-owner="portfolio-table"][data-table-toggle="8"]').click()
        page.wait_for_function(
            "document.querySelector('[data-table-id=\"portfolio-table\"] thead [data-col=\"8\"]').hidden === true",
            timeout=PAGE_TIMEOUT,
        )

        # --- turn the page: 10 rows per page makes an 11-row list a two-page table
        page.locator('[data-table-id="portfolio-table"] [data-table-size]').select_option("10")
        page.wait_for_function(
            "document.querySelectorAll('[data-table-id=\"portfolio-table\"] [data-row]:not([hidden])').length === 10",
            timeout=PAGE_TIMEOUT,
        )
        next_button = page.locator('[data-table-id="portfolio-table"] [data-table-pager="next"]')
        next_button.click()
        page.wait_for_function(
            "document.querySelectorAll('[data-table-id=\"portfolio-table\"] [data-row]:not([hidden])').length === 1",
            timeout=PAGE_TIMEOUT,
        )
    finally:
        ctx.close()
