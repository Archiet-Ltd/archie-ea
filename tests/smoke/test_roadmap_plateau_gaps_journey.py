"""Roadmaps screen: a plateau lists its gaps and realising work packages, and the
timeline names the gap each work package addresses. As the enterprise architect, open
Roadmaps, read the plateau card and the timeline row, follow the work package link when
this build has the page, then reload and see the same."""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in


def _seed(org_id):
    from app import create_app, db
    from app.models.implementation_migration import Gap, Plateau
    from app.services import gap_register_service, work_package_service

    app = create_app("testing")
    with app.app_context():
        plateau = Plateau(name="Smoke plateau", organization_id=org_id, sequence_order=1)
        bare = Plateau(name="Smoke bare plateau", organization_id=org_id, sequence_order=2)
        recorded = Gap(name="Smoke recorded gap", organization_id=org_id, severity="high")
        via_wp = Gap(name="Smoke work package gap", organization_id=org_id, severity="high")
        db.session.add_all([plateau, bare, recorded, via_wp])
        db.session.flush()
        gap_register_service.link_gap_to_plateau(recorded.id, plateau.id, org_id)
        wp = work_package_service.create_work_package(
            organization_id=org_id, name="Smoke work package", status="in_progress",
            start_date="2026-05-01", end_date="2026-09-01",
            plateau_id=plateau.id, gap_id=via_wp.id)
        wp_id = wp.id
        db.session.commit()
        db.session.remove()
    return wp_id


def _check_screen(page, base):
    page.goto(base + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    card = page.locator("[data-testid=plateau-gaps]").first
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    plateau_card = page.locator("div.rounded-lg", has_text="Smoke plateau").first
    expect(plateau_card).to_contain_text("2 gap(s) assigned")
    expect(plateau_card).to_contain_text("Smoke recorded gap")
    expect(plateau_card).to_contain_text("Smoke work package gap")
    expect(plateau_card).to_contain_text("Realised by 1 work package(s)")
    expect(plateau_card).to_contain_text("Smoke work package")
    bare = page.locator("div.rounded-lg", has_text="Smoke bare plateau").first
    expect(bare).to_contain_text("0 gap(s) assigned")
    row = page.locator(".rdm-gantt-row", has_text="Smoke work package").first
    expect(row.locator("[data-testid=gantt-gap]")).to_have_text("Smoke work package gap", timeout=PAGE_TIMEOUT)
    expect(page.locator(".rdm-gantt-section-hdr", has_text="Smoke plateau")).to_be_visible()


@pytest.mark.smoke
def test_plateau_lists_gaps_and_work_packages_and_survives_reload(browser, live_server):
    fresh = create_fresh_org("enterprise_architect")
    wp_id = _seed(fresh["org_id"])
    page = browser.new_context(ignore_https_errors=True).new_page()
    try:
        sign_in(page, live_server, fresh["emails"]["enterprise_architect"])
        _check_screen(page, live_server)

        link = page.locator(".rdm-gantt-row a.rdm-gantt-name", has_text="Smoke work package")
        if link.count():  # the work package page exists in this build
            link.first.click()
            page.wait_for_url("**/implementation/work-packages/%d" % wp_id, timeout=PAGE_TIMEOUT)
            expect(page.locator("body")).to_contain_text("Smoke work package")

        _check_screen(page, live_server)  # fresh load: the same answer
    finally:
        page.context.close()
