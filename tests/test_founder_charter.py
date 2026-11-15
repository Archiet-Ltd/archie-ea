"""A technical founder alone in a workspace gets the changes that keep their own model current.

The self-serve sign-up gives a CTO the cto_founder charter. It permits the founder's own changes,
still refuses what needs a board (ARB submission), and a refused change is declined by the checks
before any reviewing model is called. The executive-briefing cto charter stays read-only.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest

from tests.test_agent_charter_and_run_records import _seed_charters
from tests.test_change_review import (
    PASS_ALL, FakeReviewer, _application, _events, _row, _status_of, _user,
)

pytestmark = pytest.mark.usefixtures("db_session")

_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261110_founder_charter.py"
_OLD_PURPOSE = "Technical founder's view — what breaks, who owns it, what it costs, what to fix first."
_FOUNDER_CHANGES = [
    "update_application_status", "bulk_update_application_status", "create_archimate_element",
    "create_archimate_relationship", "create_solution", "link_capability_to_solution",
    "record_capability_maturity", "score_rationalization",
]


def _ask(user, persona, tool, arguments, drafter=("deepseek", "deepseek-flash")):
    """What the assistant does when its model calls a write tool in a chat turn under a persona."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.change_review import ReviewBudget
    from app.modules.ai_chat.tools.executor import ToolCall

    runner = AgentRunner(user_id=user.id, chat_session_id="founder-%s" % uuid.uuid4().hex[:6])
    runner._turn_id = "turn-1"
    runner._review_budget = ReviewBudget()
    runner._drafter = (drafter[0], drafter[1], "Please make this change")
    approval_id = runner._queue_approval(ToolCall(id="1", name=tool, arguments=arguments), persona=persona)
    return approval_id, runner._verdicts.get(approval_id)


def _status_args(app_name, status="production"):
    return {"application_name": app_name, "new_status": status, "rationale": "The founder asked for it"}


def _founder_workspace(db_session, make_org, label):
    org = make_org(label)
    _seed_charters([org.id])
    founder = _user(db_session, org.id)
    return org, founder


def test_a_founder_status_change_is_applied_in_the_same_turn_and_names_both_models(
    db_session, make_org, tenant_ctx, monkeypatch
):
    org, founder = _founder_workspace(db_session, make_org, "founder")
    atlas = _application(db_session, org.id, "Atlas %s" % uuid.uuid4().hex[:6], "development")
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, verdict = _ask(founder, "cto_founder", "update_application_status", _status_args(atlas.name))
        assert verdict["state"] == "applied", verdict
        assert verdict["message"].startswith("Applied: ")
        assert _status_of(atlas.id) == "production"
        row = _row(approval_id)
        assert row.persona == "cto_founder"
        assert row.drafter_model == "deepseek/deepseek-flash"
        assert row.reviewer_model == "deepseek/deepseek-v4-pro"
        assert "The assistant's charter allows it" in [c["name"] for c in row.review_reasons["checks"]]
    assert len(reviewer.review_prompts) == 1


def test_a_charter_refused_change_is_declined_with_the_reason_and_no_reviewer_is_called(
    db_session, make_org, tenant_ctx, monkeypatch
):
    org, founder = _founder_workspace(db_session, make_org, "arb")
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, verdict = _ask(founder, "cto_founder", "submit_for_arb_review", {"solution_name": "Anything"})
        assert verdict["state"] == "declined", verdict
        assert "Your assistant is not set up to make this kind of change" in verdict["message"]
        assert "charter" not in verdict["message"].lower() and "submit_for_arb_review" not in verdict["message"]
        assert _row(approval_id).reviewer_model is None
        assert [e.event for e in _events(approval_id)][:2] == ["created", "checked"]
    assert reviewer.selected_for == [] and reviewer.review_prompts == [] and reviewer.revision_prompts == []


def test_the_executive_briefing_charter_still_refuses_writes_before_any_review(
    db_session, make_org, tenant_ctx, monkeypatch
):
    org = make_org("cto")
    _seed_charters([org.id])
    cto = _user(db_session, org.id)
    atlas = _application(db_session, org.id, "Atlas %s" % uuid.uuid4().hex[:6], "development")
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _id, verdict = _ask(cto, "cto", "update_application_status", _status_args(atlas.name))
        assert verdict["state"] == "declined", verdict
        assert "Your assistant is not set up to make this kind of change" in verdict["message"]
        assert "charter" not in verdict["message"].lower() and "update_application_status" not in verdict["message"]
        assert _status_of(atlas.id) == "development"
    assert reviewer.selected_for == [] and reviewer.review_prompts == []


