"""A pause pressed at any moment holds a change until the moment it would apply, no secret reaches a model in any
form a person can paste or the code can write out, and what the chat and the Approvals page say about a held,
settled or waiting change is true.

Every isolation case uses two real organisations.
"""

import json
import uuid

import pytest

from tests.test_change_review import PASS_ALL, FakeReviewer, _application, _ask_for_status, _row, _status_of, _user
from tests.test_change_review_gaps import _backdate, _waiting_change
from tests.test_change_review_stop_writes_and_scrub import _chat_write, _pause, _resume

pytestmark = pytest.mark.usefixtures("db_session")


def _build(*parts):
    """A synthetic secret assembled at run time, so the source holds no literal that looks like a real one."""
    return "".join(parts)


def _luhn_number(prefix, length):
    from app.modules.ai_chat.services.llm_service_impl import _luhn_ok

    for check in "0123456789":
        number = prefix + "7" * (length - len(prefix) - 1) + check
        if _luhn_ok(number):
            return number
    raise AssertionError("no check digit")


KEY = _build("sk", "-proj-", "AbCdEf0123456789GhIjKlMn")
ANT = _build("sk", "-ant-", "api03-AbCdEf0123456789GhIjKlMn")
AWS = _build("AKIA", "IOSFODNN7EXAMPLE")
GH = _build("ghp", "_", "a1B2c3D4e5" * 4)
STRIPE = _build("sk", "_live_", "a1B2c3D4e5" * 3)
GITLAB = _build("glpat", "-", "a1B2c3D4e5a1B2c3D4e5")
HF = _build("hf", "_", "a1B2c3D4e5a1B2c3D4e5a1B2c3D4e5")
SENDGRID = _build("SG", ".", "a1B2c3D4e5a1B2c3D4e5", ".", "a1B2c3D4e5a1B2c3D4e5")
SLACK_APP = _build("xapp", "-1-", "A012345-a1b2c3d4e5")
SLACK_EXP = _build("xoxe", "-1-", "a1b2c3d4e5f6a1b2c3d4")
WEBHOOK = _build("whsec", "_", "a1B2c3D4e5a1B2c3D4e5")
JWT = _build("eyJhbGciOiJIUzI1NiJ9", ".", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", ".", "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
PEM_BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
PEM = _build("-----BEGIN ", "PRIVATE KEY-----\n", PEM_BODY, "\n-----END ", "PRIVATE KEY-----")
CARD16 = "4111111111111111"
CARD19 = _luhn_number("453201", 19)
PLAIN = "plainvalue12345"
HUNTER = "Hunter2Hunter2"
BASIC = "dXNlcjpwYXNzd29yZDEyMw=="

# (a description, the text, the secret text that must not reach a model)
SHAPES = [
    ("openai key", "key is %s ok" % KEY, KEY),
    ("anthropic key", "key is %s ok" % ANT, ANT),
    ("aws id", "id %s ok" % AWS, AWS),
    ("github token", "t %s ok" % GH, GH),
    ("stripe key", "t %s ok" % STRIPE, STRIPE),
    ("gitlab token", "t %s ok" % GITLAB, GITLAB),
    ("hugging face token", "t %s ok" % HF, HF),
    ("sendgrid key", "t %s ok" % SENDGRID, SENDGRID),
    ("slack app token", "t %s ok" % SLACK_APP, SLACK_APP),
    ("slack config token", "t %s ok" % SLACK_EXP, SLACK_EXP),
    ("webhook secret", "t %s ok" % WEBHOOK, WEBHOOK),
    ("jwt", "t %s" % JWT, JWT),
    ("private key", PEM, PEM_BODY),
    ("private key in json", json.dumps({"k": PEM}), PEM_BODY),
    ("key after underscore", "OPENAI_KEY_%s" % KEY, KEY),
    ("key after a letter", "prefix%s" % KEY, KEY),
    ("key after a quote", "'%s'" % KEY, KEY),
    ("key inside json", json.dumps({"note": "use " + KEY}), KEY),
    ("json api_key", json.dumps({"api_key": PLAIN}), PLAIN),
    ("json api_key with spaces", '{ "api_key" :   "%s" }' % PLAIN, PLAIN),
    ("json api_key2", json.dumps({"api_key2": PLAIN}), PLAIN),
    ("json access_key", json.dumps({"access_key": PLAIN}), PLAIN),
    ("json private_key", json.dumps({"private_key": PLAIN}), PLAIN),
    ("json accessToken", json.dumps({"accessToken": PLAIN}), PLAIN),
    ("json refresh_token", json.dumps({"refresh_token": PLAIN}), PLAIN),
    ("json client-secret", json.dumps({"client-secret": PLAIN}), PLAIN),
    ("json authorization basic", json.dumps({"Authorization": "Basic " + BASIC}), BASIC),
    ("json password", json.dumps({"password": HUNTER}), HUNTER),
    ("json value with a quote", json.dumps({"password": 'ab"cdEFGHIJKL'}), "cdEFGHIJKL"),
    ("escaped json", json.dumps({"rationale": json.dumps({"api_key": PLAIN})}), PLAIN),
    ("escaped json password", json.dumps({"rationale": json.dumps({"password": HUNTER})}), HUNTER),
    ("python repr", str({"api_key": "PlainValue12345"}), "PlainValue12345"),
    ("yaml", "api_key: PlainValue12345", "PlainValue12345"),
    ("short mixed password", "password=Hunter2!", "Hunter2!"),
    ("bearer header", "Authorization: Bearer abcDEF123456", "abcDEF123456"),
    ("bearer jwt header", "Authorization: Bearer %s" % JWT, JWT),
    ("bearer in headers", json.dumps({"headers": {"Authorization": "Bearer abcDEF123456"}}), "abcDEF123456"),
    ("basic header", "Authorization: Basic " + BASIC, BASIC),
    ("address credentials", "postgresql://admin:S3cretPassw0rd@db.internal:5432/x", "S3cretPassw0rd"),
    ("card 16", "card %s" % CARD16, CARD16),
    ("card spaced", "card 4111 1111 1111 1111", "4111 1111 1111 1111"),
    ("card dashed", "card 4111-1111-1111-1111", "4111-1111-1111-1111"),
    ("card dotted", "card 4111.1111.1111.1111", "1111.1111"),
    ("card 19", "card %s" % CARD19, CARD19),
    ("card in json number", json.dumps({"pan": int(CARD16)}), CARD16),
    ("amex", "card 378282246310005", "378282246310005"),
]


@pytest.mark.parametrize("name,text,needle", SHAPES, ids=[s[0] for s in SHAPES])
def test_no_secret_shape_survives_the_scrub_on_either_path(name, text, needle):
    from app.modules.ai_chat.services.llm_service_impl import _scrub_prompt, scrub_chat_messages

    for mask_pii in (False, True):
        assert needle not in _scrub_prompt(text, mask_pii=mask_pii), (name, mask_pii)
    sent = scrub_chat_messages([{"role": "user", "content": text}])[0]["content"]
    assert needle not in sent, name


def test_a_secret_in_a_tool_calls_input_or_arguments_does_not_reach_the_chat_model():
    from app.modules.ai_chat.services.llm_service_impl import scrub_chat_messages

    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "update_application_status",
             "input": {"application_name": "Ledger", "api_key": PLAIN, "headers": {"Authorization": "Bearer abcDEF123456"},
                       "notes": "also %s" % KEY, "max_tokens": 4000}},
        ]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "update_application_status",
                "arguments": json.dumps({"application_name": "Ledger", "client_secret": HUNTER, "token_count": 12,
                                         "rationale": json.dumps({"access_key": PLAIN})})}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "key %s" % KEY}]},
        ]},
    ]
    sent = json.dumps(scrub_chat_messages(messages))
    for needle in (PLAIN, "abcDEF123456", KEY, HUNTER):
        assert needle not in sent, needle
    assert "Ledger" in sent and '\\"token_count\\": 12' in sent and '"max_tokens": 4000' in sent
    assert messages[0]["content"][0]["input"]["api_key"] == PLAIN, "the original is not changed"


