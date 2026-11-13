"""A run of the change review never spends past the month's remaining AI budget.

Before each reviewer call the review asks the one headroom check the platform already keeps (LLMService).
When it says no, nothing is sent to a paid model, the change stays in review with a reason the page shows,
and the run ends; the next run tries again. The number of calls in one run is set from the measured cost
of a call.
"""

import json
import uuid

import pytest

from tests.test_change_review import FakeReviewer, PASS_ALL, _application, _row, _user
from tests.test_change_review_gaps import _ask_for_status, _backdate, _waiting_change


def _count_calls(monkeypatch):
    """A reviewer that answers every review and counts the calls that reach it."""
    from app.modules.ai_chat.services import llm_model_router as router

    calls = []

    def call_model(self, choice, prompt, timeout=90.0):
        calls.append(choice.ref)
        return json.dumps(PASS_ALL)

    monkeypatch.setattr(router.LLMModelRouter, "select_reviewer",
                        lambda self, p, m: router.ReviewerChoice("deepseek", "deepseek-v4-pro", True))
    monkeypatch.setattr(router.LLMModelRouter, "call_model", call_model)
    return calls


def _waiting(db_session, tenant_ctx, monkeypatch, org, user, how_many):
    changes = []
    for _ in range(how_many):
        app_obj = _application(db_session, org.id, "Budget %s" % uuid.uuid4().hex[:6], "development")
        approval_id = _waiting_change(db_session, tenant_ctx, monkeypatch, org, user, app_obj)
        _backdate(approval_id)
        changes.append((approval_id, app_obj))
    return changes


def _status_of(application_id):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    db.session.expire_all()
    return ApplicationComponent.query.filter_by(id=application_id).one().deployment_status


