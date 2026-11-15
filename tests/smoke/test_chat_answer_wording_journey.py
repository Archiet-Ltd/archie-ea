"""A person asks the chat to link a risk to an application and reads the answer in plain words.

The person: an enterprise architect. The goal: read what the assistant did and open the page it
points at. The model sends back its own internal words (a "genome patch", a raw field, a tool name,
database text, a path in backticks); the person must read none of them, and the page named in the
answer must open with content. Checked at 1440 light and 390 dark, with a second organisation
present so the answer is shown to be about the signed-in one.
"""
import json
import os
import re
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from . import fresh_org
from .chat_ask import open_chat, sign_in
from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"
WALK = os.environ.get("SMOKE_SCREENSHOT_DIR") or os.path.join(tempfile.gettempdir(), "chat-wording-walk")
QUESTION = "Link the risk Data loss to the application %s"
RAW_REPLY = (
    "I prepared a genome patch to link Data loss to %s. The lookup `explain_element` found it with "
    "`supported: false` and `supported_by` empty, status `in_progress`. The save failed: "
    'null value in column "organization_id" of relation "risk_application_links" violates not-null constraint. '
    "Open `/solutions` to see the rest, not `/made-up-page`, or send it to the ARB."
)


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply="placeholder")
    yield s
    s.close()


@pytest.fixture(scope="module")
def server(request, app, stub):
    others = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")
    env = {name + "_API_KEY": "" for name in others}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _seed_application(org_id, name):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        db.session.add(ApplicationComponent(name=name, organization_id=org_id, description="Core finance application",
                                            deployment_status="development", lifecycle_status="operational"))
        db.session.commit()
        db.session.remove()


@pytest.fixture
def orgs():
    out = []
    for label in ("Ledger", "Billing"):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org["application"] = "%s %s" % (label, uuid.uuid4().hex[:5])
        _seed_application(org["org_id"], org["application"])
        out.append(org)
    return out


@pytest.fixture(params=[(1440, 900, "light"), (390, 844, "dark")], ids=["1440-light", "390-dark"])
def page(browser, request):
    width, height, scheme = request.param
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    pg.shot_tag = "%d-%s" % (width, scheme)
    yield pg
    ctx.close()


def test_the_answer_has_no_internal_words_and_its_page_link_opens(page, server, stub, orgs):
    mine, theirs = orgs
    stub.reply = RAW_REPLY % mine["application"]
    sign_in(page, server, mine["emails"][ARCHITECT])
    open_chat(page, server)
    box = page.locator("#user-input")
    box.fill(QUESTION % mine["application"])
    box.press("Enter")
    answer = page.locator(".message-content").last
    expect(answer).to_contain_text("proposed change to your model", timeout=PAGE_TIMEOUT)
    text = answer.inner_text()
    lowered = text.lower()
    for word in ("genome", "explain_element", "supported:", "supported_by", "in_progress", "null value",
                 "organization_id", "risk_application_links", "constraint", "`", "/solutions"):
        assert word not in lowered, (word, text)
    assert "ARB" not in text.replace("Architecture Review Board", "")
    assert "not supported" in text and "in progress" in text and "nothing was changed" in text
    assert mine["application"] in text
    assert theirs["application"] not in page.locator("main").inner_text()
    os.makedirs(WALK, exist_ok=True)
    page.screenshot(path="%s/answer-%s.png" % (WALK, page.shot_tag), full_page=True)

    # A path the product does not serve is not turned into a link that would lead nowhere.
    assert answer.locator('a[href="/made-up-page"]').count() == 0

    link = answer.get_by_role("link", name="Solutions", exact=True)
    expect(link).to_have_count(1)
    assert link.get_attribute("href") == "/solutions"
    link.click()
    page.wait_for_url(re.compile(r"/solutions/?$"))
    expect(page.locator("main")).not_to_be_empty()
    assert len(page.locator("main").inner_text().strip()) > 20
    page.screenshot(path="%s/solutions-%s.png" % (WALK, page.shot_tag), full_page=True)