ORDINARY = [
    "The ARB approved the waiver for the mainframe batch scheduler until 2027-03-31.",
    "Contract CT-2041 with the hosting vendor renews on 2027-06-30.",
    "The annual run cost of the ERP platform is 12500000 euros across all regions.",
    "Budget for the data platform is 12 500 000 euros over three years.",
    "Purchase order 4532015112830367 covers the storage expansion for the analytics cluster.",
    "Order number 1234567890123456 was raised by procurement.",
    "Atlas 71721659 is the name of the pilot application.",
    "Application 20240115 is retired at the end of the quarter.",
    "Upgrade the gateway to 10.2.4.1000 this quarter.",
    "The network range 10.20.30.0/24 hosts the shared integration services.",
    "The token service issues credentials to callers on the platform.",
    "Clients present bearer tokens to the gateway.",
    "The gateway uses bearer authentication for the partner APIs.",
    "Bearer certificates are not in use at the bank.",
    "The treasury holds bearer instruments.",
    "API key rotation happens every ninety days.",
    "Secret management is owned by the platform team.",
    "Password resets go through the service desk now.",
    "The task-management-service owns the workflow definitions.",
    "See risk-register-entry-20240 for the open risk.",
    "We use sk-learn-pipeline-v2 for scoring in the analytics team.",
    "The sk-class handover is on Friday.",
    "Element sk-capability-map-0042 belongs to the capability map.",
    "Project code AKIAPROJECTCODE000001 is closed.",
    "Market ASIAPACIFICREGION2026 is live.",
    "The max_tokens setting for the assistant is 4000.",
    "The token_service component is owned by the identity team.",
    "The token_count column holds the number of tokens used.",
    "Keyboard shortcuts and monkey patching are out of scope for this change.",
    "The secrets_manager integration is on the roadmap.",
    "Go-live of the payments hub is planned for 2027-09-01.",
    "From 2027-03-31 to 2027-06-30 the freeze applies.",
    "Peak volume is 1500000 messages per day on the settlement topic.",
    "The server rack serial 20240115001 is listed in the asset register.",
    "Rename the capability Customer Onboarding to Client Onboarding.",
    "Set the application Ledger to production.",
    "Link the capability Payments to the application Ledger.",
    "Owner of the CRM is Maria Garcia from the sales operations team.",
    "The capability maturity of Billing is level 3 of 5.",
    "Retire the application Legacy Billing by Q4.",
    "The vendor Contoso supplies the identity platform under contract 77.",
    "The interface between Orders and Fulfilment is a message queue.",
    "Total cost of ownership for the warehouse system is 3400000 over five years.",
    "The application is hosted in the eu-west-1 region on three nodes.",
    "The migration of the data warehouse finishes in 18 weeks.",
    "The reviewer must check that the name Atlas Project 98765432 is spelled as asked.",
    "Authentication is handled by the identity provider, not by the application.",
    "The access policy for the data lake is reviewed every quarter.",
    "A private network connects the two data centres.",
    "Encryption at rest is enabled for the customer database.",
    "Signing of release artefacts is done in the build pipeline.",
    "The client portal shows the order history for each customer.",
    "Credentials for the partner API are held by the platform team in a vault.",
]


