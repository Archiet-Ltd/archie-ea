"""A person asks the chat who owns an application and can see where that answer comes from.

The person: an enterprise architect in an organisation whose application "Ifiok" has a named
primary owner. A second organisation has its own "Ifiok" with a different owner. The goal: ask
"Who owns Ifiok?", read the owner and the day the ownership was recorded, follow the source
link under the answer to the page where the owner is recorded and find the same owner there;
and never be able to open the other organisation's record.
"""
import os
import uuid
from datetime import datetime, timezone

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
OWNERS = {"A": ("Nneka", "Okafor"), "B": ("Tobias", "Wren")}


def _seed(org_id, first, last):
    """An application called Ifiok in the organisation, owned by the named person."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.services.element_ownership_service import set_element_owner

    app = create_app("testing")
    with app.app_context():
        element = ArchiMateElement(name="Ifiok", type="ApplicationComponent", layer="application", organization_id=org_id)
        db.session.add(element)
        db.session.flush()
        component = ApplicationComponent(name="Ifiok", organization_id=org_id, archimate_element_id=element.id)
        owner = User(
            email="%s.%s@example.com" % (first.lower(), uuid.uuid4().hex[:8]), first_name=first, last_name=last,
            organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
        )
        db.session.add_all([component, owner])
        db.session.flush()
        set_element_owner(
            element_type="application", element_id=component.id, user_id=owner.id,
            organization_id=org_id, ownership_type="primary", commit=False,
        )
        db.session.commit()
        component_id = component.id
        db.session.remove()
    return component_id


@pytest.fixture(scope="module")
def organisations(live_server):
    made = {}
    for key, (first, last) in OWNERS.items():
        org = create_fresh_org("enterprise_architect", org_admin=False)
        org["component_id"] = _seed(org["org_id"], first, last)
        made[key] = org
    return made


@pytest.mark.parametrize("name,viewport,scheme", SIZES, ids=[s[0] for s in SIZES])
def test_the_owner_answer_cites_the_ownership_record_and_the_link_lands_on_it(
    browser, live_server, organisations, tmp_path, name, viewport, scheme
):
    walk = os.environ.get("CHAT_ROUTE_WALK_DIR") or str(tmp_path)
    os.makedirs(walk, exist_ok=True)
    mine, theirs = organisations["A"], organisations["B"]
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        open_chat(page, live_server)
        answer = ask(page, live_server, "Who owns Ifiok?", fresh=False)

        # The owner, and the day the ownership was recorded.
        expect(answer).to_contain_text("Nneka Okafor")
        today = datetime.now(timezone.utc)
        recorded = "%d %s" % (today.day, today.strftime("%b %Y"))
        expect(answer.locator("[data-owner-since]")).to_have_text("Since " + recorded)
        assert "No contact recorded" not in answer.inner_text()

        # The answer says where it comes from: the ownership record, its origin and its date.
        footer = answer.get_by_test_id("result-footer")
        expect(footer).to_be_visible()
        expect(footer.get_by_test_id("result-footer-evidence")).to_contain_text("Ownership record: Ifiok")
        expect(footer.get_by_test_id("result-footer-source")).to_contain_text("Ownership record: entered by the team")
        expect(footer.get_by_test_id("result-footer-date")).to_contain_text(recorded)
        assert "Tobias" not in answer.inner_text() and "Wren" not in answer.inner_text()
        page.screenshot(path=os.path.join(walk, "chat-owner-source-%s.png" % name))

        # The source link opens the page where the owner is recorded, with that owner on it.
        link = footer.get_by_test_id("result-footer-evidence").get_by_role("link", name="Ownership record: Ifiok")
        href = link.get_attribute("href")
        assert href == "/applications/%d" % mine["component_id"], href
        link.click()
        page.wait_for_url("**" + href, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Ifiok")
        expect(page.locator("body")).to_contain_text("Nneka Okafor")
        assert "Tobias Wren" not in page.locator("body").inner_text()
        page.screenshot(path=os.path.join(walk, "owner-record-page-%s.png" % name))

        # The other organisation's record is not reachable from here.
        other = page.goto(live_server + "/applications/%d" % theirs["component_id"], wait_until="domcontentloaded",
                          timeout=PAGE_TIMEOUT)
        assert other.status == 404, other.status
        assert "Tobias Wren" not in page.locator("body").inner_text()
    finally:
        context.close()
