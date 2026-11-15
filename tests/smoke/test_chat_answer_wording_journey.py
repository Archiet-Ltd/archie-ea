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
    "I prepared a genome patch to link Data loss to %s. The short answer is yes. "
    "The lookup `explain_element` found it with `supported: false` and `supported_by` empty, status "
    "`in_progress`. Select an application from the list, then update Payroll set to retire; "
    "I can delete from your model what you no longer need. The architecture repository holds the standards. "
    'A save was not tried, but an earlier note read: null value in column "organization_id" of relation '
    '"risk_application_links" violates not-null constraint. '
    "Open `/applications` to see the rest, not `/made-up-page`, or send it to the ARB."
)
KEPT_ENGLISH = (
    "The short answer is yes.",
    "Select an application from the list, then update Payroll set to retire;",
    "I can delete from your model what you no longer need.",
    "The architecture repository holds the standards.",
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
    # Watch the whole page from now on: a raw word on screen, even for an instant, is recorded.
    page.evaluate("""() => {
        window.__rawSeen = [];
        const raw = ['genome', 'explain_element', 'supported_by', 'in_progress', 'null value'];
        new MutationObserver(() => {
            const text = document.body.innerText.toLowerCase();
            raw.forEach(w => { if (text.includes(w)) window.__rawSeen.push(w); });
        }).observe(document.body, {childList: true, subtree: true, characterData: true});
    }""")
    box = page.locator("#user-input")
    box.fill(QUESTION % mine["application"])
    box.press("Enter")
    answer = page.locator(".message-content").last
    expect(answer).to_contain_text("proposed change to your model", timeout=PAGE_TIMEOUT)
    text = answer.inner_text()
    assert page.evaluate("window.__rawSeen") == []
    lowered = text.lower()
    for word in ("genome", "explain_element", "supported:", "supported_by", "in_progress", "null value",
                 "organization_id", "risk_application_links", "constraint", "`", "/applications"):
        assert word not in lowered, (word, text)
    assert "ARB" not in text.replace("Architecture Review Board", "")
    assert "not supported" in text and "in progress" in text
    # Ordinary English is left as the model wrote it, and no failed save is invented.
    for sentence in KEPT_ENGLISH:
        assert sentence in text, (sentence, text)
    assert "could not be saved" not in text and "nothing was changed" not in text
    assert mine["application"] in text
    assert theirs["application"] not in page.locator("main").inner_text()
    os.makedirs(WALK, exist_ok=True)
    page.screenshot(path="%s/answer-%s.png" % (WALK, page.shot_tag), full_page=True)

    # A path the product does not serve is not turned into a link that would lead nowhere.
    assert answer.locator('a[href="/made-up-page"]').count() == 0

    # Every page the chat may link to opens for this person (no refusal, no missing page).
    offered = page.evaluate("window.chatKnownPages")
    assert "/applications" in offered and offered
    for path in offered:
        reply = page.context.request.get(server + path)
        assert reply.status == 200, (path, reply.status)

    link = answer.get_by_role("link", name="Applications", exact=True)
    expect(link).to_have_count(1)
    assert link.get_attribute("href") == "/applications"
    link.click()
    page.wait_for_url(re.compile(r"/applications/?$"))
    # The page names the application the person was asking about, not an empty state.
    expect(page.locator("main")).to_contain_text(mine["application"], timeout=PAGE_TIMEOUT)
    assert theirs["application"] not in page.locator("main").inner_text()
    page.screenshot(path="%s/applications-%s.png" % (WALK, page.shot_tag), full_page=True)
