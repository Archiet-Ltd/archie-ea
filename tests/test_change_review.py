"""A requested change is checked, reviewed by a model other than the drafter, and applied or declined.

Nothing waits for a second person. A person alone in a workspace asks the assistant for a change and
gets it, in the same turn, with a record of the checks, the reviewer's reasons, the rounds and both
model names. A change the reviewer rejects leaves the data as it was and says why.

The reviewer is stubbed at the router (LLMModelRouter.select_reviewer / call_model) except in the
reviewer-selection tests and the one test that drives the real router against a loopback DeepSeek.
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

PASS_ALL = {"criteria": [{"id": n, "passed": True, "reason": "Fine."} for n in range(1, 7)]}


def _fail(criterion, reason):
    return {"criteria": [
        {"id": n, "passed": n != criterion, "reason": reason if n == criterion else "Fine."} for n in range(1, 7)
    ]}


def _user(db_session, org_id, role_name="Architect"):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    assert role is not None, "role %s is not seeded" % role_name
    # The role is given to the constructor: User() otherwise assigns the default role object.
    user = User(email="review-%s@example.com" % uuid.uuid4().hex[:10], first_name="Solo", last_name="Founder",
                organization_id=org_id, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _application(db_session, org_id, name, deployment_status="development", lifecycle_status=None):
    from app.models.application_portfolio import ApplicationComponent

    app_obj = ApplicationComponent(name=name, organization_id=org_id, deployment_status=deployment_status,
                                   lifecycle_status=lifecycle_status)
    db_session.add(app_obj)
    db_session.flush()
    return app_obj


class FakeReviewer:
    """Stands in for the model calls behind the router; records every prompt it is sent."""

    def __init__(self, reviews, revisions=(), choice=("deepseek", "deepseek-reasoner", True)):
        self.reviews = list(reviews)
        self.revisions = list(revisions)
        self.choice = choice
        self.review_prompts = []
        self.revision_prompts = []
        self.selected_for = []

    def install(self, monkeypatch):
        from app.modules.ai_chat.services import llm_model_router as router

        fake = self

        def select_reviewer(self, drafter_provider, drafter_model):
            fake.selected_for.append((drafter_provider, drafter_model))
            if fake.choice is None:
                return None
            return router.ReviewerChoice(*fake.choice)

        def call_model(self, choice, prompt, timeout=90.0):
            if "independent reviewer rejected it" in prompt:
                fake.revision_prompts.append((choice.ref, prompt))
                return json.dumps(fake.revisions.pop(0))
            fake.review_prompts.append((choice.ref, prompt))
            return json.dumps(fake.reviews.pop(0))

        monkeypatch.setattr(router.LLMModelRouter, "select_reviewer", select_reviewer)
        monkeypatch.setattr(router.LLMModelRouter, "call_model", call_model)
        return self


def _ask_for_status(user, app_name, new_status, drafter=("deepseek", "deepseek-chat")):
    """What the assistant does when its model calls update_application_status in a chat turn."""
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.change_review import ReviewBudget
    from app.modules.ai_chat.tools.executor import ToolCall

    runner = AgentRunner(user_id=user.id, chat_session_id="review-%s" % uuid.uuid4().hex[:6])
    runner._turn_id = "turn-1"
    runner._review_budget = ReviewBudget()
    runner._drafter = (drafter[0], drafter[1], "Move %s to %s" % (app_name, new_status)) if drafter else None
    tc = ToolCall(id="1", name="update_application_status", arguments={
        "application_name": app_name, "new_status": new_status, "rationale": "The founder asked for it",
    })
    approval_id = runner._queue_approval(tc)
    return approval_id, runner._verdicts.get(approval_id)


def _row(approval_id):
    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    db.session.expire_all()
    return AIChatCRUDApproval.query.filter_by(id=approval_id).one()


def _events(approval_id):
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog

    return AIChatApprovalAuditLog.query.filter_by(approval_id=approval_id).order_by(AIChatApprovalAuditLog.id).all()


def _status_of(app_id):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    db.session.expire_all()
    return ApplicationComponent.query.filter_by(id=app_id).one().deployment_status


# --------------------------------------------------------------------------- #
# The person alone in a workspace gets the change                              #
# --------------------------------------------------------------------------- #


def test_a_person_alone_in_a_workspace_gets_the_change_applied_in_the_same_turn(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus

    org = make_org("solo")
    founder = _user(db_session, org.id)
    ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "development")
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)

    with tenant_ctx(org.id):
        approval_id, verdict = _ask_for_status(founder, ifiok.name, "production")
        assert verdict["state"] == "applied", verdict
        assert verdict["message"].startswith("Applied: ")
        assert verdict["record_url"].endswith("approval=%d" % approval_id)
        assert _status_of(ifiok.id) == "production"

        row = _row(approval_id)
        assert row.status == ApprovalStatus.APPROVED
        assert row.approved_by_id is None, "the server applied it; no person is recorded as approving"
        assert row.drafter_model == "deepseek/deepseek-chat"
        assert row.reviewer_model == "deepseek/deepseek-reasoner"
        assert row.review_rounds == 1
        assert row.rubric_version == "1"
        assert row.before_snapshot["fields"] == {"deployment_status": "development"}
        reasons = row.review_reasons
        assert [c["name"] for c in reasons["checks"]] == [
            "Belongs to this workspace", "The tool exists", "The request is complete",
            "The thing it changes exists", "The value is allowed", "Not a repeat",
            "The person who asked may make it",
        ]
        assert len(reasons["rounds"][0]["criteria"]) == 6
        assert reasons["same_provider"] is True

        events = [(e.event, e.actor_type) for e in _events(approval_id)]
        names = [e for e, _ in events]
        assert names[:3] == ["created", "checked", "reviewed"]
        assert ("approved", "system") in events and ("executed", "system") in events
        assert names[-1] == "applied"
        assert "self_approval_refused" not in names

    # The reviewer read the drafter's request and this organisation's application, and nothing else.
    assert reviewer.selected_for == [("deepseek", "deepseek-chat")]
    assert ifiok.name in reviewer.review_prompts[0][1]


def test_the_record_page_reads_rounds_models_and_reasons(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org = make_org("record")
    founder = _user(db_session, org.id)
    ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "development")
    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, _verdict = _ask_for_status(founder, ifiok.name, "production")
        record = AIChatApprovalService(founder.id).get_review_record(approval_id)["record"]
    assert record["verdict"] == "Applied"
    assert record["drafter"] == "deepseek-chat (DeepSeek)"
    assert record["reviewer"] == "deepseek-reasoner (DeepSeek)"
    assert record["same_provider_note"] == "Both models came from DeepSeek because no other provider is set up."
    assert record["rounds"] == 1
    assert record["review_rounds"][0]["criteria"][4]["name"] == "Consistent"


# --------------------------------------------------------------------------- #
# Declined, revised, and the deterministic checks                             #
# --------------------------------------------------------------------------- #


def test_a_declined_change_leaves_the_data_unchanged_and_names_the_reasons(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus

    org = make_org("decline")
    founder = _user(db_session, org.id)
    ledger = _application(db_session, org.id, "Old Ledger %s" % uuid.uuid4().hex[:6], "retiring",
                          lifecycle_status="decommissioned")
    reason = "The portfolio shows this application as decommissioned and the request does not say otherwise."
    args = {"application_name": ledger.name, "new_status": "production", "rationale": "The founder asked for it"}
    reviewer = FakeReviewer([_fail(5, reason)], revisions=[{"arguments": args, "summary": "same"}]).install(monkeypatch)

    with tenant_ctx(org.id):
        approval_id, verdict = _ask_for_status(founder, ledger.name, "production")
        assert verdict["state"] == "declined", verdict
        assert verdict["message"].startswith("Declined: ")
        assert reason in verdict["message"]
        assert _status_of(ledger.id) == "retiring"
        row = _row(approval_id)
        assert row.status == ApprovalStatus.REJECTED
        assert reason in row.rejected_reason
        assert row.review_rounds == 1
        names = [e.event for e in _events(approval_id)]
        assert names[-2:] == ["revised", "declined"]
        assert "executed" not in names
    # The identical revision ended the loop: one review, one revision, no second review.
    assert len(reviewer.review_prompts) == 1 and len(reviewer.revision_prompts) == 1
    assert reviewer.revision_prompts[0][0] == "deepseek/deepseek-chat", "the drafter revises its own draft"


def test_a_revision_is_reviewed_again_and_applied_when_it_passes(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("revise")
    founder = _user(db_session, org.id)
    ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "development")
    revised = {"application_name": ifiok.name, "new_status": "production", "rationale": "The founder asked for it"}
    FakeReviewer(
        [_fail(1, "The person asked for production, the change sets testing."), PASS_ALL],
        revisions=[{"arguments": revised, "summary": "Move %s to production." % ifiok.name}],
    ).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, verdict = _ask_for_status(founder, ifiok.name, "testing")
        assert verdict["state"] == "applied", verdict
        assert verdict["rounds"] == 2
        assert _status_of(ifiok.id) == "production"
        names = [e.event for e in _events(approval_id)]
        assert names.count("reviewed") == 2 and names.count("revised") == 1


def test_after_three_failed_reviews_the_change_is_declined(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("three")
    founder = _user(db_session, org.id)
    ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "development")

    def draft(status):
        return {"arguments": {"application_name": ifiok.name, "new_status": status, "rationale": "r"}, "summary": status}

    reviewer = FakeReviewer(
        [_fail(2, "Invented."), _fail(2, "Still invented."), _fail(2, "Invented again.")],
        revisions=[draft("testing"), draft("design")],
    ).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, verdict = _ask_for_status(founder, ifiok.name, "production")
        assert verdict["state"] == "declined"
        assert "Invented again." in verdict["message"]
        assert _status_of(ifiok.id) == "development"
        assert _row(approval_id).review_rounds == 3
    assert len(reviewer.review_prompts) == 3 and len(reviewer.revision_prompts) == 2


def test_a_change_no_model_drafted_gets_one_review_and_no_revision(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("form")
    founder = _user(db_session, org.id)
    ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "development")
    reviewer = FakeReviewer([_fail(6, "The summary does not say what changes.")],
                            choice=("deepseek", "deepseek-reasoner", False)).install(monkeypatch)
    with tenant_ctx(org.id):
        _approval_id, verdict = _ask_for_status(founder, ifiok.name, "production", drafter=None)
    assert verdict["state"] == "declined"
    assert reviewer.selected_for == [("", "")]
    assert reviewer.revision_prompts == []


def test_a_failed_check_declines_with_its_reason_and_calls_no_model(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("check")
    founder = _user(db_session, org.id)
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _approval_id, verdict = _ask_for_status(founder, "No Such App %s" % uuid.uuid4().hex[:6], "production")
    assert verdict["state"] == "declined"
    assert "No application named" in verdict["message"]
    assert reviewer.review_prompts == [] and reviewer.selected_for == []


def test_a_value_the_field_already_holds_is_declined_by_the_checks(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("same")
    founder = _user(db_session, org.id)
    ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "production")
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _approval_id, verdict = _ask_for_status(founder, ifiok.name, "production")
    assert verdict["state"] == "declined"
    assert "is already production" in verdict["message"]
    assert reviewer.review_prompts == []


def test_a_requester_whose_role_cannot_write_is_declined(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("viewer")
    viewer = _user(db_session, org.id, role_name="Viewer")
    ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "development")
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _approval_id, verdict = _ask_for_status(viewer, ifiok.name, "production")
        assert verdict["state"] == "declined"
        assert "no longer has a role that can make changes" in verdict["message"]
        assert _status_of(ifiok.id) == "development"
    assert reviewer.review_prompts == []


def test_with_no_reviewer_the_change_waits_then_is_declined_after_twelve_attempts(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.modules.ai_chat.services.change_review import MAX_WAITS, NO_REVIEWER_REASON

    org = make_org("wait")
    founder = _user(db_session, org.id)
    ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "development")
    FakeReviewer([], choice=None).install(monkeypatch)
    with tenant_ctx(org.id):
        approval_id, verdict = _ask_for_status(founder, ifiok.name, "production")
        assert verdict["state"] == "in_review"
        service = AIChatApprovalService(founder.id)
        for _ in range(MAX_WAITS - 2):
            assert service.settle_after_review(approval_id)["state"] == "in_review"
        final = service.settle_after_review(approval_id)
        assert final["state"] == "declined"
        assert final["reasons"] == [NO_REVIEWER_REASON]
        assert _status_of(ifiok.id) == "development"


def test_the_sweep_settles_a_quiet_change_of_its_own_organisation_only(db_session, make_org, tenant_ctx, monkeypatch):
    from app import db
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, AIChatCRUDApproval
    from app.modules.ai_chat.services.change_review import sweep_organisation

    org_a, org_b = make_org("sweep-a"), make_org("sweep-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    app_a = _application(db_session, org_a.id, "Ifiok A %s" % uuid.uuid4().hex[:6], "development")
    app_b = _application(db_session, org_b.id, "Ifiok B %s" % uuid.uuid4().hex[:6], "development")
    FakeReviewer([], choice=None).install(monkeypatch)
    ids = {}
    for org, user, app_obj in ((org_a, user_a, app_a), (org_b, user_b, app_b)):
        with tenant_ctx(org.id):
            ids[org.id], verdict = _ask_for_status(user, app_obj.name, "production")
            assert verdict["state"] == "in_review"
    earlier = datetime.utcnow() - timedelta(minutes=30)
    for approval_id in ids.values():
        AIChatCRUDApproval.query.filter_by(id=approval_id).update({"created_at": earlier})
        AIChatApprovalAuditLog.query.filter_by(approval_id=approval_id).update({"created_at": earlier})
    db.session.flush()

    FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org_a.id):
        counts = sweep_organisation(org_a.id)
        assert _status_of(app_a.id) == "production"
    assert counts == {"applied": 1, "declined": 0, "in_review": 0}
    with tenant_ctx(org_b.id):
        assert _status_of(app_b.id) == "development", "the sweep of one organisation never settles another's"


# --------------------------------------------------------------------------- #
# Two real organisations                                                       #
# --------------------------------------------------------------------------- #


def test_one_organisations_change_never_reaches_anothers_review_and_cannot_be_applied_from_it(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    org_a, org_b = make_org("iso-a"), make_org("iso-b")
    user_a, user_b = _user(db_session, org_a.id), _user(db_session, org_b.id)
    alpha = _application(db_session, org_a.id, "Alpha Ledger %s" % uuid.uuid4().hex[:6], "development")
    bravo = _application(db_session, org_b.id, "Bravo Billing %s" % uuid.uuid4().hex[:6], "development")

    # A's change waits (no reviewer); B tries to settle it and to apply it.
    FakeReviewer([], choice=None).install(monkeypatch)
    with tenant_ctx(org_a.id):
        a_id, _ = _ask_for_status(user_a, alpha.name, "production")
    reviewer = FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org_b.id):
        service_b = AIChatApprovalService(user_b.id)
        assert service_b.settle_after_review(a_id)["code"] == "NOT_FOUND"
        assert service_b.approve_and_execute(a_id)["code"] == "NOT_FOUND"
        assert service_b.approve_and_execute(a_id, settled_by_review=True)["code"] == "NOT_FOUND"
        assert service_b.get_review_record(a_id)["code"] == "NOT_FOUND"
        _b_id, verdict_b = _ask_for_status(user_b, bravo.name, "production")
    assert verdict_b["state"] == "applied"
    with tenant_ctx(org_a.id):
        assert _row(a_id).status == ApprovalStatus.PENDING
        assert _status_of(alpha.id) == "development"

    # The only review that ran was B's, and its prompt carries B's data and none of A's.
    assert len(reviewer.review_prompts) == 1
    prompt_b = reviewer.review_prompts[0][1]
    assert bravo.name in prompt_b and alpha.name not in prompt_b

    with tenant_ctx(org_a.id):
        verdict_a = AIChatApprovalService(user_a.id).settle_after_review(a_id)
    assert verdict_a["state"] == "applied"
    prompt_a = reviewer.review_prompts[1][1]
    assert alpha.name in prompt_a and bravo.name not in prompt_a


# --------------------------------------------------------------------------- #
# The reviewer is never the drafter (real router selection)                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def only_deepseek(monkeypatch):
    for name in ("OPENAI", "ANTHROPIC", "GEMINI", "AZURE", "OPENROUTER", "NVIDIA"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-review-test-0001")
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "62")


def test_with_one_provider_the_reviewer_is_its_other_model(db_session, make_org, tenant_ctx, only_deepseek):
    from app.modules.ai_chat.services.llm_model_router import LLMModelRouter

    org = make_org("one-provider")
    router = LLMModelRouter()
    with tenant_ctx(org.id):
        chat = router.select_reviewer("deepseek", "deepseek-chat")
        reasoner = router.select_reviewer("deepseek", "deepseek-reasoner")
        no_model = router.select_reviewer(None, None)
    assert (chat.provider, chat.model, chat.same_provider) == ("deepseek", "deepseek-reasoner", True)
    assert (reasoner.provider, reasoner.model, reasoner.same_provider) == ("deepseek", "deepseek-chat", True)
    assert (no_model.provider, no_model.model) == ("deepseek", "deepseek-reasoner")


def test_with_a_second_provider_the_reviewer_comes_from_it(db_session, make_org, tenant_ctx, only_deepseek):
    from app.models.models import APISettings
    from app.modules.ai_chat.services.llm_model_router import LLMModelRouter

    org = make_org("two-providers")
    db_session.add(APISettings(provider="openai", api_key="sk-org-own-openai-review-test", enabled=True,
                               organization_id=org.id, key_label=""))
    db_session.flush()
    router = LLMModelRouter()
    with tenant_ctx(org.id):
        for_deepseek = router.select_reviewer("deepseek", "deepseek-chat")
        for_openai = router.select_reviewer("openai", "gpt-4o")
    assert (for_deepseek.provider, for_deepseek.same_provider) == ("openai", False)
    assert for_deepseek.model and for_deepseek.model != "deepseek-chat"
    assert (for_openai.provider, for_openai.model, for_openai.same_provider) == ("deepseek", "deepseek-reasoner", False)


def test_with_no_provider_there_is_no_reviewer(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.llm_model_router import LLMModelRouter

    for name in ("OPENAI", "ANTHROPIC", "GEMINI", "DEEPSEEK"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    org = make_org("no-provider")
    with tenant_ctx(org.id):
        assert LLMModelRouter().select_reviewer("deepseek", "deepseek-chat") is None


def test_the_real_router_reviews_a_deepseek_chat_draft_with_deepseek_reasoner(
    db_session, make_org, tenant_ctx, only_deepseek, monkeypatch
):
    """Production's set-up: DeepSeek only, two DeepSeek models. The review goes through the real
    router and the real LLM service to a loopback DeepSeek, asking for the other model."""
    from tests.deepseek_stub import KEY, DeepSeekStub

    from app.modules.ai_chat.services.llm_service_impl import LLMService

    stub = DeepSeekStub(reply=json.dumps(PASS_ALL))
    spend = []
    monkeypatch.setattr(LLMService, "record_spend", staticmethod(
        lambda provider, model, token_input, token_output: spend.append((provider, model)) or 0.0))
    try:
        monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
        monkeypatch.setenv("DEEPSEEK_API_BASE", stub.base_url)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
        org = make_org("real-router")
        founder = _user(db_session, org.id)
        ifiok = _application(db_session, org.id, "Ifiok %s" % uuid.uuid4().hex[:6], "development")
        with tenant_ctx(org.id):
            approval_id, verdict = _ask_for_status(founder, ifiok.name, "production")
            assert verdict["state"] == "applied", verdict
            assert _status_of(ifiok.id) == "production"
            row = _row(approval_id)
            assert (row.drafter_model, row.reviewer_model) == ("deepseek/deepseek-chat", "deepseek/deepseek-reasoner")
    finally:
        stub.close()
    assert [r["model"] for r in stub.requests] == ["deepseek-reasoner"]
    assert ifiok.name in stub.requests[0]["text"]
    assert spend == [("deepseek", "deepseek-reasoner")], "the platform's spend ledger records the review"
