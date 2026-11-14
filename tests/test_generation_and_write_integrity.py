"""Writes that corrupt the system of record.

Three defects found by an adversarial QA sweep against a live server, each
reproduced at least twice before being fixed here:

1. ``POST /api/archimate/generate/from-vendors`` with an empty body, as ANY
   logged-in user, created 435 business actors from the global vendor catalogue
   with no dedup, no preview and no cap. Repeating it added 435 more each time
   (435 -> 870 -> 1306), and the duplicates then fed the product's own
   duplicate-detection module.
2. ``POST /applications/create`` deduplicated sequential repeats but not
   concurrent ones: five simultaneous identical posts produced five rows,
   because check-then-insert has no lock or constraint behind it.
3. ``GET /health`` and ``GET /ai-chat/token-usage`` disagreed in the same
   process about whether an LLM was configured, and ``POST /ai-chat/message``
   returned ``success: true`` on a failed LLM call while leaking the upstream
   provider's ``user_id`` to the browser.

These are behavioural tests against the real endpoints and the real database --
the defects were all invisible to code reading.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _org_and_user(db_session, make_org, role="enterprise_architect"):
    from app.models.user import User

    org = make_org("writes")
    sfx = uuid.uuid4().hex[:10]
    user = User(
        email=f"{role}-{sfx}@example.com",
        organization_id=org.id,
        enterprise_role=role,
        confirmed=True,
    )
    user.password = "Passw0rd!123"
    db_session.add(user)
    db_session.flush()
    return org, user


# --------------------------------------------------------------------------- #
# 1. Mass generation                                                            #
# --------------------------------------------------------------------------- #


def test_generation_is_idempotent(db_session, make_org, client, login_as):
    """A repeat run creates nothing. It used to create the whole model again."""
    from app.models import ArchiMateElement
    from app.models.vendor.vendor_organization import VendorOrganization

    org, user = _org_and_user(db_session, make_org)
    sfx = uuid.uuid4().hex[:8]
    vendor_ids = []
    for i in range(3):
        v = VendorOrganization(name=f"IdemVendor {sfx} {i}")
        db_session.add(v)
        db_session.flush()
        vendor_ids.append(v.id)

    def _count():
        return (
            db_session.query(ArchiMateElement)
            .filter(ArchiMateElement.organization_id == org.id)
            .count()
        )

    before = _count()
    login_as(client, user)
    first = client.post(
        "/api/archimate/generate/from-vendors", json={"vendor_ids": vendor_ids}
    )
    assert first.status_code == 200, first.get_data(as_text=True)
    after_first = _count()
    assert after_first > before, "generation wrote nothing at all"

    login_as(client, user)
    second = client.post(
        "/api/archimate/generate/from-vendors", json={"vendor_ids": vendor_ids}
    )
    assert second.status_code == 200
    body = second.get_json()["data"]
    assert _count() == after_first, "the repeat run duplicated the model"
    assert body["elements_created"] == 0
    assert body["elements_skipped_existing"] > 0


def test_generation_dry_run_writes_nothing(db_session, make_org, client, login_as):
    from app.models import ArchiMateElement
    from app.models.vendor.vendor_organization import VendorOrganization

    org, user = _org_and_user(db_session, make_org)
    sfx = uuid.uuid4().hex[:8]
    v = VendorOrganization(name=f"DryVendor {sfx}")
    db_session.add(v)
    db_session.flush()

    def _count():
        return (
            db_session.query(ArchiMateElement)
            .filter(ArchiMateElement.organization_id == org.id)
            .count()
        )

    before = _count()
    login_as(client, user)
    resp = client.post(
        "/api/archimate/generate/from-vendors",
        json={"vendor_ids": [v.id], "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["dry_run"] is True
    assert data["elements_would_create"] > 0
    assert data["elements_created"] == 0
    assert _count() == before, "a dry run wrote to the database"


def test_generation_requires_architect(db_session, make_org, client, login_as):
    """A viewer could mass-create into the model. Architect and above now."""
    _org, viewer = _org_and_user(db_session, make_org, role="viewer")
    login_as(client, viewer)
    resp = client.post("/api/archimate/generate/from-vendors", json={})
    assert resp.status_code == 403
    assert resp.get_json()["your_role"] == "viewer"


def test_generation_cap_aborts_rather_than_truncating(
    db_session, make_org, client, login_as, monkeypatch
):
    """Above the cap, nothing is written -- a half-written model is worse."""
    from app.models import ArchiMateElement
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.modules.architecture.services.archimate_rules_engine import (
        ArchiMateRulesEngine,
    )

    org, user = _org_and_user(db_session, make_org)
    monkeypatch.setattr(ArchiMateRulesEngine, "MAX_ELEMENTS_PER_COMMIT", 1)
    sfx = uuid.uuid4().hex[:8]
    ids = []
    for i in range(3):
        v = VendorOrganization(name=f"CapVendor {sfx} {i}")
        db_session.add(v)
        db_session.flush()
        ids.append(v.id)

    before = (
        db_session.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == org.id)
        .count()
    )
    login_as(client, user)
    resp = client.post("/api/archimate/generate/from-vendors", json={"vendor_ids": ids})
    assert resp.status_code == 413
    assert (
        db_session.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == org.id)
        .count()
        == before
    )


def test_capability_generation_shares_the_same_controls(
    db_session, make_org, client, login_as
):
    """The sibling route had the identical shape and the identical defect."""
    _org, viewer = _org_and_user(db_session, make_org, role="viewer")
    login_as(client, viewer)
    assert (
        client.post("/api/archimate/generate/from-capabilities", json={}).status_code
        == 403
    )


def test_generated_relationships_have_real_endpoints(db_session, make_org):
    """Endpoints were read before flush, so relationships were written with
    source_id/target_id NULL -- a relationship joining nothing."""
    from flask import g

    from app.models import ArchiMateRelationship
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.modules.architecture.services.archimate_core_service import (
        ArchiMateService,
    )

    org = make_org("rels")
    sfx = uuid.uuid4().hex[:8]
    v = VendorOrganization(name=f"RelVendor {sfx}")
    db_session.add(v)
    db_session.flush()

    g.current_org_id = org.id
    result = ArchiMateService().generate_architecture_from_vendors([v.id])
    assert result["commit_success"] is True

    orphans = (
        db_session.query(ArchiMateRelationship)
        .filter(ArchiMateRelationship.source_id.is_(None))
        .count()
    )
    assert orphans == 0


# --------------------------------------------------------------------------- #
# 2. Concurrent duplicate create                                                #
# --------------------------------------------------------------------------- #


def test_lock_is_scoped_to_one_name(db_session, make_org):
    """Two different names must not block each other."""
    from flask import g

    from app.models.application_portfolio import ApplicationComponent
    from app.utils.duplicate_guard import lock_name_for_write

    org = make_org("locks")
    g.current_org_id = org.id
    assert lock_name_for_write(ApplicationComponent, "Alpha") is True
    assert lock_name_for_write(ApplicationComponent, "Beta") is True
    # Same name twice in the same transaction is re-entrant, not a deadlock.
    assert lock_name_for_write(ApplicationComponent, "Alpha") is True
    # A blank name has no identity to lock.
    assert lock_name_for_write(ApplicationComponent, "   ") is False


# --------------------------------------------------------------------------- #
# 3. One answer about the LLM                                                   #
# --------------------------------------------------------------------------- #


def test_health_and_token_usage_agree(
    db_session, make_org, client, login_as, monkeypatch
):
    """They disagreed in the same process: /health counted api_settings rows,
    token-usage asked the resolver. An environment-configured provider has no
    row, so /health told an operator the AI was dead while it was serving."""
    import app._bootstrap.routes as bootstrap_routes
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    _org, user = _org_and_user(db_session, make_org)

    monkeypatch.setattr(
        LLMService,
        "_get_configured_provider",
        staticmethod(lambda *a, **k: ("openrouter", "some/model")),
    )
    # /health caches for a TTL; this test asserts a fresh read.
    bootstrap_routes._health_cache["result"] = None
    try:
        login_as(client, user)
        health = client.get("/health").get_json()["checks"]["llm_providers"]
        login_as(client, user)
        usage = client.get("/ai-chat/token-usage").get_json()

        assert health["configured"] is True
        assert usage["configured"] is True
        assert health["provider"] == usage["provider"] == "openrouter"
        # The row count is reported as detail, never as the verdict.
        assert "db_enabled_providers" in health
    finally:
        bootstrap_routes._health_cache["result"] = None


def test_health_reports_unconfigured_when_the_resolver_says_so(
    db_session, make_org, client, login_as, monkeypatch
):
    import app._bootstrap.routes as bootstrap_routes
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    _org, user = _org_and_user(db_session, make_org)

    def _raise(*a, **k):
        raise ValueError("No enabled LLM provider found with a configured model.")

    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(_raise))
    bootstrap_routes._health_cache["result"] = None
    try:
        login_as(client, user)
        health = client.get("/health").get_json()["checks"]["llm_providers"]
        assert health["configured"] is False
        assert health["status"] == "warning"
    finally:
        bootstrap_routes._health_cache["result"] = None


def test_failed_llm_call_does_not_report_success_and_does_not_leak(
    db_session, make_org, client, login_as, monkeypatch
):
    from app.modules.ai_chat.services.agent_runner import AgentRunner
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    _org, user = _org_and_user(db_session, make_org)

    # The route is feature-gated on the same resolver; without a provider it
    # 503s before the agent runs and the envelope under test is never built.
    monkeypatch.setattr(
        LLMService,
        "_get_configured_provider",
        staticmethod(lambda *a, **k: ("openrouter", "some/model")),
    )

    raw = (
        "LLM call failed: Error code: 402 - {'error': {'message': 'More credits "
        "are required'}, 'user_id': 'user_2abcSECRET'}"
    )
    monkeypatch.setattr(
        AgentRunner,
        "run",
        lambda self, **kw: {
            "response": "The AI request could not be completed.",
            "actions_taken": [],
            "pending_approvals": [],
            "error": raw,
        },
    )

    login_as(client, user)
    resp = client.post("/ai-chat/message", json={"message": "hi", "domain": "general"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    payload = resp.get_json()

    assert payload["success"] is False, "a failed LLM call reported success"
    assert payload["agent_error"], "the failure must still be flagged to the UI"
    assert "user_2abcSECRET" not in body, "provider internals reached the client"
    assert "More credits are required" not in body
    # The user-visible copy is honest and stays as it is.
    assert "could not be completed" in payload["response"]


def test_sanitize_agent_error_categories():
    from app.modules.ai_chat.services.agent_runner import sanitize_agent_error

    assert sanitize_agent_error(None) is None
    assert (
        "no llm provider is configured"
        in sanitize_agent_error("No API keys configured").lower()
    )
    # Rate limit errors should say to try again shortly
    rate_limit = sanitize_agent_error("429 rate limit exceeded for org_9xy")
    assert "rate limit" in rate_limit.lower()
    assert "try again" in rate_limit.lower() or "wait" in rate_limit.lower()
    assert "org_9xy" not in rate_limit
    # Credit-balance errors should say credit ran out and who can fix it
    credit_balance = sanitize_agent_error("Error code: 402 - credits, user_id: u_1abc")
    assert "credit" in credit_balance.lower()
    assert "run out" in credit_balance.lower() or "exhausted" in credit_balance.lower()
    assert "administrator" in credit_balance.lower() or "support" in credit_balance.lower()
    assert "u_1abc" not in credit_balance
    # Other credit-balance phrases
    insufficient = sanitize_agent_error("insufficient credits")
    assert "credit" in insufficient.lower()
    assert "run out" in insufficient.lower() or "exhausted" in insufficient.lower()
    assert "administrator" in insufficient.lower() or "support" in insufficient.lower()
    quota_exceeded = sanitize_agent_error("quota exceeded")
    assert "credit" in quota_exceeded.lower()
    assert "run out" in quota_exceeded.lower() or "exhausted" in quota_exceeded.lower()
    assert "administrator" in quota_exceeded.lower() or "support" in quota_exceeded.lower()
    billing = sanitize_agent_error("billing error")
    assert "credit" in billing.lower()
    assert "run out" in billing.lower() or "exhausted" in billing.lower()
    assert "administrator" in billing.lower() or "support" in billing.lower()
