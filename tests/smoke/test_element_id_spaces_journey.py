"""Journey: an architect opens, then deletes, the work package they clicked, never its number twin.

The repository lists a mirrored work package by its ArchiMate element id; the
work packages table keeps its own ids, and the two overlap. Before the fix the
quick view's "Full Detail" control was a button that went nowhere, and the
detail page, the edit save and delete looked the id up in the wrong table, so
the architect could open or delete a different work package that shared the
number. This journey builds that collision and clicks what a person clicks.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_collision(org):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.implementation_migration import WorkPackage

    app = create_app("testing")
    with app.app_context():
        target_ae = None
        for _ in range(10):
            ae = ArchiMateElement(name="Payments cutover", type="WorkPackage",
                                  layer="Implementation", organization_id=org["org_id"],
                                  description="Move card payments to the new provider")
            db.session.add(ae)
            db.session.flush()
            if db.session.get(WorkPackage, ae.id) is None:
                target_ae = ae
                break
        assert target_ae is not None
        db.session.add(WorkPackage(name="Payments cutover", organization_id=org["org_id"],
                                   element_type="WorkPackage", archimate_element_id=target_ae.id))
        decoy_ae = ArchiMateElement(name="Ledger archive", type="WorkPackage",
                                    layer="Implementation", organization_id=org["org_id"],
                                    description="Archive the old ledger")
        db.session.add(decoy_ae)
        db.session.flush()
        db.session.add(WorkPackage(id=target_ae.id, name="Ledger archive",
                                   organization_id=org["org_id"], element_type="WorkPackage",
                                   archimate_element_id=decoy_ae.id))
        db.session.flush()
        db.session.execute(db.text(
            "SELECT setval(pg_get_serial_sequence('work_packages', 'id'), "
            "(SELECT MAX(id) FROM work_packages))"))
        # Linked data: the cutover depends on the archive.
        db.session.add(ArchiMateRelationship(type="association", source_id=target_ae.id,
                                             target_id=decoy_ae.id, organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()


def _work_package_names(org):
    from app import create_app, db
    from app.models.implementation_migration import WorkPackage

    app = create_app("testing")
    with app.app_context():
        names = sorted(w.name for w in WorkPackage.query.filter_by(organization_id=org["org_id"]).all())
        db.session.remove()
    return names


def _open_repository(page, base):
    page.goto(base + "/architecture/dashboard?layer=implementation",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("button", name="Open details for Payments cutover").first).to_be_visible(
        timeout=PAGE_TIMEOUT)


def test_the_architect_opens_and_deletes_the_work_package_they_clicked(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    _seed_collision(org)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _open_repository(page, live_server)

        # 1. The quick view's Full Detail is a real link to the clicked work package.
        page.get_by_role("button", name="Open details for Payments cutover").first.click()
        detail = page.get_by_role("link", name="View full detail")
        expect(detail).to_be_visible(timeout=PAGE_TIMEOUT)
        detail.click()
        page.wait_for_url("**/architecture/implementation/WorkPackage/*", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Payments cutover", exact=True)).to_be_visible(
            timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Ledger archive", exact=True)).to_have_count(0)

        # 2. Deleting it from the list removes it, and only it.
        _open_repository(page, live_server)
        page.get_by_role("button", name="Delete Payments cutover").first.click()
        page.get_by_role("button", name="Delete", exact=True).click()
        expect(page.get_by_role("button", name="Open details for Payments cutover")).to_have_count(
            0, timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("button", name="Open details for Ledger archive").first).to_be_visible()
        assert _work_package_names(org) == ["Ledger archive"], "delete removed the wrong work package"
    finally:
        context.close()
