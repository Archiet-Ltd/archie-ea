"""The stop-all-writes switch stops every write, no secret reaches the reviewer in any form, and the remaining gaps
in the review of the review loop stay closed.

* While an organisation has paused writes, no change by any path (the chat's own create, update, delete and link,
  a tool call, a person's Approve, the sweep) is applied or sent to the reviewer; each is settled once they resume.
* Keys, tokens and card numbers are scrubbed from the final text sent to a model, by their shape, in JSON form too.
* A row from before the loop never counts as a repeat; a person who left the workspace has their change declined;
  the queue offers Approve only where it will act.

Every isolation case uses two real organisations.
"""

import json
import uuid
from datetime import datetime, timedelta

import pytest

from tests.test_change_review import PASS_ALL, FakeReviewer, _application, _ask_for_status, _fail, _row, _status_of, _user
from tests.test_change_review_gaps import (
    _backdate, _person_route, _person_service, _person_words, _waiting_change,
)

pytestmark = pytest.mark.usefixtures("db_session")


def _capability(db_session, org_id, name):
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(name=name, organization_id=org_id, level=1)
    db_session.add(cap)
    db_session.flush()
    return cap


def _pause(org, user):
    from app.models.agent_oversight_state import AgentOversightState

    AgentOversightState.get_for_org(org.id).pause(user.id, "Audit week")


def _resume(org):
    from app.models.agent_oversight_state import AgentOversightState

    AgentOversightState.get_for_org(org.id).resume()


def _chat_write(user, kind, cap_id, name):
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    service = AIChatApprovalService(user.id)
    session = "p-%s" % uuid.uuid4().hex[:6]
    if kind == "create":
        return service.create_pending_approval(
            "create", "capability", "create capability %s" % name, {"name": name, "description": "d"},
            "Create capability %s" % name, chat_session_id=session)
    if kind == "update":
        return service.create_pending_approval(
            "update", "capability", "rename capability", {"description": "A new description"},
            "Update capability %s" % name, entity_id=cap_id, chat_session_id=session)
    if kind == "delete":
        return service.create_pending_approval(
            "delete", "capability", "delete capability", {"id": cap_id}, "Delete capability %s" % name,
            entity_id=cap_id, chat_session_id=session)
    return service.create_pending_approval(
        "link", "capability_mapping", "link capability", {"capability_id": cap_id, "application_id": cap_id},
        "Link capability %s" % name, chat_session_id=session)


def _capability_state(org_id, name):
    from app import db
    from app.models.business_capabilities import BusinessCapability

    db.session.expire_all()
    row = BusinessCapability.query.filter_by(name=name, organization_id=org_id).first()
    return None if row is None else row.description


