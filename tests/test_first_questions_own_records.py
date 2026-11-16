"""An organisation with no applications is first offered questions about its own records."""

import pytest
from flask import url_for

from app.extensions import db
from app.models import ArchiMateElement
from app.modules.account.services import onboarding_service as onboarding
from app.modules.ai_chat.routes.analytics_routes import own_record_questions


def _picked(org, canvases, fill=False):
    """Pick canvases; with ``fill`` the organisation first records one element for each box, so the loaded canvas holds entries."""
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES

    if fill:
        types = {t for key in canvases for z in CANVAS_TEMPLATES[key]["zones"] if z["membership"] != "attribute" for t in z["element_types"]}
        for kind in sorted(types):
            db.session.add(ArchiMateElement(name="Recorded %s" % kind, type=kind, layer="business", organization_id=org.id))
        db.session.flush()
    onboarding.save_choices(org, None, [], "startup", canvases)
    onboarding.run_starter_data(org.id)


def test_cards_come_from_the_organisations_own_canvases(app, db_session, make_org, tenant_ctx):
    one, two = make_org("one"), make_org("two")
    _picked(one, ["lean_canvas"], fill=True)
    _picked(two, ["business_model_canvas"], fill=True)
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


def test_an_empty_canvas_gets_no_card_and_every_card_names_a_record_with_content(app, db_session, make_org, tenant_ctx):
    empty, filled = make_org("empty"), make_org("filled")
    _picked(empty, ["lean_canvas", "business_model_canvas"])
    _picked(filled, ["lean_canvas"], fill=True)
    with app.test_request_context("/"):
        with tenant_ctx(empty.id):
            bare = own_record_questions(empty.id)
            held_empty = onboarding.loaded_at_sign_up(empty.id)
        with tenant_ctx(filled.id):
            full = own_record_questions(filled.id)
            held_full = onboarding.loaded_at_sign_up(filled.id)
    assert held_empty["canvases"] and all(row["entries"] == 0 for row in held_empty["canvases"])
    assert not any("canvas" in c["question"].lower() for c in bare)
    assert any("Lean Canvas" in c["question"] and c["detail"] != "0 entries" for c in full)
    assert held_full["canvases"][0]["entries"] > 0
    for card in bare + full:
        assert card["question"] == "What did we record when we signed up?" or card.get("detail") or card.get("href")


def test_the_answer_to_a_canvas_card_reads_what_is_on_that_canvas(app, db_session, make_org, login_as, client):
    from app.models.user import User
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    org = make_org("quoted")
    _picked(org, ["lean_canvas"], fill=True)
    user = User(email="quote-%s@example.test" % org.id, first_name="Q", last_name="R", organization_id=org.id, confirmed=True)
    db.session.add(user)
    db.session.flush()
    login_as(client, user)
    sources = []
    with client:
        client.get("/ai-chat/first-questions")
        found = AgentRunner(user_id=user.id)._look_up_chosen_records("What is on our Lean Canvas?", sources)
    assert "Recorded " in found, found[:400]
    assert [s["type"] for s in sources] == ["canvas"]
