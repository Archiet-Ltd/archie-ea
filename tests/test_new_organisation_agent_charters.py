"""Every organisation is given the default agent charters when it is created, so a new
startup's first chat answer is never refused by a missing charter; and a charter that lets
a persona read never lets it read another organisation.
"""
from __future__ import annotations

import uuid

import pytest

from tests.genome_cited_seed import seed_cited_model

pytestmark = pytest.mark.usefixtures("db_session")


def _signed_up(app, label):
    from app.models.user import User
    from app.modules.account.services.account_service import AccountService

    email = "chrt.%s@example.test" % uuid.uuid4().hex[:8]
    with app.test_request_context("/"):
        AccountService.register_user("Casey", label, email, "Charter!Test2026", confirmed=True)
    user = User.query.filter_by(email=email).one()
    return user, user.organization_id


def _call(user, org_id, tenant_ctx, persona, name, **arguments):
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor

    with tenant_ctx(org_id):
        return ToolExecutor(user.id, persona=persona).execute(ToolCall(id="t", name=name, arguments=arguments))


def test_a_fresh_sign_up_has_the_default_charters_and_its_chat_lookup_is_not_refused(app, db_session, tenant_ctx):
    from app.models.agent_charter import AgentCharter
    from app.modules.ai_chat.services.architect_persona_charters import ARCHITECT_PERSONAS

    user, org_id = _signed_up(app, "Founder")
    seed_cited_model(db_session, org_id)
    held = {c.persona for c in AgentCharter.query.filter_by(organization_id=org_id, version=1)}
    assert held == set(ARCHITECT_PERSONAS)
    for persona in ARCHITECT_PERSONAS:
        result = _call(user, org_id, tenant_ctx, persona, "get_genome_slice", domain="business")
        assert result.get("code") != "CHARTER_REFUSED", persona
        assert result["success"] is True and result["result"]["nodes"], persona


def test_giving_charters_again_changes_nothing(app, db_session):
    from app.commands.seed_agent_charters import ensure_charters
    from app.models.agent_charter import AgentCharter

    _user, org_id = _signed_up(app, "Again")
    before = AgentCharter.query.filter_by(organization_id=org_id).count()
    made, kept = ensure_charters(org_id)
    assert made == 0 and kept > 0
    assert AgentCharter.query.filter_by(organization_id=org_id).count() == before


def test_the_admin_created_organisation_and_the_service_created_one_have_charters(app, db_session):
    from app.models.agent_charter import AgentCharter
    from app.services.org_service import OrgService

    org = OrgService.create_org("Charter Co %s" % uuid.uuid4().hex[:6], "own.%s@example.test" % uuid.uuid4().hex[:6], "Charter!Test2026")
    org_id = org[0].id
    assert AgentCharter.query.filter_by(organization_id=org_id).count() > 0


def test_a_charter_that_lets_a_persona_read_never_lets_it_read_another_organisation(app, db_session, tenant_ctx):
    """The widened rule opens read tools to a persona; the organisation still comes from the session."""
    from app.models.agent_charter import AgentCharter

    user_a, org_a = _signed_up(app, "Alpha")
    user_b, org_b = _signed_up(app, "Bravo")
    seed_a = seed_cited_model(db_session, org_a)
    seed_b = seed_cited_model(db_session, org_b, suffix=" B")
    # a charter is the organisation's own: B's charter grants nothing in A
    assert AgentCharter.tool_allowed("solutions_architect", "get_genome_slice", org_a) is True
    # an organisation with no stored charter is given the persona's default (never another organisation's), so
    # a read is allowed and a forbidden tool is still refused; what a read returns is fenced by the session
    assert AgentCharter.tool_allowed("solutions_architect", "get_genome_slice", 999999999) is True
    assert AgentCharter.tool_allowed("solutions_architect", "create_programme", 999999999) is False

    seen = _call(user_a, org_a, tenant_ctx, "solutions_architect", "get_genome_slice", domain="business")
    names = {n["name"] for n in seen["result"]["nodes"]}
    assert seed_a["names"]["planned"] in names
    assert not any(n.endswith(" B") for n in names)

    # an organisation id offered as an argument is dropped; B's node id reads like a missing one
    scoped = _call(user_a, org_a, tenant_ctx, "solutions_architect", "get_genome_slice",
                   domain="business", organization_id=org_b)
    assert {n["id"] for n in scoped["result"]["nodes"]} == {n["id"] for n in seen["result"]["nodes"]}
    foreign = _call(user_a, org_a, tenant_ctx, "solutions_architect", "get_genome_slice", domain="business",
                    focus_ids=["business:cap:%d" % seed_b["cap_ids"]["planned"]])
    assert foreign.get("code") == "NOT_FOUND" and foreign["success"] is False

    # a tool the charter forbids is still refused
    forbidden = _call(user_a, org_a, tenant_ctx, "solutions_architect", "create_programme")
    assert forbidden.get("code") == "CHARTER_REFUSED"
