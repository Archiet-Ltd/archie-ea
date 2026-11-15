"""An enterprise architect asks who owns an application that serves two others, and the answer speaks only of it.

The application and the two it serves each have their own recorded owner. The one-sentence answer names the
asked application's owner once, its result footer cites only that application's ownership record, and every
"Since" line sits next to a named owner. A second organisation's same-named application never appears.
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
ASKED = "Payments service"
DOWNSTREAM = {"Customer web app": ("Rhea", "Mbeki"), "Order management system": ("Tobias", "Wren")}


def _seed(org_id, owner_first, owner_last, downstream):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User
    from app.services.element_ownership_service import set_element_owner

    app = create_app("testing")
    with app.app_context():
        def make(name, first, last):
            element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
            db.session.add(element)
            db.session.flush()
            component = ApplicationComponent(name=name, organization_id=org_id, archimate_element_id=element.id,
                                             lifecycle_status="active")
            person = User(email="%s.%s@example.com" % (first.lower(), uuid.uuid4().hex[:8]), first_name=first,
                          last_name=last, organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect")
            db.session.add_all([component, person])
            db.session.flush()
            set_element_owner(element_type="application", element_id=component.id, user_id=person.id,
                              organization_id=org_id, ownership_type="primary", commit=False)
            return element

        centre = make(ASKED, owner_first, owner_last)
        for name, (first, last) in downstream.items():
            db.session.add(ArchiMateRelationship(type="Serving", source_id=centre.id, target_id=make(name, first, last).id,
                                                 organization_id=org_id))
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def organisations(live_server):
    ours = create_fresh_org("enterprise_architect", org_admin=False)
    _seed(ours["org_id"], "Nneka", "Okafor", DOWNSTREAM)
    theirs = create_fresh_org("enterprise_architect", org_admin=False)
    _seed(theirs["org_id"], "Imani", "Costa", {"Other Billing App": ("Wale", "Idowu")})
    return {"ours": ours, "theirs": theirs}


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_the_owner_answer_and_its_footer_speak_only_of_the_application_asked_about(
    browser, live_server, organisations, name, viewport
):
    context = browser.new_context(viewport=viewport, color_scheme="dark" if viewport["width"] < 600 else "light")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, organisations["ours"]["emails"]["enterprise_architect"])
        open_chat(page, live_server)
        answer = ask(page, live_server, "Who owns %s?" % ASKED, fresh=False)

        sentence = answer.get_by_test_id("owner-sentence")
        expect(sentence).to_contain_text("Nneka Okafor")
        text = re.sub(r"\s+", " ", sentence.inner_text())
        assert not re.search(r"\)\s*\(", text), text

        evidence = answer.get_by_test_id("result-footer").get_by_test_id("result-footer-evidence")
        expect(evidence).to_contain_text("Ownership record: %s" % ASKED)
        for other in DOWNSTREAM:
            assert other not in evidence.inner_text(), evidence.inner_text()

        records = answer.locator("[data-owner-record]")
        assert records.count() >= 1
        for i in range(records.count()):
            record = records.nth(i)
            expect(record.locator("[data-owner-since-name]")).to_have_text("Nneka Okafor")
            expect(record.locator("[data-owner-since]")).to_contain_text("Since ")
        everything = answer.inner_text()
        for stranger in ("Rhea", "Tobias", "Imani", "Wale", "Other Billing App"):
            assert stranger not in everything, stranger
    finally:
        context.close()