def test_when_the_month_has_no_room_no_reviewer_is_called_and_the_page_says_why(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services.change_review import (
        BUDGET_USED_UP_REASON, ReviewBudget, describe, sweep_organisation,
    )
    from app.modules.ai_chat.services.llm_service_impl import LLMService
    from app.models.ai_chat_crud_approval import ApprovalStatus

    org = make_org("budget-out")
    user = _user(db_session, org.id)
    first, second = _waiting(db_session, tenant_ctx, monkeypatch, org, user, 2)
    calls = _count_calls(monkeypatch)
    monkeypatch.setattr(LLMService, "headroom", staticmethod(lambda provider, require_ceiling=True: (False, "at ceiling")))

    with tenant_ctx(org.id):
        budget = ReviewBudget()
        counts = sweep_organisation(org.id, budget)
        assert calls == []
        assert budget.used == 0
        assert counts["applied"] == 0 and counts["declined"] == 0
        for approval_id, app_obj in (first, second):
            assert _row(approval_id).status == ApprovalStatus.PENDING
            assert _status_of(app_obj.id) == "development"
        shown = describe(_row(first[0]))
        assert shown["verdict"] == "In review"
        assert shown["waiting_reason"] == BUDGET_USED_UP_REASON
        assert "month's AI budget is used up" in shown["waiting_reason"]
        # The run ended at the first change, so the second was not even looked at.
        assert describe(_row(second[0]))["waiting_reason"] != BUDGET_USED_UP_REASON


def test_a_change_stopped_by_the_month_is_not_counted_against_its_attempts_and_is_made_on_a_later_run(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.modules.ai_chat.services.change_review import MAX_WAITS, ReviewBudget, sweep_organisation
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    org = make_org("budget-later")
    user = _user(db_session, org.id)
    ((approval_id, app_obj),) = _waiting(db_session, tenant_ctx, monkeypatch, org, user, 1)
    calls = _count_calls(monkeypatch)
    closed = staticmethod(lambda provider, require_ceiling=True: (False, "at ceiling"))
    with monkeypatch.context() as month:
        month.setattr(LLMService, "headroom", closed)
        with tenant_ctx(org.id):
            for _ in range(MAX_WAITS + 2):
                _backdate(approval_id)
                sweep_organisation(org.id, ReviewBudget())
            assert _row(approval_id).status == ApprovalStatus.PENDING
    assert calls == []
    # A new month (or a raised ceiling): the same change is reviewed and made, never declined for lack of a reviewer.
    _backdate(approval_id)
    with tenant_ctx(org.id):
        counts = sweep_organisation(org.id, ReviewBudget())
        assert counts["applied"] == 1, counts
        assert _status_of(app_obj.id) == "production"
    assert len(calls) == 1


def test_a_call_that_would_take_the_month_past_its_ceiling_is_not_sent(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.change_review import BUDGET_USED_UP_REASON, ReviewBudget, describe, sweep_organisation
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    org = make_org("budget-edge")
    user = _user(db_session, org.id)
    ((approval_id, app_obj),) = _waiting(db_session, tenant_ctx, monkeypatch, org, user, 1)
    calls = _count_calls(monkeypatch)
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "1.00")
    # Under the ceiling by one cent, but one review may cost more than a cent.
    monkeypatch.setattr(LLMService, "spend_this_month", staticmethod(lambda provider: (0.99, 40)))
    with tenant_ctx(org.id):
        sweep_organisation(org.id, ReviewBudget())
        assert calls == []
        assert describe(_row(approval_id))["waiting_reason"] == BUDGET_USED_UP_REASON
        assert _status_of(app_obj.id) == "development"
    # With room for a full-price call the same change is reviewed and made.
    monkeypatch.setattr(LLMService, "spend_this_month", staticmethod(lambda provider: (0.50, 20)))
    _backdate(approval_id)
    with tenant_ctx(org.id):
        assert sweep_organisation(org.id, ReviewBudget())["applied"] == 1
    assert len(calls) == 1


def test_a_run_stops_at_the_call_cap_and_the_cap_is_a_small_share_of_the_month(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app.modules.ai_chat.services import change_review as review
    from app.modules.ai_chat.services import llm_model_router as router
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    # The prompt of a normal change (a one-field status change), as the review builds it for a real record.
    org = make_org("budget-cap")
    user = _user(db_session, org.id)
    app_obj = _application(db_session, org.id, "Cap %s" % uuid.uuid4().hex[:6], "development")
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)
    with tenant_ctx(org.id):
        _ask_for_status(user, app_obj.name, "production")
    (_ref, real_prompt), = reviewer.review_prompts
    prompt_tokens = len(real_prompt) // 4 + 1  # the same count _month_has_room uses

    calls = []
    monkeypatch.setattr(router.LLMModelRouter, "call_model",
                        lambda self, choice, prompt, timeout=90.0: calls.append(prompt) or "{}")
    monkeypatch.setattr(review, "_month_has_room", lambda choice, prompt: True)
    budget = review.ReviewBudget()
    assert budget.limit == review.CALLS_PER_RUN
    choice = router.ReviewerChoice("deepseek", "deepseek-v4-pro", True)
    with pytest.raises(review.BudgetSpent):
        for _ in range(review.CALLS_PER_RUN + 5):
            review._ask(router.LLMModelRouter(), choice, "review this", budget)
    assert len(calls) == review.CALLS_PER_RUN

    # One call at its dearest (the model's reply at full length, at the dearest price) times the cap stays
    # within one per cent of the USD 62 monthly ceiling.
    price_in, price_out = LLMService.price_per_mtok("deepseek", "deepseek-v4-pro")
    dearest_call = prompt_tokens * price_in / 1e6 + review.MAX_REPLY_TOKENS * price_out / 1e6
    assert dearest_call == pytest.approx(0.0338, abs=0.0001)
    assert review.CALLS_PER_RUN * dearest_call <= 0.01 * 62


def test_the_reply_size_the_cap_is_costed_at_is_the_size_a_call_asks_for():
    import inspect

    from app.modules.ai_chat.services import change_review as review
    from app.modules.ai_chat.services import llm_model_router as router

    assert "max_tokens=%d" % review.MAX_REPLY_TOKENS in inspect.getsource(router.LLMModelRouter.call_model)


def test_a_vendor_another_workspace_named_is_not_found_by_this_workspaces_review(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app import db
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    mine = make_org("vendor-mine")
    theirs = make_org("vendor-theirs")
    my_user = _user(db_session, mine.id)
    their_user = _user(db_session, theirs.id)
    suffix = uuid.uuid4().hex[:8]
    their_vendor = VendorOrganization(name="Theirs %s" % suffix, website="https://old.example.com",
                                      named_by_organization_id=theirs.id)
    my_vendor = VendorOrganization(name="Mine %s" % suffix, website="https://old.example.com",
                                   named_by_organization_id=mine.id)
    db.session.add_all([their_vendor, my_vendor])
    db.session.flush()
    reviewer = FakeReviewer([PASS_ALL]).install(monkeypatch)

    def ask(org, user, vendor):
        with tenant_ctx(org.id):
            return create_approval_record(
                organization_id=org.id, operation_type="update", entity_type="vendor", entity_id=vendor.id,
                summary="Change the vendor website", user_id=user.id, original_command="change the website",
                operation_payload={"website": "https://new.example.com"}, settle=True).review_verdict

    other = ask(mine, my_user, their_vendor)
    assert other["state"] == "declined" and "no longer exists in this workspace" in other["message"], other
    # The workspace that named a vendor still finds it (it is then refused only because the list is shared).
    own = ask(mine, my_user, my_vendor)
    assert own["state"] == "declined" and "shared by every workspace" in own["message"], own
    # And the other workspace sees its own vendor, not mine.
    assert "no longer exists" in ask(theirs, their_user, my_vendor)["message"]
    assert "shared by every workspace" in ask(theirs, their_user, their_vendor)["message"]
    db.session.expire_all()
    assert VendorOrganization.query.filter_by(id=their_vendor.id).one().website == "https://old.example.com"
    assert reviewer.review_prompts == []


def test_a_vendor_from_the_platform_list_is_read_by_the_review_of_every_workspace(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from app import db
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    first = make_org("platform-vendor-a")
    second = make_org("platform-vendor-b")
    users = {first.id: _user(db_session, first.id), second.id: _user(db_session, second.id)}
    platform_vendor = VendorOrganization(name="Platform %s" % uuid.uuid4().hex[:8],
                                         website="https://old.example.com", named_by_organization_id=None)
    db.session.add(platform_vendor)
    db.session.flush()
    FakeReviewer([PASS_ALL]).install(monkeypatch)

    for org in (first, second):
        with tenant_ctx(org.id):
            verdict = create_approval_record(
                organization_id=org.id, operation_type="update", entity_type="vendor",
                entity_id=platform_vendor.id, summary="Change the vendor website", user_id=users[org.id].id,
                original_command="change the website", operation_payload={"website": "https://new.example.com"},
                settle=True).review_verdict
        # Found (not "no longer exists"); refused only because the platform list is shared by every workspace.
        assert verdict["state"] == "declined" and "shared by every workspace" in verdict["message"], verdict
        assert "no longer exists" not in verdict["message"]
    db.session.expire_all()
    assert VendorOrganization.query.filter_by(id=platform_vendor.id).one().website == "https://old.example.com"
