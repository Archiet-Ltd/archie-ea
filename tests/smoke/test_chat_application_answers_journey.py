"""The chat's answers about applications say only what they show, count only what is in use, and
answer an owner question in a sentence.

The person: a CTO's team in an organisation with three applications, one of them decommissioned,
each with an owner and an annual cost. The Administrator may read cost; a business architect has no
spend authority. Another organisation has applications of its own that must never appear.

The server is set up as production is, with the existing DeepSeek stub as the provider: asked which
catalogue entry fits "Which applications do we have?", it names the inventory entry, as the model did
on the walk; the owner question is answered from the records before any model is asked.

The goal, at 1440 light as the Administrator and at 390 dark as the person without spend authority:
- "Which applications do we have?" is answered by a sentence that names the owner (and the annual
  cost only for the person who may read it), and every entry of the list carries what the sentence names;
- the count in the sentence is the applications list's own count of applications in use, and the
  sentence says how many decommissioned ones it leaves out;
- "Who owns <application>?" is answered in one sentence naming the owner and linking the page where it
  is recorded, with no line about data that is not connected;
- the link opens a page with content, and the other organisation's applications are never named.
"""
import json
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .chat_ask import ask, open_chat, sign_in
from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("CHAT_ANSWER_WALK_DIR")
LIST_QUESTION = "Which applications do we have?"
OWNER_QUESTION = "Who owns Ledger Core?"
IN_USE = ("Ledger Core", "Billing Hub")
GONE = "Old Archive"
FOREIGN = "Harbour Payroll"
STUB_REPLY = "Nothing in your records is called that."


OTHER_PROVIDERS = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")


def _provider(model, text, has_tools):
    """Which catalogue entry fits the inventory question: the inventory entry."""
    if "Map the user's question onto exactly one of these catalogue entries" in text and LIST_QUESTION in text:
        return json.dumps({"entry_id": "applications_and_owners", "params": {}})
    return None


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply=STUB_REPLY, respond=_provider)
    yield s
    s.close()


