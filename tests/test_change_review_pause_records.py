"""What a held change's record says, and what travels the real loop.

* A change held by the stop-all-writes switch has one "paused" row when it is first held and one "resumed" row
  when writes resume, and a sweep that does not check it writes no "checked" row.
* A person's refused Approve while writes are paused leaves a "paused" row on that record, once.
* The record's pause sentence is read from the switch as it is now.
* A row recorded before automatic review does not claim it will be checked.
* A password, key or URL token written inside an ordinary field never reaches the reviewer.

Every case uses two real organisations.
"""

import json
import uuid

import pytest

from tests.test_change_review import PASS_ALL, FakeReviewer, _application, _ask_for_status, _row, _status_of, _user
from tests.test_change_review_gaps import _backdate, _waiting_change
from tests.test_genome_patch_flow import _element_count_by_name, _make_admin, _valid_patch

pytestmark = pytest.mark.usefixtures("db_session")


def _events(approval_id):
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog

    return [e.event for e in AIChatApprovalAuditLog.query.filter_by(approval_id=approval_id)
            .order_by(AIChatApprovalAuditLog.id).all()]


def _pause(org, user):
    from app.models.agent_oversight_state import AgentOversightState

    AgentOversightState.get_for_org(org.id).pause(user.id, "Audit week")


def _resume(org):
    from app.models.agent_oversight_state import AgentOversightState

    AgentOversightState.get_for_org(org.id).resume()


def _sweep(db_session, org, approval_id):
    from app.modules.ai_chat.services.change_review import ReviewBudget, sweep_organisation

    _backdate(approval_id)
    db_session.commit()
    return sweep_organisation(org.id, ReviewBudget())


def test_a_change_held_before_its_first_check_has_one_paused_no_checked_and_one_resumed_row(
    db_session, make_org, tenant_ctx, monkeypatch
):
    org, other = make_org("rec-a"), make_org("rec-b")
    user, other_user = _user(db_session, org.id), _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    other_ledger = _application(db_session, other.id, "Atlas %s" % uuid.uuid4().hex[:6], "development")
    FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _pause(org, user)
        db_session.commit()
        aid, verdict = _ask_for_status(user, ledger.name, "production")
        assert verdict["state"] == "in_review"
        for _ in range(3):
            _sweep(db_session, org, aid)
        held = _events(aid)
        assert held.count("paused") == 1, held
        assert held.count("checked") == 0, held
        assert held.count("resumed") == 0, held
        _resume(org)
        db_session.commit()
        _sweep(db_session, org, aid)
        done = _events(aid)
        assert done.count("paused") == 1 and done.count("resumed") == 1, done
        assert done.index("paused") < done.index("resumed") < done.index("applied"), done
        assert _status_of(ledger.id) == "production"
    # the other organisation never paused: its change settles with no paused or resumed row
    with tenant_ctx(other.id):
        other_id, other_verdict = _ask_for_status(other_user, other_ledger.name, "production")
        assert other_verdict["state"] == "applied", other_verdict
        assert "paused" not in _events(other_id) and "resumed" not in _events(other_id)


def test_a_change_paused_after_a_check_writes_no_further_checked_row_in_three_sweeps(
    db_session, make_org, tenant_ctx, monkeypatch
):
    org, other = make_org("rec-c"), make_org("rec-d")
    user, other_user = _user(db_session, org.id), _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    other_ledger = _application(db_session, other.id, "Atlas %s" % uuid.uuid4().hex[:6], "development")
    aid = _waiting_change(db_session, tenant_ctx, monkeypatch, org, user, ledger)
    other_id = _waiting_change(db_session, tenant_ctx, monkeypatch, other, other_user, other_ledger)
    FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        before = _events(aid).count("checked")
        _pause(org, user)
        db_session.commit()
        for _ in range(3):
            _sweep(db_session, org, aid)
        held = _events(aid)
        assert held.count("checked") == before, held
        assert held.count("paused") == 1, held
        _resume(org)
        db_session.commit()
        _sweep(db_session, org, aid)
        assert _events(aid).count("resumed") == 1
    with tenant_ctx(other.id):
        assert "paused" not in _events(other_id), "pausing one organisation leaves another's record alone"


