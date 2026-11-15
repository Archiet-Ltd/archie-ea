"""A person asks the chat for the rationalization status of a part of an application and is
answered with the application it belongs to.

The person: a solution architect in an organisation whose application "MarketingIQ" has a
portfolio record and is made of "MarketingIQ — backend (API and workers)", a component with
no record of its own. The goal: ask about the part, read that its figures are the
application's, and open the application's planning page from the answer and find it there.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, open_chat, sign_in
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = (
    ("desktop-light", {"width": 1440, "height": 900}, "light"),
    ("phone-dark", {"width": 390, "height": 844}, "dark"),
)


def _seed(org_id, whole_name, part_name):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    with app.app_context():
        whole = ArchiMateElement(name=whole_name, type="ApplicationComponent", layer="application", organization_id=org_id)
        part = ArchiMateElement(name=part_name, type="ApplicationComponent", layer="application", organization_id=org_id)
        db.session.add_all([whole, part])
        db.session.flush()
        record = ApplicationComponent(name=whole_name, organization_id=org_id, archimate_element_id=whole.id)
        db.session.add(record)
        db.session.add(
            ArchiMateRelationship(type="composition", source_id=whole.id, target_id=part.id, organization_id=org_id)
        )
        db.session.commit()
        record_id = record.id
        db.session.remove()
    return record_id


@pytest.fixture(scope="module")
def organisation(live_server):
    suffix = uuid.uuid4().hex[:6]
    whole = "MarketingIQ %s" % suffix
    part = "%s — backend (API and workers)" % whole
    made = create_fresh_org("solution_architect", org_admin=False)
    made.update(whole=whole, part=part, record_id=_seed(made["org_id"], whole, part))
    return made


@pytest.mark.parametrize("name,viewport,scheme", SIZES, ids=[s[0] for s in SIZES])
def test_a_part_is_answered_with_its_application_and_the_link_opens_it(
    browser, live_server, organisation, tmp_path, name, viewport, scheme
):
    walk = os.environ.get("CHAT_PART_WALK_DIR") or str(tmp_path)
    os.makedirs(walk, exist_ok=True)
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, organisation["emails"]["solution_architect"])
        open_chat(page, live_server)
        card = ask(page, live_server, "What is the rationalization status of %s" % organisation["part"], fresh=False)

        sentence = card.get_by_test_id("part-of-sentence")
        expect(sentence).to_have_text(
            "%s is part of %s; figures are %s's." % (organisation["part"], organisation["whole"], organisation["whole"])
        )
        link = card.get_by_test_id("lens-open-planning")
        expect(link).to_be_visible()
        assert link.get_attribute("href") == "/applications/rationalization/planning/%s" % organisation["record_id"]
        page.screenshot(path=os.path.join(walk, "chat-part-answer-%s.png" % name))

        page.goto(live_server + link.get_attribute("href"), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(organisation["whole"])
        page.screenshot(path=os.path.join(walk, "chat-part-planning-%s.png" % name))
    finally:
        context.close()
