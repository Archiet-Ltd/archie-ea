"""A RACI cell refuses another organisation's capability.

Organisation A's architect tries to put a person against organisation B's capability and is told it was not found,
with nothing stored; clearing a cell against that capability gets the same answer. A's own capability still saves
and shows on A's matrix after a reload.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org
from .test_raci_matrix_journey import _open, _seed_capability
from .test_raci_clear_journey import CELL, _cap_id, _reload

pytestmark = [pytest.mark.smoke, pytest.mark.journey]
expect.set_options(timeout=PAGE_TIMEOUT)


def _cell_count(org_id):
    from app import create_app
    from app.models.organization_model import EnterpriseRaciAssignment

    with create_app("testing").app_context():
        return EnterpriseRaciAssignment.query.filter_by(organization_id=org_id).count()


def test_cell_refuses_another_organisations_capability_and_keeps_its_own(browser, live_server):
    mine = create_fresh_org("business_architect")
    theirs = create_fresh_org("business_architect")
    _seed_capability(mine["org_id"], name="Order Handling")
    _seed_capability(theirs["org_id"], name="Their Secret Capability")
    my_cap, their_cap = _cap_id(mine["org_id"]), _cap_id(theirs["org_id"])
    who = mine["user_ids"]["business_architect"]
    body = {"stakeholder_type": "user", "stakeholder_id": who, "stakeholder_name": "Owner", "raci": "A"}
    context, page = _open(browser, live_server, mine["emails"]["business_architect"])
    try:
        before = _cell_count(mine["org_id"])
        status, refused = api(page, "POST", CELL, dict(body, capability_id=their_cap))
        assert status == 404, (status, refused)
        status, missing = api(page, "POST", CELL, dict(body, capability_id=987654321))
        assert status == 404, (status, missing)
        assert refused["error"] == missing["error"], (refused, missing)  # a foreign id looks like a missing one
        status, cleared = api(page, "DELETE", CELL, {k: body[k] for k in ("stakeholder_type", "stakeholder_id")}
                              | {"capability_id": their_cap})
        assert status == 404, (status, cleared)
        assert _cell_count(mine["org_id"]) == before
        assert _cell_count(theirs["org_id"]) == 0

        status, saved = api(page, "POST", CELL, dict(body, capability_id=my_cap))
        assert status == 200, (status, saved)
        _reload(page)
        expect(page.locator("[data-testid=raci-matrix] tbody tr", has_text="Order Handling").first).to_be_visible()
        expect(page.locator("[data-testid=raci-matrix]", has_text="Their Secret Capability")).to_have_count(0)
        expect(page.locator("[data-raci-cell]", has_text=re.compile(r"^\s*A\s*$")).first).to_be_visible()
    finally:
        context.close()
