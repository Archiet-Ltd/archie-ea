"""A person asks the chat to create an Assessment and is told, in the same turn, that it was applied.

The server is set up as production is: DeepSeek only, as a stub on a loopback port. deepseek-flash drafts
the change and deepseek-v4-pro reviews it. The person asks "Create a new Assessment element named ...";
the reply says Applied (not "being checked"), the link under it opens the record of the checks, and the
new Assessment opens on its own page in the person's organisation. A second organisation's person
searching the register never sees it.
"""
import json
import os
import pathlib
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

OTHERS = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")
CHAT_REPLY = "I asked for that change; the result is shown below."
ELEMENT_NAME = "Toolmap check"


def _respond(model, text, has_tools):
    if model == "deepseek-v4-pro" and not has_tools:
        return json.dumps({"criteria": [
            {"id": n, "passed": True, "reason": "Criterion %d holds for this change." % n} for n in range(1, 7)
        ]})
    return None


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply=CHAT_REPLY, respond=_respond)
    yield s
    s.close()


@pytest.fixture(scope="module")
def deepseek_server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        LLM_SPEND_CEILING_USD_DEEPSEEK="62", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _workspace(label):
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="%s %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        user = User(email="owner-%s@example.com" % suffix, first_name="Sole", last_name="Owner",
                    organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        out = {"org_id": org.id, "email": user.email}
        db.session.remove()
        return out


def _elements(org_id, name):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement

        rows = [(e.id, e.type, e.layer) for e in
                db.session.query(ArchiMateElement).execution_options(include_all_tenants=True)
                .filter(ArchiMateElement.organization_id == org_id, ArchiMateElement.name == name)]
        db.session.remove()
        return rows


@pytest.fixture
def shots(tmp_path):
    folder = pathlib.Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _shot(page, folder, name):
    page.screenshot(path=str(pathlib.Path(folder) / ("%s-%d.png" % (name, page.viewport_size["width"]))),
                    full_page=True)


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width", [1440, 390])
def test_asking_the_chat_for_an_assessment_creates_it_and_says_so_in_the_same_turn(
    browser, deepseek_server, stub, shots, width
):
    base = deepseek_server
    ws, other = _workspace("Toolmap"), _workspace("Neighbour")
    name = "%s %s" % (ELEMENT_NAME, uuid.uuid4().hex[:6])
    stub.tool_script = [("create_assessment", {"name": name})]
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        _sign_in(page, base, ws["email"])
        page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        box = page.locator("#user-input")
        expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
        box.fill("Create a new Assessment element named %s" % name)
        box.press("Enter")
        verdicts = page.get_by_test_id("change-verdicts")
        expect(verdicts).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        text = verdicts.inner_text()
        assert "Applied:" in text and name in text, text
        body = page.locator("#messages-container").inner_text()
        for stale in ("being checked", "awaiting approval", "must approve"):
            assert stale not in body, stale
        _shot(page, shots, "assessment-applied-in-chat")

        rows = _elements(ws["org_id"], name)
        assert len(rows) == 1 and rows[0][1] == "assessment", rows
        assert _elements(other["org_id"], name) == []

        # The link under the answer opens the record of the checks, with the review's reasons on it.
        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Applied")
        round_one = page.get_by_test_id("review-record-round-1")
        expect(round_one.locator("[data-testid^='review-criterion-']")).to_have_count(6)
        _shot(page, shots, "assessment-record")

        # The new Assessment opens on its own page.
        element_id, element_type, layer = rows[0]
        page.goto("%s/architecture/%s/%s/%d" % (base, layer, element_type, element_id),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(name).locator("visible=true").first).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, shots, "assessment-page")
    finally:
        context.close()
