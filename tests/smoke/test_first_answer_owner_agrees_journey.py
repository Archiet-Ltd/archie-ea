"""A chief technology officer asks what breaks if the hub fails and the owners in the answer agree with the coverage page.

The hub serves four applications: one owned by a name typed on the application, one owned by a person
recorded in the ownership record, and two nobody owns. The answer says "No owner recorded" for exactly the
two nobody owns, and the ownership coverage page counts the same two as the applications without an owner.
A second organisation with its own unowned application never changes either figure.
"""

import re
import uuid

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
TYPED = "Tidewater Ledger"
RECORDED = "Marlow Payroll"
BARE = ("Harbour Portal", "Quay Archive")


def _seed(org_id, hub, apps):
    """The hub and the connected applications. ``apps`` maps a name to "typed", "recorded" or None."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User
    from app.services.element_ownership_service import set_element_owner

    app = create_app("testing")
    with app.app_context():
        def make(name, how):
            element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
            db.session.add(element)
            db.session.flush()
            component = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id,
                                             lifecycle_status="active")
            if how == "typed":
                component.legacy_application_owner = "Ada Lovelace"
            db.session.add(component)
            db.session.flush()
            if how == "recorded":
                person = User(email="nneka.%s@example.com" % uuid.uuid4().hex[:8], first_name="Nneka", last_name="Okafor",
                              organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect")
                db.session.add(person)
                db.session.flush()
                set_element_owner(element_type="application", element_id=component.id, user_id=person.id,
                                  organization_id=org_id, ownership_type="primary", commit=False)
            return element

        centre = make(hub, "typed")
        for name, how in apps.items():
            db.session.add(ArchiMateRelationship(type="Serving", source_id=centre.id, target_id=make(name, how).id,
                                                 organization_id=org_id))
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def organisations(live_server):
    ours = create_fresh_org("cto")
    _seed(ours["org_id"], HUB, {TYPED: "typed", RECORDED: "recorded", BARE[0]: None, BARE[1]: None})
    theirs = create_fresh_org("cto")
    _seed(theirs["org_id"], HUB, {"Other Unowned One": None})
    return {"ours": ours, "theirs": theirs}


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_the_answer_and_the_coverage_page_count_the_same_unowned_applications(
    browser, live_server, organisations, name, viewport
):
    ours = organisations["ours"]
    context = browser.new_context(viewport=viewport, color_scheme="dark" if viewport["width"] < 600 else "light")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, ours["emails"]["cto"])
        open_chat(page, live_server)
        answer = ask(page, live_server, "What breaks if %s fails?" % HUB, fresh=False)
        rows = answer.locator("[data-answer-row]")
        expect(rows).to_have_count(4)

        def owner_of(application):
            row = answer.locator("[data-slot='card']:has([data-answer-row])").filter(has_text=application).first
            return re.sub(r"\s+", " ", row.text_content())

        assert "Ada Lovelace" in owner_of(TYPED)
        assert "Nneka Okafor" in owner_of(RECORDED)
        for bare in BARE:
            assert "No owner recorded" in owner_of(bare)
        said_none = answer.text_content().count("No owner recorded")
        assert said_none == len(BARE), said_none

        page.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        main = page.locator("main")
        total = int(main.get_by_text("Total applications").locator("xpath=following-sibling::p[1]").inner_text())
        owned = int(main.get_by_text("With named owner").locator("xpath=following-sibling::p[1]").inner_text())
        # The hub and two connections are owned, two are not; the other organisation's application is not counted.
        assert (total, owned) == (5, 3), (total, owned)
        assert total - owned == said_none
    finally:
        context.close()