def test_a_refused_approve_while_paused_leaves_one_paused_row_and_resume_one_resumed_row(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.modules.genome.patch.proposer import propose_genome_patch

    org, other = make_org("rec-e"), make_org("rec-f")
    requester = _make_admin(db_session, org, "recreq")
    approver = _make_admin(db_session, org, "recapp")
    other_admin = _make_admin(db_session, other, "recoth")
    name = "Paused Patch %s" % uuid.uuid4().hex[:6]
    FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _pause(org, requester)
        db_session.commit()
        proposed = propose_genome_patch(
            request_text="propose", user_id=requester.id, patch_source=lambda *_a, **_k: _valid_patch(org.id, name=name))
        aid = proposed["approval_id"]
        for _ in range(2):
            refused = AIChatApprovalService(approver.id).approve_and_execute(aid, approver.id)
            assert refused["success"] is False and refused["code"] == "IN_REVIEW"
        assert _events(aid).count("paused") == 1, _events(aid)
        assert _element_count_by_name(org.id, name) == 0
        _resume(org)
        db_session.commit()
        applied = AIChatApprovalService(approver.id).approve_and_execute(aid, approver.id)
        assert applied["success"] is True, applied
        events = _events(aid)
        assert events.count("paused") == 1 and events.count("resumed") == 1, events
        assert events.index("paused") < events.index("resumed") < events.index("approved"), events
        assert _element_count_by_name(org.id, name) == 1
    with tenant_ctx(other.id):
        # the other organisation's administrator cannot act on, or leave a row on, this record
        again = AIChatApprovalService(other_admin.id).approve_and_execute(aid, other_admin.id)
        assert again["success"] is False
    with tenant_ctx(org.id):
        assert _events(aid).count("paused") == 1


def test_the_record_says_the_pause_only_while_it_lasts(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services import change_review

    org, other = make_org("rec-g"), make_org("rec-h")
    user, other_user = _user(db_session, org.id), _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    other_ledger = _application(db_session, other.id, "Atlas %s" % uuid.uuid4().hex[:6], "development")
    aid = _waiting_change(db_session, tenant_ctx, monkeypatch, org, user, ledger)
    other_id = _waiting_change(db_session, tenant_ctx, monkeypatch, other, other_user, other_ledger)
    FakeReviewer([]).install(monkeypatch)
    with tenant_ctx(org.id):
        _pause(org, user)
        db_session.commit()
        _sweep(db_session, org, aid)
        assert change_review.describe(_row(aid))["waiting_reason"] == change_review.WRITES_PAUSED_REASON
        _resume(org)
        db_session.commit()
        # no sweep has run since: the record must still not claim writes are paused
        assert change_review.describe(_row(aid))["waiting_reason"] != change_review.WRITES_PAUSED_REASON
    with tenant_ctx(other.id):
        assert change_review.describe(_row(other_id))["waiting_reason"] != change_review.WRITES_PAUSED_REASON


def test_a_row_from_before_automatic_review_does_not_promise_a_check(db_session, make_org, tenant_ctx, monkeypatch, client, login_as):
    from app import db
    from app.modules.ai_chat.services import change_review
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService, create_approval_record

    org, other = make_org("rec-i"), make_org("rec-j")
    user, colleague = _user(db_session, org.id), _user(db_session, org.id)
    other_user = _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    payload = {"application_name": ledger.name, "new_status": "production", "rationale": "Asked for it"}
    with tenant_ctx(org.id):
        old = create_approval_record(
            organization_id=org.id, operation_type="tool_use", entity_type="update_application_status",
            original_command="old", operation_payload=payload, summary="Old change", user_id=colleague.id)
        db.session.commit()
        held_id = _waiting_change(db_session, tenant_ctx, monkeypatch, org, colleague, ledger)
        _pause(org, user)
        db.session.commit()
        items = {i["id"]: i for i in AIChatApprovalService(user.id).get_approver_queue()["approvals"]}
        assert items[old.id].get("pre_loop") is True and "has_check_record" not in items[old.id]
        assert items[old.id]["settled_by_review"] is False and items[old.id]["can_approve"] is False
        assert change_review.describe(_row(old.id))["waiting_reason"] is None, "its record promises nothing either"
        assert items[held_id]["held_by_pause"] is True and items[held_id].get("has_check_record") is True
        user.confirmed = True
        db.session.flush()
        login_as(client, user)
        html = client.get("/ai-chat/approvals/inbox").get_data(as_text=True)
    assert "approval-pre-loop-%d" % old.id in html
    assert "recorded before changes by the assistant were checked" in html
    assert "approval-held-%d" % old.id not in html and "approval-held-%d" % held_id in html
    pre_loop_card = html.split("approval-pre-loop-%d" % old.id)[1].split("approval-in-review")[0][:1500]
    assert "See how it was checked" not in pre_loop_card
    with tenant_ctx(other.id):
        assert AIChatApprovalService(other_user.id).get_approver_queue()["approvals"] == []


def test_a_password_key_and_url_token_in_an_ordinary_field_never_reach_the_reviewer(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services import llm_model_router as router
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.change_review import ReviewBudget
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.modules.ai_chat.tools.executor import ToolCall

    org, other = make_org("rec-k"), make_org("rec-l")
    user = _user(db_session, org.id)
    _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    words = "Connect with password=Hunter3Hunter3 and api_key=plainvalue12345 via https://x.io/a?token=urltoken99887766"
    args = {"application_name": ledger.name, "new_status": "production", "rationale": words}
    sent = []

    def fake_call(prompt, model, provider, max_tokens, timeout, _already_tried):
        sent.append(prompt)
        return json.dumps(PASS_ALL), None

    monkeypatch.setattr(LLMService, "_call_llm_with_failover", staticmethod(fake_call))
    monkeypatch.setattr(router.LLMModelRouter, "select_reviewer",
                        lambda self, p, m: router.ReviewerChoice("deepseek", "deepseek-reasoner", True))
    runner = AgentRunner(user_id=user.id, chat_session_id="rec-%s" % uuid.uuid4().hex[:6])
    runner._turn_id = "t"
    runner._review_budget = ReviewBudget()
    runner._drafter = ("deepseek", "deepseek-chat", "Move %s to production." % ledger.name)
    with tenant_ctx(org.id):
        runner._queue_approval(ToolCall(id="1", name="update_application_status", arguments=dict(args)))
    assert sent, "the reviewer was called"
    for secret in ("Hunter3Hunter3", "plainvalue12345", "urltoken99887766"):
        assert secret not in sent[0], secret
    for word in ("Connect with password=", "and api_key=", "via https://x.io/a?token="):
        assert word in sent[0], word
    assert ledger.name in sent[0]