def test_ordinary_architecture_text_comes_through_the_reviewers_scrub_unchanged():
    from app.modules.ai_chat.services.llm_service_impl import _scrub_prompt

    assert len(ORDINARY) >= 50
    changed = [t for t in ORDINARY if _scrub_prompt(t, mask_pii=False) != t]
    assert changed == []
    as_data = {"max_tokens": 4000, "token_service": "Okta", "token_count": 3, "name": "Atlas 71721659",
               "due": "2027-03-31", "order": "4532015112830367", "keyboard": "us", "monkey": "patch"}
    from app.modules.ai_chat.services.llm_service_impl import scrub_for_model

    assert scrub_for_model(as_data, mask_pii=False) == as_data


# --------------------------------------------------------------------------- #
# Through the real review loop                                                  #
# --------------------------------------------------------------------------- #


def _loop_stub(monkeypatch, replies, on_call=None):
    from app.modules.ai_chat.services import llm_model_router as router
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    sent = []

    def fake_call(prompt, model, provider, max_tokens, timeout, _already_tried):
        sent.append(prompt)
        if on_call:
            on_call()
        return replies.pop(0), None

    monkeypatch.setattr(LLMService, "_call_llm_with_failover", staticmethod(fake_call))
    monkeypatch.setattr(router.LLMModelRouter, "select_reviewer",
                        lambda self, p, m: router.ReviewerChoice("deepseek", "deepseek-reasoner", True))
    return sent


