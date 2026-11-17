"""An impact answer says the day a hand-added element was first added, not the day it was last edited.

The person: a CTO whose organisation hand-added "Order Service" on 24 Oct 2026, wired three other
systems to depend on it, and edited its description afterwards. Their goal: ask the chat what breaks
if a system fails and read, on the row for that service, the day it was first added.
"""

import os
from datetime import datetime

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, sign_in
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SERVICE = "Order Service"
ADDED = datetime(2026, 10, 24, 8, 0)
EDITED = datetime(2026, 11, 12, 15, 0)


def _seed(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.entity_history import EntityHistory

    app = create_app("testing")
    with app.app_context():
        service = ArchiMateElement(
            name=SERVICE, type="ApplicationService", layer="application", organization_id=org_id
        )
        db.session.add(service)
        db.session.flush()
        for n in range(3):
            caller = ArchiMateElement(
                name="Order Caller %d" % n, type="ApplicationComponent", layer="application",
                organization_id=org_id,
            )
            db.session.add(caller)
            db.session.flush()
            db.session.add(ArchiMateRelationship(
                type="Serving", source_id=caller.id, target_id=service.id, organization_id=org_id,
            ))
        # The one change history holds the first version on the day it was added; the
        # element's own last-recorded time then moves to the later edit.
        db.session.add(EntityHistory(
            organization_id=org_id, table_name="archimate_elements", record_id=service.id,
            snapshot={"name": SERVICE}, valid_from=ADDED, recorded_at=ADDED, source="trigger",
        ))
        service.description = "Takes customer orders"
        service.recorded_at = EDITED
        db.session.commit()
        db.session.remove()


def test_the_impact_row_names_the_day_the_service_was_first_added(page, live_server):
    org = create_fresh_org("cto", org_admin=True)
    _seed(org["org_id"])
    sign_in(page, live_server, org["emails"]["cto"])

    answer = ask(page, live_server, "What are the single points of failure in our stack?")
    row = answer.locator("[data-catalogue-record]").filter(has_text=SERVICE)
    expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
    source = row.locator("[data-catalogue-source]")
    expect(source).to_be_visible(timeout=PAGE_TIMEOUT)
    assert source.inner_text().strip() == "added by hand on 24 Oct 2026", source.inner_text()
    shots = os.environ.get("IMPACT_DAY_SHOTS")
    if shots:
        os.makedirs(shots, exist_ok=True)
        page.screenshot(path=os.path.join(shots, "impact-day.png"), full_page=True)
