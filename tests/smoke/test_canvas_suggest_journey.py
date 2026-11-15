"""An architect opens a business model canvas with two filled boxes, presses Suggest on an empty box, reads
the proposals with their sources and the AI label, dismisses one, accepts the other, and finds it in the box
and as a record of the model after a reload.

The model provider is a stub on a loopback port; the real route, prompt, provider call and parse run against it. Runs at 1440 wide in the light theme and at 390 wide in
the dark theme.
"""

import json
import os
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.deepseek_stub import KEY, DeepSeekStub

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from .test_canvas_one_store_journey import seed_canvas

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("CANVAS_SUGGEST_SHOTS", "") or "/tmp/canvas-suggest-shots")
BOX = "key-activities"
KEPT, DISMISSED = "Weekly release reviews", "Partner onboarding"
OTHERS = ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "GOOGLE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "CLAUDE")
REPLY = json.dumps({"proposals": [
    {"name": KEPT, "source": "Value Propositions and Customer Segments already on this canvas", "element_id": None},
    {"name": DISMISSED, "source": "Key Partners already on this canvas", "element_id": None},
]})


@pytest.fixture(scope="module")
def stub():
    s = DeepSeekStub(reply=REPLY)
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


def workspace():
    """A new organisation with one solution architect, whose role may edit a canvas."""
    from app import create_app, db

    suffix = uuid.uuid4().hex[:8]
    with create_app("testing").app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="Suggest %s" % suffix, slug="suggest-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        user = User(email="architect-%s@example.com" % suffix, first_name="Sol", last_name="Architect",
                    organization_id=org.id, enterprise_role="solution_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        out = {"org_id": org.id, "email": user.email}
        db.session.remove()
        return out


def sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=False)


def records_named(org_id, name):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    with create_app("testing").app_context():
        found = (db.session.query(ArchiMateElement).execution_options(include_all_tenants=True)
                 .filter(ArchiMateElement.organization_id == org_id, ArchiMateElement.name == name).count())
        db.session.remove()
        return found


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_suggest_proposes_with_sources_and_an_accepted_entry_becomes_a_record(
    browser, deepseek_server, width, scheme
):
    live_server = deepseek_server
    who = workspace()
    org_id = who["org_id"]
    ids = seed_canvas(org_id, canvas_name="Suggest model %d" % width)
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, who["email"])
        url = "%s/business-model/%d" % (live_server, ids["canvas"])
        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator('[data-canvas-notes="customer-segments"] > li').count() == 2
        assert page.locator('[data-canvas-notes="key-partners"] > li').count() == 1
        assert page.locator('[data-canvas-notes="%s"] > li' % BOX).count() == 0

        page.locator('[data-bmc-suggest="%s"]' % BOX).first.click()
        panel = page.get_by_test_id("bmc-suggest-panel")
        items = panel.locator("[data-suggest-item]")
        expect(items).to_have_count(2)
        expect(panel.locator("[data-suggest-notice]").first).to_contain_text("written by AI")
        for item in items.all():
            assert item.locator("[data-suggest-source]").inner_text().strip()
        shot(page, "panel", width, scheme)

        expect(items.nth(1).locator("[data-suggest-name]")).to_have_value(DISMISSED)
        items.nth(1).locator("[data-suggest-dismiss]").click()
        expect(items).to_have_count(1)
        page.locator("[data-suggest-accept]").click()
        page.wait_for_load_state("domcontentloaded")

        page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        notes = page.locator('[data-canvas-notes="%s"] > li a[data-note-text]' % BOX)
        assert [t.strip() for t in notes.all_inner_texts()] == [KEPT]
        shot(page, "after", width, scheme)
        assert records_named(org_id, KEPT) == 1
        assert records_named(org_id, DISMISSED) == 0

        notes.first.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("h1").first).to_contain_text(KEPT)
    finally:
        context.close()