def test_pasted_json_and_named_keys_never_reach_the_reviewer_or_the_reviser_and_names_do(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.change_review import ReviewBudget
    from app.modules.ai_chat.tools.executor import ToolCall
    from tests.test_change_review import _fail

    org = make_org("midway-scrub")
    user = _user(db_session, org.id)
    suffix = "".join(str(uuid.uuid4().int)[:8])
    ledger = _application(db_session, org.id, "Atlas %s" % suffix, "development")
    pasted = json.dumps({"api_key": PLAIN, "access_key": PLAIN + "b", "private_key": PLAIN + "c", "api_key2": PLAIN + "d",
                         "headers": {"Authorization": "Bearer abcDEF123456"}, "max_tokens": 4000})
    rationale = "Config pasted by the founder: %s and the repr %s and card %s" % (
        pasted, str({"password": HUNTER}), CARD16)
    arguments = {"application_name": ledger.name, "new_status": "production", "rationale": rationale,
                 "client_secret": "client-secret-value-1"}
    sent = _loop_stub(monkeypatch, [json.dumps(_fail(1, "Not quite.")),
                                    json.dumps({"arguments": arguments, "summary": "Same."})])
    runner = AgentRunner(user_id=user.id, chat_session_id="midway-%s" % uuid.uuid4().hex[:6])
    runner._turn_id = "t"
    runner._review_budget = ReviewBudget()
    runner._drafter = ("deepseek", "deepseek-chat", "Move %s to production. %s" % (ledger.name, rationale))
    with tenant_ctx(org.id):
        runner._queue_approval(ToolCall(id="1", name="update_application_status", arguments=dict(arguments)))
    assert len(sent) == 2, "one review and one revision were sent"
    for needle in (PLAIN, PLAIN + "b", PLAIN + "c", PLAIN + "d", "abcDEF123456", HUNTER, CARD16, "client-secret-value-1"):
        for text in sent:
            assert needle not in text, needle
    for text in sent:
        assert ledger.name in text, "the reviewer sees the name it has to check, all digits included"
        assert "[PHONE_REDACTED]" not in text
    assert '"max_tokens": 4000' in sent[0] or "max_tokens" in sent[0]


# --------------------------------------------------------------------------- #
# The pause is read until the moment a change would apply                       #
# --------------------------------------------------------------------------- #


def test_a_pause_pressed_during_a_review_holds_the_change_and_it_is_applied_once_after_resume(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, ApprovalStatus
    from app.modules.ai_chat.services.change_review import (
        WRITES_PAUSED_REASON, ReviewBudget, sweep_organisation,
    )

    org, other = make_org("midway-a"), make_org("midway-b")
    user, other_user = _user(db_session, org.id), _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    other_ledger = _application(db_session, other.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    other_id = _waiting_change(db_session, tenant_ctx, monkeypatch, other, other_user, other_ledger)
    waiting_id = _waiting_change(db_session, tenant_ctx, monkeypatch, org, user, ledger)

    calls = []

    def press_pause():
        # the person presses the switch while the reviewer is still thinking
        _pause(org, user)
        db_session.commit()

    from app.modules.ai_chat.services import llm_model_router as router

    def call_model(self, choice, prompt, timeout=90.0):
        calls.append(prompt)
        if len(calls) == 1:
            press_pause()
        return json.dumps(PASS_ALL)

    monkeypatch.setattr(router.LLMModelRouter, "select_reviewer",
                        lambda self, p, m: router.ReviewerChoice("deepseek", "deepseek-reasoner", True))
    monkeypatch.setattr(router.LLMModelRouter, "call_model", call_model)

    with tenant_ctx(org.id):
        _backdate(waiting_id)
        db_session.commit()
        sweep_organisation(org.id, ReviewBudget())
        db_session.expire_all()
        assert _row(waiting_id).status == ApprovalStatus.PENDING, "paused during the review: nothing applied"
        assert _status_of(ledger.id) == "development"
        assert len(calls) == 1
        for _ in range(3):
            _backdate(waiting_id)
            db_session.commit()
            sweep_organisation(org.id, ReviewBudget())
        assert len(calls) == 1, "no further review while paused"
        assert _status_of(ledger.id) == "development"
        held = AIChatApprovalAuditLog.query.filter_by(approval_id=waiting_id, event="paused").all()
        assert len(held) == 1 and held[0].reason == WRITES_PAUSED_REASON, "one audit row, not one per sweep"
        # the other organisation never paused
    with tenant_ctx(other.id):
        _backdate(other_id)
        db_session.commit()
        sweep_organisation(other.id, ReviewBudget())
        assert _status_of(other_ledger.id) == "production"

    calls_before_resume = len(calls)
    with tenant_ctx(org.id):
        _resume(org)
        db_session.commit()
        for _ in range(2):
            _backdate(waiting_id)
            db_session.commit()
            sweep_organisation(org.id, ReviewBudget())
        db_session.expire_all()
        assert _status_of(ledger.id) == "production"
        assert _row(waiting_id).status == ApprovalStatus.APPROVED
        applied = AIChatApprovalAuditLog.query.filter_by(approval_id=waiting_id, event="applied").count()
        released = AIChatApprovalAuditLog.query.filter_by(approval_id=waiting_id, event="resumed").count()
        assert applied == 1 and released == 1
        assert len(calls) == calls_before_resume, "the review that already passed is not paid for twice"


def test_the_chat_says_a_held_change_is_held_because_writes_are_paused(db_session, make_org, tenant_ctx, monkeypatch):
    org = make_org("midway-words")
    user = _user(db_session, org.id)
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    name = "Cap %s" % uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        _pause(org, user)
        db_session.flush()
        out = _chat_write(user, "create", None, name)
    assert out["status"] == "pending_approval"
    assert "paused for this workspace" in out["message"] and "when they resume" in out["message"]
    assert "No independent reviewer" not in out["message"]
    assert reviewer.review_prompts == []


def test_a_persons_approve_of_a_record_outside_the_loop_waits_for_resume_and_stays_pending(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app import db
    import app.modules.ai_chat.services.ai_chat_approval_service as svc_mod
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService, create_approval_record

    org, other = make_org("midway-p-a"), make_org("midway-p-b")
    requester, approver = _user(db_session, org.id), _user(db_session, org.id)
    other_requester = _user(db_session, other.id)
    written = []

    class _Data:
        def __init__(self, user_id):
            self.user_id = user_id

        def create_capability(self, payload):
            written.append(payload)
            return {"success": True, "id": 1}

    monkeypatch.setattr(svc_mod, "AIDataInteractionService", _Data)
    with tenant_ctx(org.id):
        record = create_approval_record(
            organization_id=org.id, operation_type="create", entity_type="capability",
            original_command="backfilled", operation_payload={"name": "Backfilled"}, summary="Backfilled",
            user_id=requester.id)
        record.source_table = "confidence_review_items"
        record.source_id = 1
        db.session.commit()
        record_id = record.id
        _pause(org, requester)
        db.session.commit()
        refused = AIChatApprovalService(approver.id).approve_and_execute(record_id, approver.id)
        assert refused["success"] is False and "paused" in refused["error"], refused
        assert _row(record_id).status == ApprovalStatus.PENDING and written == []
        _resume(org)
        db.session.commit()
        done = AIChatApprovalService(approver.id).approve_and_execute(record_id, approver.id)
        assert done["success"] is True, done
        assert _row(record_id).status == ApprovalStatus.APPROVED and len(written) == 1
    with tenant_ctx(other.id):
        assert AIChatApprovalService(other_requester.id).get_approver_queue()["approvals"] == []


# --------------------------------------------------------------------------- #
# What the Approvals page says                                                  #
# --------------------------------------------------------------------------- #


def test_the_approvals_page_claims_a_settlement_only_for_a_change_with_a_recorded_review(
    db_session, make_org, tenant_ctx, monkeypatch, client, login_as
):
    from app import db
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService, create_approval_record

    org, other = make_org("midway-q-a"), make_org("midway-q-b")
    user, colleague = _user(db_session, org.id), _user(db_session, org.id)
    admin = _user(db_session, org.id, "Administrator")
    other_user = _user(db_session, other.id)
    ledger = _application(db_session, org.id, "Ledger %s" % uuid.uuid4().hex[:6], "development")
    waiting_id = _waiting_change(db_session, tenant_ctx, monkeypatch, org, user, ledger)
    with tenant_ctx(org.id):
        older = create_approval_record(
            organization_id=org.id, operation_type="tool_use", entity_type="update_application_status",
            original_command="old", operation_payload={"application_name": "x", "new_status": "production"},
            summary="Older change", user_id=colleague.id)
        connector = create_approval_record(
            organization_id=org.id, operation_type="update", entity_type="connectors",
            original_command="connector", operation_payload={"id": 1}, summary="Connector change",
            user_id=user.id)
        connector_by_colleague = create_approval_record(
            organization_id=org.id, operation_type="update", entity_type="connectors",
            original_command="connector", operation_payload={"id": 2}, summary="Connector change two",
            user_id=colleague.id)
        db.session.commit()
        items = {i["id"]: i for i in AIChatApprovalService(user.id).get_approver_queue()["approvals"]}
        assert items[waiting_id]["settled_by_review"] is True, "it has recorded checks and a review is coming"
        assert items[older.id]["settled_by_review"] is False, "a row from before the loop has no recorded review"
        assert items[connector_by_colleague.id]["can_approve"] is False
        assert "administrator" in items[connector_by_colleague.id]["approve_note"]
        admin_items = {i["id"]: i for i in AIChatApprovalService(admin.id).get_approver_queue()["approvals"]}
        assert admin_items[connector_by_colleague.id]["can_approve"] is True

        _pause(org, user)
        db.session.commit()
        paused = {i["id"]: i for i in AIChatApprovalService(user.id).get_approver_queue()["approvals"]}
        assert paused[waiting_id]["held_by_pause"] is True and paused[waiting_id]["settled_by_review"] is False
        assert paused[connector_by_colleague.id]["can_approve"] is False
        assert "paused" in paused[connector_by_colleague.id]["approve_note"]
        assert paused[older.id]["can_approve"] is False and paused[older.id]["held_by_pause"] is True
        user.confirmed = True
        db.session.flush()
        login_as(client, user)
        html = client.get("/ai-chat/approvals/inbox").get_data(as_text=True)
    assert "approval-held-%d" % waiting_id in html and "approval-in-review-%d" % waiting_id not in html
    assert "approval-in-review-%d" % older.id not in html
    assert "/ai-chat/approvals/%d/approve" % older.id not in html
    assert "approval-own-request-%d" % connector.id in html
    with tenant_ctx(other.id):
        assert AIChatApprovalService(other_user.id).get_approver_queue()["approvals"] == []
