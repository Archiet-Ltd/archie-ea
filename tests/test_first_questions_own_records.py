"""An organisation with no applications is first offered questions about its own records."""

import pytest
from flask import url_for

from app.modules.account.services import onboarding_service as onboarding
from app.modules.ai_chat.routes.analytics_routes import own_record_questions


def _picked(org, canvases):
    onboarding.save_choices(org, None, [], "startup", canvases)
    onboarding.run_starter_data(org.id)


def test_cards_come_from_the_organisations_own_canvases(app, db_session, make_org, tenant_ctx):
    one, two = make_org("one"), make_org("two")
    _picked(one, ["lean_canvas"])
    _picked(two, ["business_model_canvas"])
    with app.test_request_context("/"):
        with tenant_ctx(one.id):
            mine = [c["question"] for c in own_record_questions(one.id)]
        with tenant_ctx(two.id):
            theirs = [c["question"] for c in own_record_questions(two.id)]
    assert any("Lean Canvas" in q for q in mine)
    assert not any("Business Model Canvas" in q for q in mine)
    assert any("Business Model Canvas" in q for q in theirs)
    assert not any("Lean Canvas" in q for q in theirs)


def test_connect_github_is_offered_first_while_nothing_is_connected(app, db_session, make_org, tenant_ctx):
    org = make_org("bare")
    with app.test_request_context("/"):
        with tenant_ctx(org.id):
            cards = own_record_questions(org.id)
    assert cards[0]["question"] == "Connect GitHub to see who owns what and what breaks"
    assert cards[0]["href"] == "/admin/connectors/github"


def test_company_profile_card_only_for_an_organisation_that_recorded_one(app, db_session, make_org, tenant_ctx):
    recorded, bare = make_org("recorded"), make_org("bare")
    onboarding.save_choices(recorded, None, [], "scaleup", [])
    with app.test_request_context("/"):
        with tenant_ctx(recorded.id):
            mine = [c["question"] for c in own_record_questions(recorded.id)]
        with tenant_ctx(bare.id):
            none = [c["question"] for c in own_record_questions(bare.id)]
    assert "What did we record when we signed up?" in mine
    assert "What did we record when we signed up?" not in none
