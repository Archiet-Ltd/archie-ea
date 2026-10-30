import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .ai_protocol_stub import CHAT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ASKCHAT_WALK_DIR")
QUESTION = CHAT['prompt']


def _shot(page, name, width, theme="light"):
    if WALK:
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)), full_page=False)


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _seed_zombie_project_org(ai_protocol_stub):
    """A fresh organisation, one enterprise-architect user, and one deterministic
    ZOMBIE_PROJECT fact (a strategic initiative whose target end date is 200 days
    past, linked to a business case) — committed so the live_server subprocess's
    own DB connection reads it.
    """
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from datetime import date, timedelta

        from app.models.business_case import BusinessCase
        from app.models.organization import Organization
        from app.models.strategic import StrategicInitiative
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="Predictive Org %s" % suffix, slug="predictive-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)

        if ai_protocol_stub is not None:
            # The same loopback LLM transport every other smoke test's
            # organisation gets (tests/smoke/conftest.py's _seed_standard_org);
            # the chat page needs a configured provider for THIS organisation,
            # not just the shared one, or send_message_stream answers 503.
            from app.models.models import APISettings
            from tests.smoke.ai_protocol_stub import MODEL, TOKEN

            db.session.add(APISettings(
                provider="openai", key_label="ci-protocol-stub", api_key=TOKEN,
                enabled=True, default_model=MODEL, organization_id=org.id,
            ))
            db.session.commit()

        user = User(
            email="predictive-%s@example.com" % suffix,
            first_name="Predictive",
            last_name="Architect",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)

        initiative = StrategicInitiative(
            name="Legacy Migration %s" % suffix,
            organization_id=org.id,
            target_completion_date=date.today() - timedelta(days=200),
            status="in_progress",
        )
        db.session.add(initiative)
        db.session.flush()

        bc = BusinessCase(
            title="Legacy Migration Business Case %s" % suffix,
            organization_id=org.id,
            strategic_initiative_id=initiative.id,
        )
        db.session.add(bc)
        db.session.commit()
        return user.email, org.id, bc.title


def _engine_answer(org_id):
    """The real predictive engine's answer for *org_id*, computed the same way
    the chat and MCP tools reach it (app.services.predictive_engine) — not
    through the chat transport, so this assertion cannot be satisfied by the
    CI LLM transport fixture's canned reply."""
    from app import create_app

    app = create_app("testing")
    with app.app_context():
        from app.services.predictive_engine import get_predictive_insights

        from flask import g

        g.current_org_id = org_id
        return get_predictive_insights(topic="financial")


@pytest.mark.parametrize("width", [1440])
def test_predictive_answer_journey(browser, live_server, ai_protocol_stub, width):
    email, org_id, bc_title = _seed_zombie_project_org(ai_protocol_stub)

    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)

        # Go to chat
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The chat transport itself works end to end (the CI protocol fixture
        # proves the streamed-reply path; it answers no estate or predictive
        # question, since the stub supports no tool-calling protocol — see
        # the engine assertion below for the real predictive answer).
        box = page.locator("#user-input")
        expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
        box.fill(QUESTION)
        box.press("Enter")
        answer = page.get_by_text(CHAT['reply'])
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "predictive-transport", width)

        # The real predictive engine's answer for the seeded organisation —
        # the one engine the chat's get_predictive_insights tool and the MCP
        # tool both answer from (app.services.predictive_engine). This is the
        # assertion that fails when the engine returns the stub's text: the
        # stub's canned reply names no business case, target or evidence, so
        # it can never satisfy these.
        result = _engine_answer(org_id)
        alerts = result["alerts"]
        zombie = [a for a in alerts if a["prediction_type"] == "ZOMBIE_PROJECT"]
        assert zombie, f"no ZOMBIE_PROJECT alert for the seeded business case: {alerts}"
        alert = zombie[0]

        assert bc_title in alert["target_element"]
        assert CHAT['reply'] not in alert["target_element"]
        assert any(bc_title in line for line in alert["evidence"])
        assert any("as of" in line for line in alert["evidence"])
        assert all(CHAT['reply'] not in line for line in alert["evidence"])
        assert alert["source"]
        assert alert["as_of"]
        assert "unknown" not in " ".join(alert["evidence"])

    finally:
        context.close()
