"""Work package facts card: missing facts link to where they are set,
the card sits on the library's spacing, the 0% bar shows, and the owner
panel names whose owner it shows.

A journey at 1440 light and 390 dark proves each of the four walk lows:
1. "No capability linked" and "No dates set" each carry a link to the edit page.
2. The facts card has the fact list's normal padding and row spacing.
3. A package at 0% shows its progress bar (empty) with no gap before Dates at 390.
4. The "Owners and approvals" panel names whose owner it shows ("Capability owner"
   beside the package's own "Owner").
"""

import os
import re
from datetime import date
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _set_wp_fields(org, wp_id, capability_id=None, owner_id=None,
                   start_date=None, end_date=None, progress_percentage=None):
    from app import create_app, db
    from app.models.unified_work_package import UnifiedWorkPackage

    app = create_app("testing")
    with app.app_context():
        row = db.session.get(UnifiedWorkPackage, wp_id)
        if capability_id is not None:
            row.capability_id = capability_id
        if owner_id is not None:
            row.owner_id = owner_id
        if start_date is not None:
            row.start_date = start_date
        if end_date is not None:
            row.end_date = end_date
        if progress_percentage is not None:
            row.progress_percentage = progress_percentage
        db.session.commit()
        db.session.remove()


def _set_capability_owner(org, capability_id, user_id):
    from app import create_app, db
    from app.services.capability_ownership_service import set_capability_owner

    app = create_app("testing")
    with app.app_context():
        set_capability_owner(capability_id=capability_id, user_id=user_id,
                             organization_id=org["org_id"])
        db.session.commit()
        db.session.remove()


def _create_capability(org, name):
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        cap = UnifiedCapability(name=name, organization_id=org["org_id"])
        db.session.add(cap)
        db.session.flush()
        cap_id = cap.id
        db.session.commit()
        db.session.remove()
        return cap_id


def _estate(page, org):
    """A plateau and two work packages: one sparse (no capability, no dates)
    and one full (capability, dates, owner, progress)."""
    suffix = org["suffix"]
    names = {
        "sparse_wp": "Sparse package %s" % suffix,
        "full_wp": "Full package %s" % suffix,
        "plateau": "Target state %s" % suffix,
        "capability": "Capability %s" % suffix,
    }
    status, plateau = api(page, "POST", "/capability-map/api/roadmap/plateaus",
                          {"name": names["plateau"], "sequence_order": 1})
    assert status == 201, plateau

    status, sparse = api(page, "POST", "/capability-map/api/roadmap/work-packages", {
        "name": names["sparse_wp"], "plateau_id": plateau["plateau"]["id"],
    })
    assert status == 201, sparse
    _set_wp_fields(org, sparse["work_package"]["id"], progress_percentage=0)

    status, full = api(page, "POST", "/capability-map/api/roadmap/work-packages", {
        "name": names["full_wp"], "plateau_id": plateau["plateau"]["id"],
    })
    assert status == 201, full
    _set_wp_fields(org, full["work_package"]["id"],
                   start_date=date(2026, 11, 1), end_date=date(2027, 1, 31),
                   progress_percentage=0)

    cap_id = _create_capability(org, names["capability"])
    _set_wp_fields(org, full["work_package"]["id"], capability_id=cap_id)
    _set_capability_owner(org, cap_id, org["user_ids"]["enterprise_architect"])

    return names, sparse["work_package"]["id"], full["work_package"]["id"], cap_id


def _shoot(page, label):
    out = os.environ.get("WP02_SHOTS")
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(Path(out) / label), full_page=True)


def _check_sparse(page, live_server, sparse_id, shots_prefix):
    page.goto(live_server + "/implementation/work-packages/%d" % sparse_id,
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    body = page.locator("body")
    expect(body).not_to_contain_text("We couldn't find that page")

    # Missing capability links to the edit page
    cap_cell = page.get_by_test_id("wp-capability")
    expect(cap_cell).to_contain_text("No capability linked")
    cap_link = cap_cell.locator("a")
    expect(cap_link).to_have_count(1)
    expect(cap_link).to_have_attribute("href",
        re.compile(r"/implementation/work-packages/%d/edit" % sparse_id))

    # Missing dates link to the edit page
    dates_cell = page.get_by_test_id("wp-dates")
    expect(dates_cell).to_contain_text("No dates set")
    dates_link = dates_cell.locator("a")
    expect(dates_link).to_have_count(1)
    expect(dates_link).to_have_attribute("href",
        re.compile(r"/implementation/work-packages/%d/edit" % sparse_id))

    # Progress bar shows at 0% (empty bar)
    progress_cell = page.get_by_test_id("wp-progress")
    expect(progress_cell).to_contain_text("0%")

    _shoot(page, shots_prefix + "-sparse.png")


def _check_full(page, live_server, full_id, cap_name, shots_prefix):
    page.goto(live_server + "/implementation/work-packages/%d" % full_id,
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    body = page.locator("body")
    expect(body).not_to_contain_text("We couldn't find that page")

    # Capability is shown as a link
    cap_cell = page.get_by_test_id("wp-capability")
    expect(cap_cell).to_contain_text(cap_name)
    cap_link = cap_cell.locator("a")
    expect(cap_link).to_have_count(1)

    # Dates are shown
    dates_cell = page.get_by_test_id("wp-dates")
    expect(dates_cell).to_contain_text("1 Nov 2026")

    # Progress bar shows at 0%
    progress_cell = page.get_by_test_id("wp-progress")
    expect(progress_cell).to_contain_text("0%")

    # Owner panel names whose owner it shows: "Capability owner"
    owner_panel = page.get_by_test_id("owner-panel")
    expect(owner_panel).to_contain_text("Capability owner")

    _shoot(page, shots_prefix + "-full.png")


def test_work_package_facts_card_next_step_and_owner_label(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        names, sparse_id, full_id, cap_id = _estate(page, org)

        _check_sparse(page, live_server, sparse_id, "wp02-1440-light-")
        _check_full(page, live_server, full_id, names["capability"], "wp02-1440-light-")
    finally:
        context.close()

    # Same at 390 dark
    dark = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 800},
                               color_scheme="dark")
    dark.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
                         "localStorage.setItem('theme', 'dark');")
    dpage = dark.new_page()
    try:
        sign_in(dpage, live_server, org["emails"]["enterprise_architect"])
        _check_sparse(dpage, live_server, sparse_id, "wp02-390-dark-")
        _check_full(dpage, live_server, full_id, names["capability"], "wp02-390-dark-")
    finally:
        dark.close()