def test_the_default_founder_charter_permits_the_listed_changes_and_forbids_only_what_needs_a_board(db_session):
    from app.commands.seed_agent_charters import default_charter_fields
    from app.models.agent_charter import AgentCharter

    fields = default_charter_fields("cto_founder")
    assert sorted(fields["proposable_actions"]) == sorted(_FOUNDER_CHANGES)
    assert {"application", "capability", "risk", "solution", "archimate_element"} <= set(fields["readable_entities"])
    for tool in _FOUNDER_CHANGES:
        assert AgentCharter.tool_allowed("cto_founder", tool, 999999999), tool
    assert not AgentCharter.tool_allowed("cto_founder", "submit_for_arb_review", 999999999)
    assert not AgentCharter.tool_allowed("cto_founder", "create_vendor", 999999999)


def test_a_founder_of_one_organisation_never_changes_a_record_of_another(
    db_session, make_org, tenant_ctx, monkeypatch
):
    org_a, _founder_a = _founder_workspace(db_session, make_org, "a")
    org_b, founder_b = _founder_workspace(db_session, make_org, "b")
    theirs = _application(db_session, org_a.id, "Theirs %s" % uuid.uuid4().hex[:6], "development")
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org_b.id):
        _id, verdict = _ask(founder_b, "cto_founder", "update_application_status", _status_args(theirs.name))
        assert verdict["state"] == "declined", verdict
    with tenant_ctx(org_a.id):
        assert _status_of(theirs.id) == "development"
    assert reviewer.review_prompts == []


# --- the stored charters --------------------------------------------------------------------------


def _run_migration(step="upgrade"):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app import db

    spec = importlib.util.spec_from_file_location("founder_charter_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = MigrationContext.configure(db.session.connection())
    with Operations.context(context):
        getattr(module, step)()


def _versions(org_id):
    from app.models.agent_charter import AgentCharter

    return sorted(c.version for c in AgentCharter.query.filter_by(organization_id=org_id, persona="cto_founder"))


def _old_default(org_id, version=1):
    from app.commands.seed_agent_charters import ALL_WRITE_TOOLS
    from app.models.agent_charter import AgentCharter

    return AgentCharter(organization_id=org_id, persona="cto_founder", version=version, purpose=_OLD_PURPOSE,
                        readable_entities=["application", "capability", "risk"], proposable_actions=[],
                        forbidden_actions=list(ALL_WRITE_TOOLS), charter_text="old text")


def test_the_migration_gives_an_unamended_stored_charter_the_new_default_and_keeps_an_amended_one(
    app, db_session, make_org
):
    from app import db
    from app.models.agent_charter import AgentCharter
    from app.models.user import User  # noqa: F401  (models loaded)

    stale, amended = make_org("stale"), make_org("amended")
    db.session.add(_old_default(stale.id))
    db.session.add(AgentCharter(
        organization_id=amended.id, persona="cto_founder", version=1, purpose="Our own wording",
        readable_entities=["application"], proposable_actions=["score_rationalization"],
        forbidden_actions=["submit_for_arb_review"], charter_text="Written by our administrator"))
    db.session.flush()

    _run_migration()
    db.session.expire_all()

    assert _versions(stale.id) == [1, 2]
    assert AgentCharter.query.filter_by(organization_id=stale.id, persona="cto_founder", version=1).one().charter_text == "old text"
    assert AgentCharter.tool_allowed("cto_founder", "update_application_status", stale.id)
    assert not AgentCharter.tool_allowed("cto_founder", "submit_for_arb_review", stale.id)

    assert _versions(amended.id) == [1]
    kept = AgentCharter.current_for("cto_founder", amended.id)
    assert kept.purpose == "Our own wording" and kept.charter_text == "Written by our administrator"
    assert not AgentCharter.tool_allowed("cto_founder", "update_application_status", amended.id)

    before = AgentCharter.query.count()
    _run_migration()
    db.session.expire_all()
    assert AgentCharter.query.count() == before, "a second run adds nothing"

    _run_migration("downgrade")
    db.session.expire_all()
    assert _versions(stale.id) == [1] and _versions(amended.id) == [1]
