"""Settle one recorded change the way the scheduled review does, for tests that need the change applied."""


def settle_as_the_review(user_id, approval_id):
    """Check the change, have a second model (here one that passes everything) review it, and apply it.

    Returns the verdict with ``success`` true only when the change was applied, so a test reads the result the
    way it read an applied change before."""
    from _pytest.monkeypatch import MonkeyPatch

    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from tests.test_change_review import PASS_ALL, FakeReviewer

    with MonkeyPatch.context() as patch:
        FakeReviewer([PASS_ALL]).install(patch)
        body = AIChatApprovalService(user_id).settle_after_review(approval_id)
    applied = body.get("state") == "applied"
    return {**body, "success": applied}
