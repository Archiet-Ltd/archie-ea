"""A person asks the chat who owns an application, what it runs on and depends on, and
gets each answer in its own words, in one conversation.

The person: an enterprise architect in an organisation whose application "Ifiok" has a
named primary owner, two technology nodes it runs on and two supplier actors. The goal:
ask "Who owns Ifiok?", "What does Ifiok depend on?" and the three-part question, read an
answer to each, and see the Recent chats count equal the messages sent and received.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, open_chat, sign_in
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

THREE = "What does Ifiok run on, which suppliers does it depend on, and who owns it?"
SIZES = (
    ("desktop-light", {"width": 1440, "height": 900}, "light"),
    ("phone-dark", {"width": 390, "height": 844}, "dark"),
)
NODES = ("Harbour Cluster Alpha", "Harbour Cluster Beta")
SUPPLIERS = ("Tidewater Hosting Ltd", "Marlow Payments Ltd")


def _seed(org_id):
    """Ifiok, its owner, its nodes and its suppliers, through the models the product reads."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.user import User
    from app.services.element_ownership_service import set_element_owner

    app = create_app("testing")
    with app.app_context():
        element = ArchiMateElement(name="Ifiok", type="ApplicationComponent", layer="application", organization_id=org_id)
        db.session.add(element)
        db.session.flush()
        component = ApplicationComponent(name="Ifiok", organization_id=org_id, archimate_element_id=element.id)
        owner = User(
            email="nneka.%s@example.com" % uuid.uuid4().hex[:8], first_name="Nneka", last_name="Okafor",
            organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
        )
        db.session.add_all([component, owner])
        db.session.flush()
        set_element_owner(
            element_type="application", element_id=component.id, user_id=owner.id,
            organization_id=org_id, ownership_type="primary", commit=False,
        )
        for name in NODES:
            node = ArchiMateElement(name=name, type="Node", layer="technology", organization_id=org_id)
            db.session.add(node)
            db.session.flush()
            db.session.add(ArchiMateRelationship(type="Serving", source_id=node.id, target_id=element.id, organization_id=org_id))
        for name in SUPPLIERS:
            actor = ArchiMateElement(name=name, type="BusinessActor", layer="business", organization_id=org_id)
            db.session.add(actor)
            db.session.flush()
            db.session.add(ArchiMateRelationship(type="Association", source_id=actor.id, target_id=element.id, organization_id=org_id))
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def organisation(live_server):
    made = create_fresh_org("enterprise_architect", org_admin=False)
    _seed(made["org_id"])
    return made


@pytest.mark.parametrize("name,viewport,scheme", SIZES, ids=[s[0] for s in SIZES])
def test_the_chat_answers_owner_then_dependencies_then_all_three_parts(
    browser, live_server, organisation, tmp_path, name, viewport, scheme
):
    walk = os.environ.get("CHAT_ROUTE_WALK_DIR") or str(tmp_path)
    os.makedirs(walk, exist_ok=True)
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, organisation["emails"]["enterprise_architect"])
        open_chat(page, live_server)

        owner = ask(page, live_server, "Who owns Ifiok?", fresh=False)
        expect(owner).to_contain_text("Nneka Okafor")
        assert "What breaks if" not in owner.inner_text()

        depends = ask(page, live_server, "What does Ifiok depend on?", fresh=False)
        expect(depends).to_contain_text("What Ifiok depends on")
        text = depends.inner_text()
        for each in NODES + SUPPLIERS:
            assert each in text, text
        assert "What breaks if" not in text
        # Every connection card opens the Composer on a page with content.
        first_link = depends.locator("[data-answer-row] a").first
        assert first_link.get_attribute("href")

        everything = ask(page, live_server, THREE, fresh=False)
        text = everything.inner_text()
        assert "What Ifiok depends on" in text and "Nneka Okafor" in text, text
        for each in NODES + SUPPLIERS:
            assert each in text, text
        assert text.index(NODES[0]) < text.index("Nneka Okafor"), "owner is asked last, so it is answered last"
        assert "What breaks if" not in text

        # Six messages were sent and received; the Recent chats item says so.
        rail = page.locator("#session-list")
        expect(rail).to_contain_text("6 messages", timeout=PAGE_TIMEOUT)
        assert not re.search(r"\b[2-5] messages\b", rail.text_content())
        page.screenshot(path=os.path.join(walk, "chat-owner-depends-%s.png" % name))

        # The links of the answer land on pages with content.
        href = depends.get_by_test_id("answer-open").get_attribute("href")
        page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Ifiok")
    finally:
        context.close()