# --------------------------------------------------------------------------- #
# Defect 1: stop-all-writes stops every write                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["create", "update", "delete", "link"])
def test_the_chats_own_write_is_held_while_writes_are_paused_and_settled_when_they_resume(
    db_session, make_org, tenant_ctx, monkeypatch, kind
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services.change_review import WRITES_PAUSED_REASON, sweep_organisation

    org, other = make_org("stop-%s-a" % kind), make_org("stop-%s-b" % kind)
    user, other_user = _user(db_session, org.id), _user(db_session, other.id)
    name = "Cap %s" % uuid.uuid4().hex[:6]
    cap = _capability(db_session, org.id, name) if kind != "create" else None
    reviewer = FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    cap_id = cap.id if cap else None

    with tenant_ctx(org.id):
        _pause(org, user)
        db_session.flush()
        out = _chat_write(user, kind, cap_id, name)
        approval_id = out["approval_id"]
        row = _row(approval_id)
        assert row.status == ApprovalStatus.PENDING, out
        assert reviewer.review_prompts == [] and reviewer.selected_for == [], "a held change costs no review"
        assert WRITES_PAUSED_REASON in json.dumps(out), out
        before = _capability_state(org.id, name)
        assert (before is None) if kind == "create" else (before != "A new description")
        for _ in range(3):
            _backdate(approval_id)
            sweep_organisation(org.id)
        assert _row(approval_id).status == ApprovalStatus.PENDING
        assert reviewer.review_prompts == []

    # The other organisation never paused: its own write is reviewed and applied meanwhile.
    with tenant_ctx(other.id):
        other_name = "Cap %s" % uuid.uuid4().hex[:6]
        other_out = _chat_write(other_user, "create", None, other_name)
        assert other_out["approval_id"]
    assert len(reviewer.review_prompts) == 1, "the other organisation was reviewed"

    with tenant_ctx(org.id):
        _resume(org)
        db_session.flush()
        _backdate(approval_id)
        sweep_organisation(org.id)
        assert len(reviewer.review_prompts) == 2, "settled after writes resumed"
        after = _row(approval_id)
        assert after.status != ApprovalStatus.PENDING or kind == "link"
        if kind == "create":
            assert _capability_state(org.id, name) is not None


@pytest.mark.parametrize("shape", ["tool call", "chat create"])
@pytest.mark.parametrize("path", ["person_service", "person_route", "person_words"])
def test_a_persons_approve_applies_nothing_while_writes_are_paused(
    db_session, make_org, tenant_ctx, monkeypatch, client, login_as, path, shape
):
    from app.models.ai_chat_crud_approval import ApprovalStatus

    org, other = make_org("stop-p-a"), make_org("stop-p-b")
    user, other_user = _user(db_session, org.id), _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    other_ledger = _application(db_session, other.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    name = "Cap %s" % uuid.uuid4().hex[:6]
    if shape == "tool call":
        approval_id = _waiting_change(db_session, tenant_ctx, monkeypatch, org, user, ledger)
    else:
        FakeReviewer([], choice=None).install(monkeypatch)
        with tenant_ctx(org.id):
            approval_id = _chat_write(user, "create", None, name)["approval_id"]
            assert _row(approval_id).status == ApprovalStatus.PENDING
    other_id = _waiting_change(db_session, tenant_ctx, monkeypatch, other, other_user, other_ledger)
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _pause(org, user)
        db_session.flush()
        row = _row(approval_id)
        if path == "person_service":
            _person_service(user, approval_id, row)
        elif path == "person_route":
            _person_route(user, approval_id, row, client=client, login_as=login_as)
        else:
            _person_words(user, approval_id, row)
        assert _row(approval_id).status == ApprovalStatus.PENDING
        assert _status_of(ledger.id) == "development"
        assert _capability_state(org.id, name) is None
        assert reviewer.review_prompts == []
    with tenant_ctx(other.id):
        assert _row(other_id).status == ApprovalStatus.PENDING
        assert _status_of(other_ledger.id) == "development"


# --------------------------------------------------------------------------- #
# Defect 2: no secret in any form reaches a model                              #
# --------------------------------------------------------------------------- #

SECRETS = {
    "bare_openai": "sk-proj-Ab12Cd34Ef56Gh78Ij90Kl12",
    "bare_anthropic": "sk-ant-api03-Ab12Cd34Ef56Gh78Ij90",
    "bare_stripe": "sk_live_9f8e7d6c5b4a3f2e1d0c",
    "bare_aws": "AKIAIOSFODNN7EXAMPLE",
    "bare_github": "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
    "bearer": "Bearer abcDEF1234567890xyz",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
    "card_plain": "4111111111111111",
    "card_spaces": "4111 1111 1111 1111",
    "card_dashes": "5500-0000-0000-0004",
}
JSON_SECRET = "sk_json_9f8e7d6c5b4a3f2e"


def _needles():
    out = dict(SECRETS)
    out["json_value"] = JSON_SECRET
    for name, value in SECRETS.items():
        if name == "private_key":
            out["private_key_body"] = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
        if name.startswith("card_"):
            digits = "".join(ch for ch in value if ch.isdigit())
            out[name + "_last_group"] = digits[-4:]
    return out


def test_every_secret_shape_is_scrubbed_from_the_final_text_with_or_without_a_keyword():
    from app.modules.ai_chat.services.llm_service_impl import _scrub_prompt

    words = " ".join("%s is %s." % (k, v) for k, v in SECRETS.items())
    text = json.dumps({"words": words, "api_key": JSON_SECRET, "nested": {"client_secret": "zzzz-1234-yyyy"}}, indent=1)
    cleaned = _scrub_prompt(text)
    for name, value in SECRETS.items():
        if name == "private_key":
            assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" not in cleaned, name
        elif name.startswith("card_"):
            assert "".join(ch for ch in value if ch.isdigit())[-4:] not in cleaned, name
        else:
            assert value not in cleaned, name
    assert JSON_SECRET not in cleaned and "zzzz-1234-yyyy" not in cleaned
    assert '"api_key": "[CREDENTIAL_REDACTED]"' in cleaned, "the JSON stays well formed around the redaction"
    json.loads(cleaned)


def test_a_number_that_fails_the_card_check_is_left_alone():
    from app.modules.ai_chat.services.llm_service_impl import _scrub_prompt

    assert "[CARD_REDACTED]" in _scrub_prompt("pay with 4111 1111 1111 1111 now")
    assert "[CARD_REDACTED]" not in _scrub_prompt("order 9999999999999999")


def test_no_secret_shape_reaches_the_reviewer_the_reviser_or_the_chat_path(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services import llm_model_router as router
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.change_review import ReviewBudget
    from app.modules.ai_chat.services.llm_service_impl import LLMService, scrub_chat_messages
    from app.modules.ai_chat.tools.executor import ToolCall

    org = make_org("stop-scrub")
    user = _user(db_session, org.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    words = " ".join("%s is %s." % (k, v) for k, v in SECRETS.items())
    arguments = {"application_name": ledger.name, "new_status": "production", "rationale": words,
                 "api_key": JSON_SECRET}
    sent = []
    replies = [json.dumps(_fail(1, "Not quite.")), json.dumps({"arguments": arguments, "summary": "Same."})]

    def fake_call(prompt, model, provider, max_tokens, timeout, _already_tried):
        sent.append(prompt)
        return replies.pop(0), None

    monkeypatch.setattr(LLMService, "_call_llm_with_failover", staticmethod(fake_call))
    monkeypatch.setattr(router.LLMModelRouter, "select_reviewer",
                        lambda self, p, m: router.ReviewerChoice("deepseek", "deepseek-reasoner", True))
    runner = AgentRunner(user_id=user.id, chat_session_id="scrub-%s" % uuid.uuid4().hex[:6])
    runner._turn_id = "t"
    runner._review_budget = ReviewBudget()
    runner._drafter = ("deepseek", "deepseek-chat", "Move %s to production. %s" % (ledger.name, words))
    with tenant_ctx(org.id):
        runner._queue_approval(ToolCall(id="1", name="update_application_status", arguments=dict(arguments)))
    assert len(sent) == 2, "one review and one revision were sent"
    chat = scrub_chat_messages([{"role": "user", "content": words + json.dumps({"api_key": JSON_SECRET})}])
    texts = sent + [chat[0]["content"]]
    for needle_name, needle in _needles().items():
        for text in texts:
            assert needle not in text, needle_name
    assert ledger.name in sent[0], "the scrub keeps the data the reviewer needs"


# --------------------------------------------------------------------------- #
# Defect 4: a row from before the loop is never a repeat                       #
# --------------------------------------------------------------------------- #


def test_a_row_from_before_the_loop_does_not_make_a_new_request_a_repeat(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    org, other = make_org("stop-old-a"), make_org("stop-old-b")
    user, other_user = _user(db_session, org.id), _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    other_ledger = _application(db_session, other.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    payload = {"application_name": ledger.name, "new_status": "production", "rationale": "The founder asked for it"}
    with tenant_ctx(org.id):
        old = create_approval_record(
            organization_id=org.id, operation_type="tool_use", entity_type="update_application_status",
            original_command="old", operation_payload=payload, summary="old", user_id=user.id)
        db.session.commit()
        AIChatCRUDApproval.query.filter_by(id=old.id).update({"created_at": datetime.utcnow() - timedelta(days=30)})
        db.session.commit()
        assert old.rubric_version is None
    FakeReviewer([PASS_ALL, PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _id, verdict = _ask_for_status(user, ledger.name, "production")
        assert verdict["state"] == "applied", verdict
        assert "already" not in verdict["message"]
        assert _status_of(ledger.id) == "production"
        assert _row(old.id).status.value == "pending", "the old row is left as it was"
    # A repeat inside the loop is still caught, and another organisation's row is never counted.
    with tenant_ctx(other.id):
        _id, first = _ask_for_status(other_user, other_ledger.name, "production")
        assert first["state"] == "applied"
        _id, again = _ask_for_status(other_user, other_ledger.name, "production")
        assert again["state"] == "declined", again


# --------------------------------------------------------------------------- #
# Defect 5: a person who left the workspace has their change declined          #
# --------------------------------------------------------------------------- #


def test_the_sweep_declines_a_change_whose_requester_moved_to_another_workspace(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services.change_review import MOVED_REASON, sweep_organisation

    org, elsewhere = make_org("stop-left-a"), make_org("stop-left-b")
    user, stayer = _user(db_session, org.id), _user(db_session, org.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    kept = _application(db_session, org.id, "Kept %s" % uuid.uuid4().hex[:6], "development")
    aid = _waiting_change(db_session, tenant_ctx, monkeypatch, org, user, ledger)
    kept_id = _waiting_change(db_session, tenant_ctx, monkeypatch, org, stayer, kept)
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    user.organization_id = elsewhere.id
    db_session.flush()
    with tenant_ctx(org.id):
        _backdate(aid)
        _backdate(kept_id)
        first = sweep_organisation(org.id)
        row = _row(aid)
        assert row.status == ApprovalStatus.REJECTED and row.rejected_reason == MOVED_REASON, (first, row.status)
        assert _status_of(ledger.id) == "development"
        assert len(reviewer.review_prompts) == 1, "only the person who stayed was reviewed"
        assert _status_of(kept.id) == "production"
        second = sweep_organisation(org.id)
        assert second == {"applied": 0, "declined": 0, "in_review": 0}, "it ended: no sweep retries it"


# --------------------------------------------------------------------------- #
# Defect 6: Approve is offered only where it will act                          #
# --------------------------------------------------------------------------- #


def test_the_queue_offers_approve_only_where_it_will_act(db_session, make_org, tenant_ctx, monkeypatch, client, login_as):
    from app import db
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService, create_approval_record

    org, other = make_org("stop-q-a"), make_org("stop-q-b")
    user, colleague = _user(db_session, org.id), _user(db_session, org.id)
    other_user = _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    aid = _waiting_change(db_session, tenant_ctx, monkeypatch, org, user, ledger)
    with tenant_ctx(org.id):
        own = create_approval_record(
            organization_id=org.id, operation_type="agent_charter_change", entity_type="agent_registration",
            original_command="system: charter change", operation_payload={"a": 1}, summary="Charter change own",
            user_id=user.id)
        theirs = create_approval_record(
            organization_id=org.id, operation_type="agent_charter_change", entity_type="agent_registration",
            original_command="system: charter change", operation_payload={"a": 2}, summary="Charter change theirs",
            user_id=colleague.id)
        db.session.commit()
        items = {i["id"]: i for i in AIChatApprovalService(user.id).get_approver_queue()["approvals"]}
        assert items[own.id]["can_approve"] is False
        assert items[theirs.id]["can_approve"] is True
        assert items[aid]["can_approve"] is True and items[aid]["settled_by_review"] is True
        user.confirmed = True
        db.session.flush()
        login_as(client, user)
        html = client.get("/ai-chat/approvals/inbox").get_data(as_text=True)
    assert "approval-own-request-%d" % own.id in html
    assert "/ai-chat/approvals/%d/approve" % own.id not in html
    assert "/ai-chat/approvals/%d/approve" % theirs.id in html
    assert "approval-in-review-%d" % aid in html
    assert "waits here for a second person" not in html
    with tenant_ctx(other.id):
        assert AIChatApprovalService(other_user.id).get_approver_queue()["approvals"] == []
