"""Whoever can set a RACI cell can clear it, and a stakeholder who is added is kept.

An architect sets a cell, then clears it with Delete and by cycling past I; both succeed and survive a reload.
A Viewer is refused in words on clear as on set. A stakeholder added with no cell is still on the matrix after a
reload. A user of another organisation cannot clear the first organisation's cell.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .test_raci_matrix_journey import RACI, _open, _seed_capability

pytestmark = [pytest.mark.smoke, pytest.mark.journey]
expect.set_options(timeout=PAGE_TIMEOUT)

CELL = "/organization/raci/api/cell"


def _cap_id(org_id):
    from app import create_app
    from app.models.unified_capability import UnifiedCapability

    with create_app("testing").app_context():
        return UnifiedCapability.query.filter_by(organization_id=org_id).one().id


def _add_stakeholder(page, name):
    add = page.locator("[data-testid=raci-add-first] button")
    if add.count():
        add.click()
    else:
        page.get_by_role("button", name=re.compile("Add Stakeholder", re.I)).first.click()
    page.fill("#raci-stakeholder-search", name)
    page.get_by_role("button", name=re.compile(r'Create "%s"' % re.escape(name))).click()
    expect(page.locator("[data-testid=raci-matrix] thead th", has_text=name)).to_be_visible()


def _reload(page):
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("[data-testid=raci-matrix]")


def test_architect_clears_a_cell_by_delete_and_by_cycling_past_i(browser, live_server):
    org = create_fresh_org("business_architect")
    _seed_capability(org["org_id"])
    context, page = _open(browser, live_server, org["emails"]["business_architect"])
    try:
        _add_stakeholder(page, "Finance Lead")
        cell = page.locator("[data-raci-cell]").first
        cell.focus()
        page.keyboard.press("A")
        expect(cell).to_have_text(re.compile(r"^\s*A\s*$"))
        page.keyboard.press("Delete")
        expect(cell).to_have_text(re.compile(r"^\s*$"))
        _reload(page)
        expect(page.locator("[data-raci-cell]").first).to_have_text(re.compile(r"^\s*$"))
        expect(page.locator("[data-testid=raci-matrix] thead th", has_text="Finance Lead")).to_be_visible()

        cell = page.locator("[data-raci-cell]").first
        for letter in ("R", "A", "C", "I"):  # click cycles R, A, C, I, then clears
            cell.click()
            expect(cell).to_have_text(re.compile(r"^\s*%s\s*$" % letter))
        cell.click()
        expect(cell).to_have_text(re.compile(r"^\s*$"))
        expect(page.locator("[data-testid=toast], [role=alert]", has_text=re.compile("permission|not allowed", re.I))).to_have_count(0)
        _reload(page)
        expect(page.locator("[data-raci-cell]").first).to_have_text(re.compile(r"^\s*$"))
    finally:
        context.close()


def test_viewer_is_refused_in_words_on_clear_as_on_set(browser, live_server):
    org = create_fresh_org("business_architect", platform_role="Viewer")
    _seed_capability(org["org_id"])
    cap = _cap_id(org["org_id"])
    context, page = _open(browser, live_server, org["emails"]["business_architect"])
    try:
        body = {"stakeholder_type": "user", "stakeholder_id": org["user_ids"]["business_architect"],
                "stakeholder_name": "Viewer", "capability_id": cap, "raci": "R"}
        status, set_body = api(page, "POST", CELL, body)
        assert status == 403, (status, set_body)
        status, clear_body = api(page, "DELETE", CELL, {k: body[k] for k in ("stakeholder_type", "stakeholder_id", "capability_id")})
        assert status == 403, (status, clear_body)
        assert str(clear_body) == str(set_body) or "permission" in str(clear_body).lower() or "not allowed" in str(clear_body).lower(), clear_body
    finally:
        context.close()


def test_a_stakeholder_added_with_no_cell_is_kept_after_a_reload(browser, live_server):
    org = create_fresh_org("business_architect")
    _seed_capability(org["org_id"])
    context, page = _open(browser, live_server, org["emails"]["business_architect"])
    try:
        _add_stakeholder(page, "Risk Officer")
        _reload(page)
        expect(page.locator("[data-testid=raci-matrix] thead th", has_text="Risk Officer")).to_be_visible()
        expect(page.locator("[data-testid=raci-example-row]")).to_have_count(0)
    finally:
        context.close()


def test_another_organisation_cannot_clear_the_first_organisations_cell(browser, live_server):
    first = create_fresh_org("business_architect")
    _seed_capability(first["org_id"])
    cap = _cap_id(first["org_id"])
    context, page = _open(browser, live_server, first["emails"]["business_architect"])
    try:
        who = first["user_ids"]["business_architect"]
        status, body = api(page, "POST", CELL, {"stakeholder_type": "user", "stakeholder_id": who,
                                                "stakeholder_name": "Owner", "capability_id": cap, "raci": "A"})
        assert status == 200, (status, body)
    finally:
        context.close()
    second = create_fresh_org("business_architect")
    context, other = _open(browser, live_server, second["emails"]["business_architect"])
    try:
        status, body = api(other, "DELETE", CELL, {"stakeholder_type": "user", "stakeholder_id": who, "capability_id": cap})
        assert status == 404, (status, body)
    finally:
        context.close()
    context, page = _open(browser, live_server, first["emails"]["business_architect"])
    try:
        status, body = api(page, "GET", "/organization/raci/api/data")
        assert any(a["raci"] == "A" for a in body["data"]["assignments"]), body
    finally:
        context.close()
