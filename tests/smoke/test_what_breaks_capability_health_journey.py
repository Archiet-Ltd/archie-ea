"""A chief technology officer asks what breaks if the hub fails and every row says how healthy it is.

The hub serves one application with a recorded health, one application with none, and one capability
nobody has assessed. The capability row says "Not assessed" in words, never a blank and never a red
"danger" badge; the application with no health still says "Health not recorded". A second organisation
with its own capability never changes the answer.
"""

import os

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, open_chat, sign_in
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = (
    ("desktop-light", {"width": 1440, "height": 900}),
    ("phone-dark", {"width": 390, "height": 844}),
)
HUB = "Switchyard"
CAPABILITY = "Settle Payments"


def _seed(org_id, hub, capability):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    with app.app_context():
        def element(name, kind, layer):
            row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
            db.session.add(row)
            db.session.flush()
            return row

        def application(name, health):
            row = element(name, "ApplicationComponent", "application")
            db.session.add(ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=row.id,
                                                lifecycle_status="active", health_status=health))
            db.session.flush()
            return row

        centre = application(hub, "healthy")
        targets = [application("Ledger Core", "at_risk"), application("Quay Archive", None),
                   element(capability, "Capability", "business")]
        for target in targets:
            db.session.add(ArchiMateRelationship(type="Serving", source_id=centre.id, target_id=target.id,
                                                 organization_id=org_id))
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def organisations(live_server):
    ours = create_fresh_org("cto")
    _seed(ours["org_id"], HUB, CAPABILITY)
    theirs = create_fresh_org("cto")
    _seed(theirs["org_id"], HUB, "Other Capability")
    return {"ours": ours, "theirs": theirs}


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_every_row_of_the_answer_says_how_healthy_it_is(browser, live_server, organisations, name, viewport, tmp_path):
    context = browser.new_context(viewport=viewport, color_scheme="dark" if viewport["width"] < 600 else "light")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, organisations["ours"]["emails"]["cto"])
        open_chat(page, live_server)
        answer = ask(page, live_server, "What breaks if %s fails?" % HUB, fresh=False)
        rows = answer.locator("[data-slot='card']:has([data-answer-row])")
        expect(rows).to_have_count(3)

        def row_of(item):
            return rows.filter(has_text=item).first

        expect(row_of(CAPABILITY)).to_contain_text("Not assessed")
        expect(row_of(CAPABILITY).get_by_label("Health: Not assessed")).to_be_visible()
        expect(row_of("Ledger Core")).to_contain_text("At risk")
        expect(row_of("Quay Archive")).to_contain_text("Health not recorded")
        assert "Other Capability" not in answer.text_content()

        shots = os.environ.get("SMOKE_SCREENSHOT_DIR") or str(tmp_path)
        page.screenshot(path=os.path.join(shots, "what-breaks-health-%s.png" % name), full_page=True)
    finally:
        context.close()
