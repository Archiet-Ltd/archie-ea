"""The chat answers "what can our own product do?" from the organisation's model, with the
recorded status and limits, and says "not recorded" instead of guessing.

The person: an architect at a company that sells services built on its own products, Entelim and
Ifiok, working out which jobs it can honestly bid for. The organisation's model holds both
products and what each can and cannot do (loaded from product files); another organisation
holds a product of its own.

The goal, at 1440 light and 390 dark:
- "Can Entelim export a canvas?" is answered "does not do this yet", with the recorded status and
  what exists, and the source link opens the capability's page with that status on it;
- "What does Ifiok's GitHub import do?" is answered with its shipped status, what it does and its
  known limits, and the source link opens a page with content;
- "Can Entelim connect GitHub?" is answered from the shipped Entelim file with its limits;
- a capability nobody recorded is answered "Not recorded in your model", names no capability,
  and cites the product's own page, which has content.
"""
import json
import os

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, open_chat, sign_in
from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("CHAT_ANSWER_WALK_DIR")
IFIOK = {
    "product": "Ifiok",
    "as_of": "2026-11-15T00:00:00Z",
    "capabilities": [
        {"id": "github-import", "name": "Import from GitHub", "status": "shipped",
         "what_a_person_can_do": "Reads the repositories you pick and lists the services they contain.",
         "limits": ["private repositories of other accounts", "GitLab"]},
        {"id": "invoicing", "name": "Send invoices", "status": "not shipped", "what_exists": "A draft screen only."},
    ],
}


@pytest.fixture(scope="module")
def server(request, app):
    return boot_live_server(request, None, app)


def _seed(org_id, other_org_id, folder):
    from app import create_app, db
    from app.services import customer_zero_model as model

    path = os.path.join(folder, "ifiok.json")
    with open(path, "w") as handle:
        json.dump(IFIOK, handle)
    other = os.path.join(folder, "zephyr.json")
    with open(other, "w") as handle:
        json.dump({"product": "Zephyr", "capabilities": [
            {"id": "tp", "name": "Teleport files", "status": "shipped", "what_a_person_can_do": "Moves files."}]}, handle)
    app = create_app("testing")
    with app.app_context():
        model.load_product(model.DATA_DIRECTORY / "entelim.json", org_id)
        model.load_product(path, org_id)
        model.load_product(other, other_org_id)
        db.session.remove()


@pytest.fixture(scope="module")
def organisation(server, tmp_path_factory):
    made = create_fresh_org("enterprise_architect", org_admin=True)
    other = create_fresh_org("enterprise_architect", org_admin=True)
    _seed(made["org_id"], other["org_id"], str(tmp_path_factory.mktemp("products")))
    return made


def _shot(page, tmp_path, name):
    folder = WALK or str(tmp_path)
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, name))


CASES = (
    ("1440-light", {"width": 1440, "height": 900}, "light"),
    ("390-dark", {"width": 390, "height": 844}, "dark"),
)


@pytest.mark.parametrize("label,viewport,scheme", CASES, ids=[c[0] for c in CASES])
def test_the_chat_answers_what_our_products_can_do_from_the_model(
    browser, server, organisation, tmp_path, label, viewport, scheme
):
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, server, organisation["emails"]["enterprise_architect"])
        open_chat(page, server)

        # A claim that is not shipped is said not to be done yet, with what is recorded.
        answer = ask(page, server, "Can Entelim export a canvas?", fresh=False)
        assert answer.get_by_test_id("product-status").inner_text().strip() == "Not shipped"
        sentence = answer.get_by_test_id("product-sentence").inner_text()
        assert "Entelim does not do this yet" in sentence and "Recorded as not proven" in sentence
        assert "public menu" in answer.get_by_test_id("product-does").inner_text()
        _shot(page, tmp_path, "own-products-canvas-%s.png" % label)
        href = answer.get_by_test_id("product-source").get_attribute("href")
        page.goto(server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Export a business model canvas")
        expect(page.locator("main")).to_contain_text("Not shipped (not proven)")

        # A shipped capability of the second product says what it does and what it does not.
        open_chat(page, server)
        answer = ask(page, server, "What does Ifiok's GitHub import do?", fresh=False)
        assert answer.get_by_test_id("product-status").inner_text().strip() == "Shipped"
        assert "Ifiok does this" in answer.get_by_test_id("product-sentence").inner_text()
        assert "lists the services they contain" in answer.get_by_test_id("product-does").inner_text()
        limits = answer.get_by_test_id("product-limits").inner_text()
        assert "private repositories of other accounts" in limits and "GitLab" in limits
        _shot(page, tmp_path, "own-products-github-import-%s.png" % label)
        href = answer.get_by_test_id("product-source").get_attribute("href")
        page.goto(server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Ifiok: Import from GitHub")
        expect(page.locator("main")).to_contain_text("Reads the repositories you pick")

        # The list that ships with the product answers too.
        open_chat(page, server)
        answer = ask(page, server, "Can Entelim connect GitHub?", fresh=False)
        assert answer.get_by_test_id("product-status").inner_text().strip() == "Shipped"
        assert "GitLab, Bitbucket or Azure DevOps" in answer.get_by_test_id("product-limits").inner_text()

        # Nothing recorded: said so, nothing invented, and the cited page has content.
        open_chat(page, server)
        answer = ask(page, server, "Can Entelim do blockchain?", fresh=False)
        text = answer.inner_text()
        assert "Not recorded in your model" in text
        assert "blockchain" not in answer.get_by_test_id("product-sentence").inner_text().lower()
        assert answer.get_by_test_id("product-status").count() == 0
        _shot(page, tmp_path, "own-products-not-recorded-%s.png" % label)
        href = answer.get_by_test_id("product-source").get_attribute("href")
        page.goto(server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Entelim")
    finally:
        context.close()