@pytest.fixture(scope="module")
def server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHER_PROVIDERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _seed(org_id, other_org_id):
    """Three applications in the organisation (one decommissioned) and one in another, through the models."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.billing_plans import set_contract_plan
    from app.services.element_ownership_service import set_element_owner

    app = create_app("testing")
    with app.app_context():
        set_contract_plan(db.session.get(Organization, org_id), "enterprise", None)
        db.session.commit()
        element = ArchiMateElement(
            name="Ledger Core", type="ApplicationComponent", layer="application", organization_id=org_id
        )
        db.session.add(element)
        db.session.flush()
        ledger = ApplicationComponent(
            name="Ledger Core", organization_id=org_id, archimate_element_id=element.id,
            total_cost_of_ownership=12000, lifecycle_status="2.1 Strategic",
        )
        owner = User(
            email="nneka.%s@example.com" % uuid.uuid4().hex[:8], first_name="Nneka", last_name="Okafor",
            organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
        )
        technical = User(
            email="cameron.%s@example.com" % uuid.uuid4().hex[:8], first_name="Cameron", last_name="Doyle",
            organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
        )
        db.session.add_all([ledger, owner, technical])
        db.session.flush()
        set_element_owner(
            element_type="application", element_id=ledger.id, user_id=owner.id,
            organization_id=org_id, ownership_type="primary", commit=False,
        )
        set_element_owner(
            element_type="application", element_id=ledger.id, user_id=technical.id,
            organization_id=org_id, ownership_type="technical", commit=False,
        )
        db.session.add(ApplicationComponent(
            name="Billing Hub", organization_id=org_id, application_owner="Ada Obi",
            total_cost_of_ownership=4500, lifecycle_status="2.2 Tactical",
        ))
        db.session.add(ApplicationComponent(
            name=GONE, organization_id=org_id, application_owner="Tunde Bello",
            total_cost_of_ownership=900, lifecycle_status="5. Decommissioned",
        ))
        db.session.add(ApplicationComponent(
            name=FOREIGN, organization_id=other_org_id, application_owner="Zed Foreign",
            total_cost_of_ownership=777, lifecycle_status="2.1 Strategic",
        ))
        db.session.commit()
        ids = {"ledger": ledger.id}
        db.session.remove()
        return ids


@pytest.fixture(scope="module")
def organisation(server):
    made = create_fresh_org("enterprise_architect", org_admin=True, extra_roles=("business_architect",))
    other = create_fresh_org("enterprise_architect", org_admin=True)
    made["other"] = other
    made["ids"] = _seed(made["org_id"], other["org_id"])
    return made


def _shot(page, tmp_path, name):
    folder = WALK or str(tmp_path)
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, name))


def _new_page(browser, viewport, scheme):
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    return context, context.new_page()


def _list_count(page, base):
    """The applications list's own count of applications in use, and of decommissioned ones."""
    page.goto(base + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    badge = page.get_by_test_id("total-count-badge")
    expect(badge).to_be_visible(timeout=PAGE_TIMEOUT)
    shown = int(re.match(r"(\d+) shown", badge.inner_text().strip()).group(1))
    decom = int(page.get_by_test_id("decom-count-label").inner_text().strip())
    return shown, decom


CASES = (
    ("administrator", "enterprise_architect", {"width": 1440, "height": 900}, "light", True),
    ("no-spend", "business_architect", {"width": 390, "height": 844}, "dark", False),
)


@pytest.mark.parametrize("label,persona,viewport,scheme,sees_cost", CASES, ids=[c[0] for c in CASES])
def test_the_applications_answer_says_only_what_it_shows_and_counts_what_is_in_use(
    browser, server, organisation, tmp_path, label, persona, viewport, scheme, sees_cost
):
    context, page = _new_page(browser, viewport, scheme)
    try:
        sign_in(page, server, organisation["emails"][persona])
        in_use, decommissioned = _list_count(page, server)
        assert (in_use, decommissioned) == (2, 1), "the list itself should show 2 in use and 1 decommissioned"

        answer = ask(page, server, LIST_QUESTION)
        sentence = answer.get_by_test_id("catalogue-sentence").inner_text()
        assert "%d applications in use" % in_use in sentence, sentence
        assert "%d decommissioned not counted" % decommissioned in sentence, sentence
        assert "accountable owner" in sentence
        # The sentence names the annual cost only where the list below carries it.
        assert ("annual cost" in sentence) is sees_cost, sentence

        entries = answer.locator("[data-catalogue-record]")
        assert entries.count() == in_use, "the list holds %d entries, the sentence says %d" % (entries.count(), in_use)
        text = answer.inner_text()
        assert GONE not in text and FOREIGN not in text
        for entry_index in range(entries.count()):
            detail = entries.nth(entry_index).get_by_test_id("catalogue-open").inner_text() + " " + \
                entries.nth(entry_index).locator("[data-catalogue-detail]").inner_text()
            assert "Owner: " in detail and "not recorded" not in detail.split("Owner: ")[1].split(" · ")[0], detail
            assert (("Annual cost: " in detail) is sees_cost), detail
        assert "Nneka Okafor" in text and "Ada Obi" in text
        if sees_cost:
            assert "12,000" in text and "4,500" in text
        else:
            assert "12,000" not in text and "4,500" not in text and "cost" not in text.lower()
        _shot(page, tmp_path, "chat-applications-%s.png" % label)

        # Every entry opens its application's page, with content.
        href = entries.first.get_by_test_id("catalogue-open").get_attribute("href")
        page.goto(server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Billing Hub")
    finally:
        context.close()


@pytest.mark.parametrize("label,persona,viewport,scheme,sees_cost", CASES, ids=[c[0] for c in CASES])
def test_an_owner_question_is_answered_in_one_sentence_with_its_source(
    browser, server, organisation, tmp_path, label, persona, viewport, scheme, sees_cost
):
    context, page = _new_page(browser, viewport, scheme)
    try:
        sign_in(page, server, organisation["emails"][persona])
        open_chat(page, server)
        answer = ask(page, server, OWNER_QUESTION, fresh=False)
        sentence = answer.get_by_test_id("owner-sentence")
        expect(sentence).to_have_text(
            "Ledger Core is owned by Nneka Okafor (primary) and Cameron Doyle (technical), recorded on the Ledger Core application page."
        )
        text = answer.inner_text()
        assert "not yet connected" not in text and "Capacity" not in text, text
        _shot(page, tmp_path, "chat-owner-%s.png" % label)

        # The source link opens the application's page, which shows the application.
        link = answer.get_by_test_id("owner-source")
        assert link.get_attribute("href") == "/applications/%d" % organisation["ids"]["ledger"]
        page.goto(server + link.get_attribute("href"), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Ledger Core")
        expect(page.locator("main")).to_contain_text("Nneka Okafor")
    finally:
        context.close()


@pytest.mark.parametrize("label,persona,viewport,scheme,sees_cost", CASES, ids=[c[0] for c in CASES])
def test_three_owners_read_as_a_b_and_c(
    browser, server, organisation, tmp_path, label, persona, viewport, scheme, sees_cost
):
    """Three owners read as "A, B and C", each with its role."""
    from app import create_app, db
    from app.models.user import User
    from app.services.element_ownership_service import set_element_owner

    app = create_app("testing")
    with app.app_context():
        ledger_id = organisation["ids"]["ledger"]
        samir = User(
            email="samir.%s@example.com" % uuid.uuid4().hex[:8], first_name="Samir", last_name="Patel",
            organization_id=organisation["org_id"], confirmed=True, enterprise_role="enterprise_architect",
        )
        db.session.add(samir)
        db.session.flush()
        set_element_owner(
            element_type="application", element_id=ledger_id, user_id=samir.id,
            organization_id=organisation["org_id"], ownership_type="business", commit=False,
        )
        db.session.commit()
        db.session.remove()

    context, page = _new_page(browser, viewport, scheme)
    try:
        sign_in(page, server, organisation["emails"][persona])
        open_chat(page, server)
        answer = ask(page, server, OWNER_QUESTION, fresh=False)
        sentence = answer.get_by_test_id("owner-sentence")
        expect(sentence).to_have_text(
            "Ledger Core is owned by Nneka Okafor (primary), Cameron Doyle (technical) and Samir Patel (business), "
            "recorded on the Ledger Core application page."
        )
        text = answer.inner_text()
        assert "not yet connected" not in text and "Capacity" not in text, text
        _shot(page, tmp_path, "chat-owner-three-%s.png" % label)

        link = answer.get_by_test_id("owner-source")
        assert link.get_attribute("href") == "/applications/%d" % organisation["ids"]["ledger"]
        page.goto(server + link.get_attribute("href"), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Ledger Core")
    finally:
        context.close()


def test_another_organisations_chat_answers_from_its_own_applications_only(browser, server, organisation, tmp_path):
    other = organisation["other"]
    context, page = _new_page(browser, {"width": 1440, "height": 900}, "light")
    try:
        sign_in(page, server, other["emails"]["enterprise_architect"])
        answer = ask(page, server, LIST_QUESTION)
        sentence = answer.get_by_test_id("catalogue-sentence").inner_text()
        assert "1 application in use" in sentence and "decommissioned" not in sentence, sentence
        text = answer.inner_text()
        assert FOREIGN in text
        for name in IN_USE + (GONE,):
            assert name not in text, text
        assert "Nneka Okafor" not in text and "Ada Obi" not in text

        # Ledger Core is not this organisation's: no card names its owner, and the reply is in words.
        page.locator("#user-input").fill(OWNER_QUESTION)
        page.locator("#user-input").press("Enter")
        expect(page.get_by_text(STUB_REPLY)).to_have_count(1, timeout=PAGE_TIMEOUT * 2)
        assert page.get_by_test_id("estate-answer").count() == 1
        assert "Nneka Okafor" not in page.locator("#messages-container").inner_text()
        _shot(page, tmp_path, "chat-applications-other-organisation.png")
    finally:
        context.close()
