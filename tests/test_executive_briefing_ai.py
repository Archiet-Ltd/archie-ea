"""Regression tests for the CTO-facing AI advisories: the executive briefing
on the Architecture Health Scorecard, and priority suggestions on the
Investment Priorities dashboard.

Both endpoints are advisory only — nothing they return is persisted. Both
reuse the same metrics/context their host page already assembles (via a
factored-out helper), so the LLM is only ever handed real, already-computed
numbers; never invented data (CLAUDE.md).

Uses the shared fixtures in tests/conftest.py (db_session rolls everything
back) and the logged-in-client / auth-cache pattern from
tests/test_arb_review_ai.py.
"""

from __future__ import annotations

import json
import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in_org(db_session, make_org, client):
    """A confirmed CTO user in a fresh org, logged into the test client."""
    from app.models.user import User

    org = make_org("exec-briefing-ai")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"cto-{suffix}@example.com",
        first_name="Chief",
        last_name="Technology",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="cto",
    )
    db_session.add(user)
    db_session.flush()

    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    _clear_auth_caches()
    return org, user


def _clear_auth_caches():
    """Anything that touches current_user on the shared app context re-caches
    an anonymous user in `g`; call this right before each test-client
    request."""
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


# ---------------------------------------------------------------------------
# Executive briefing — POST /dashboard/api/ai-executive-briefing
# ---------------------------------------------------------------------------

_VALID_BRIEFING_JSON = json.dumps(
    {
        "headline": "Portfolio risk is concentrated in the ARB pending queue.",
        "what_changed": ["ARB pending queue grew"],
        "risks": ["3 critical solution risks are unresolved"],
        "recommended_focus": ["Clear the ARB pending queue"],
        "rationale": "Pending reviews and critical risks both point to governance backlog as the top exposure.",
    }
)


def test_executive_briefing_happy_path(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module
    monkeypatch.setattr(
        svc_module.LLMService, "generate_from_prompt", staticmethod(lambda *a, **k: _VALID_BRIEFING_JSON)
    )

    _clear_auth_caches()
    resp = client.post("/dashboard/api/ai-executive-briefing")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    briefing = data["briefing"]
    assert briefing["headline"] == "Portfolio risk is concentrated in the ARB pending queue."
    assert briefing["what_changed"] == ["ARB pending queue grew"]
    assert briefing["risks"] == ["3 critical solution risks are unresolved"]
    assert briefing["recommended_focus"] == ["Clear the ARB pending queue"]
    assert "rationale" in briefing


def test_executive_briefing_llm_failure_is_502(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module

    def _boom(*a, **k):
        raise RuntimeError("LLM provider unavailable")

    monkeypatch.setattr(svc_module.LLMService, "generate_from_prompt", staticmethod(_boom))

    _clear_auth_caches()
    resp = client.post("/dashboard/api/ai-executive-briefing")
    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_executive_briefing_unparseable_llm_output_is_502(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module
    monkeypatch.setattr(
        svc_module.LLMService,
        "generate_from_prompt",
        staticmethod(lambda *a, **k: "not json at all"),
    )

    _clear_auth_caches()
    resp = client.post("/dashboard/api/ai-executive-briefing")
    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_executive_briefing_disabled_is_503(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: False)

    _clear_auth_caches()
    resp = client.post("/dashboard/api/ai-executive-briefing")
    assert resp.status_code == 503


def test_executive_briefing_advisory_only_health_scorecard_still_works(
    db_session, make_org, logged_in_org, client, monkeypatch
):
    """The briefing endpoint must not change what the scorecard page itself
    renders — it's purely additive/advisory."""
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    _clear_auth_caches()
    resp = client.get("/dashboard/health")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Architecture Health Scorecard" in html
    # The scorecard's own figures still render beside the advisory briefing...
    assert 'data-testid="health-total-solutions"' in html
    # ...and with nothing to measure it says so instead of inventing a score.
    assert 'data-testid="health-score-not-measured"' in html


# ---------------------------------------------------------------------------
# Investment priority suggestions — POST /architecture/api/investment-priorities/ai-suggest
# ---------------------------------------------------------------------------
#
# The full-portfolio analysis from InvestmentPrioritizationService spans
# every organization (UnifiedCapability / UnifiedApplicationCapabilityMapping
# carry no organization_id), so the route must filter capability_scores down
# to this org's own mapped footprint before it ever reaches the LLM. These
# fixture builders create a real CapabilityValueStreamMapping row so that
# filter (value_stream_ai_service._org_scoped_capability_names) has
# something org-scoped to find — see architecture_routes.
# ---------------------------------------------------------------------------


def _make_value_stream_with_stage(db_session, org):
    from app.models.unified_capability import ValueStream, ValueStreamStage

    suffix = uuid.uuid4().hex[:8]
    vs = ValueStream(
        name=f"Order to Cash {suffix}",
        code=f"VS-{suffix}",
        description="From customer order to cash collection.",
        organization_id=org.id,
    )
    db_session.add(vs)
    db_session.flush()

    stage = ValueStreamStage(
        name="Order Capture",
        description="Capture the customer order.",
        value_stream_id=vs.id,
        stage_order=1,
        organization_id=org.id,
    )
    db_session.add(stage)
    db_session.flush()
    return vs, stage


def _make_capability(db_session, name):
    from app.models.unified_capability import UnifiedCapability

    suffix = uuid.uuid4().hex[:8]
    cap = UnifiedCapability(name=name, code=f"CAP-{suffix}", level=1)
    db_session.add(cap)
    db_session.flush()
    return cap


def _map_capability_to_org(db_session, org, capability, value_stream, stage):
    """A tenant-scoped CapabilityValueStreamMapping row — this is what makes
    a capability show up in this org's AI-suggestion context (see
    value_stream_ai_service._org_scoped_capability_names). organization_id
    is set explicitly since these rows are built outside a live request
    (TenantMixin's auto-set-on-flush needs g.current_org_id)."""
    from app.models.unified_capability import CapabilityValueStreamMapping

    mapping = CapabilityValueStreamMapping(
        capability_id=capability.id,
        value_stream_id=value_stream.id,
        value_stream_stage_id=stage.id,
        organization_id=org.id,
    )
    db_session.add(mapping)
    db_session.flush()
    return mapping


def _capability_score_entry(capability, priority_level="CRITICAL", score=85):
    return {
        "capability_id": capability.id,
        "capability_name": capability.name,
        "investment_priority_score": score,
        "priority_level": priority_level,
        "strategic_score": 20,
        "coverage_score": 20,
        "maturity_score": 20,
        "risk_score": 20,
    }


_VALID_SUGGESTIONS_JSON = json.dumps(
    {
        "suggestions": [
            {"item": "Fund the top critical-priority capability", "priority": "now", "rationale": "Highest total score and unresolved risk."},
            {"item": "Plan the high-priority capability for next quarter", "priority": "next", "rationale": "Strong strategic score, moderate coverage."},
        ],
        "summary": "Two capabilities dominate the critical/high investment tiers and should be sequenced accordingly.",
    }
)

_INVALID_ENUM_SUGGESTIONS_JSON = json.dumps(
    {
        "suggestions": [
            {"item": "Fund the top capability", "priority": "urgent", "rationale": "..."},
        ],
        "summary": "One suggestion with a bad priority.",
    }
)

_MIXED_VALID_INVALID_SUGGESTIONS_JSON = json.dumps(
    {
        "suggestions": [
            {"item": "Fund the top capability", "priority": "now", "rationale": "Real one."},
            {"item": "", "priority": "next", "rationale": "Empty item should be dropped."},
        ],
        "summary": "Mixed suggestions.",
    }
)

_ALL_DROPPED_SUGGESTIONS_JSON = json.dumps(
    {
        "suggestions": [
            {"item": "", "priority": "now", "rationale": "Empty item."},
            {"item": "   ", "priority": "next", "rationale": "Blank item."},
        ],
        "summary": "All suggestions are empty.",
    }
)


def test_investment_suggestions_happy_path(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module
    monkeypatch.setattr(
        svc_module.LLMService, "generate_from_prompt", staticmethod(lambda *a, **k: _VALID_SUGGESTIONS_JSON)
    )

    vs, stage = _make_value_stream_with_stage(db_session, org)
    cap_critical = _make_capability(db_session, "Payments Platform")
    cap_high = _make_capability(db_session, "Customer 360")
    _map_capability_to_org(db_session, org, cap_critical, vs, stage)
    _map_capability_to_org(db_session, org, cap_high, vs, stage)

    from app.modules.architecture.routes import architecture_routes as routes_module

    def _fake_analysis():
        return (
            {
                "capability_scores": [
                    _capability_score_entry(cap_critical, "CRITICAL", 85),
                    _capability_score_entry(cap_high, "HIGH", 65),
                ],
                "critical_investments": [{"capability_name": "Payments Platform"}],
                "high_investments": [{"capability_name": "Customer 360"}],
                "medium_investments": [],
                "low_investments": [],
                "portfolio_metrics": {"total_capabilities": 2, "critical_priorities": 1, "high_priorities": 1},
                "recommendations": [],
            },
            1,
        )

    monkeypatch.setattr(routes_module, "_assemble_investment_priorities_context", _fake_analysis)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert len(data["suggestions"]) == 2
    assert {s["priority"] for s in data["suggestions"]} <= {"now", "next", "later"}
    assert "summary" in data


def test_investment_suggestions_llm_failure_is_502(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module

    def _boom(*a, **k):
        raise RuntimeError("LLM provider unavailable")

    monkeypatch.setattr(svc_module.LLMService, "generate_from_prompt", staticmethod(_boom))

    vs, stage = _make_value_stream_with_stage(db_session, org)
    cap = _make_capability(db_session, "Fraud Detection")
    _map_capability_to_org(db_session, org, cap, vs, stage)

    from app.modules.architecture.routes import architecture_routes as routes_module

    def _fake_analysis():
        return (
            {
                "capability_scores": [_capability_score_entry(cap, "CRITICAL", 85)],
                "critical_investments": [{"capability_name": cap.name}],
                "high_investments": [],
                "medium_investments": [],
                "low_investments": [],
                "portfolio_metrics": {"total_capabilities": 1},
                "recommendations": [],
            },
            1,
        )

    monkeypatch.setattr(routes_module, "_assemble_investment_priorities_context", _fake_analysis)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_investment_suggestions_unparseable_llm_output_is_502(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module
    monkeypatch.setattr(
        svc_module.LLMService,
        "generate_from_prompt",
        staticmethod(lambda *a, **k: "not json at all"),
    )

    vs, stage = _make_value_stream_with_stage(db_session, org)
    cap = _make_capability(db_session, "Fraud Detection")
    _map_capability_to_org(db_session, org, cap, vs, stage)

    from app.modules.architecture.routes import architecture_routes as routes_module

    def _fake_analysis():
        return (
            {
                "capability_scores": [_capability_score_entry(cap, "CRITICAL", 85)],
                "critical_investments": [{"capability_name": cap.name}],
                "high_investments": [],
                "medium_investments": [],
                "low_investments": [],
                "portfolio_metrics": {"total_capabilities": 1},
                "recommendations": [],
            },
            1,
        )

    monkeypatch.setattr(routes_module, "_assemble_investment_priorities_context", _fake_analysis)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_investment_suggestions_disabled_is_503(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: False)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 503


def test_investment_suggestions_invalid_priority_enum_is_502(db_session, make_org, logged_in_org, client, monkeypatch):
    """A single suggestion with a priority outside {now, next, later} is
    dropped by the parser; if nothing survives, the endpoint errors rather
    than returning an empty/fabricated list."""
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module
    monkeypatch.setattr(
        svc_module.LLMService,
        "generate_from_prompt",
        staticmethod(lambda *a, **k: _INVALID_ENUM_SUGGESTIONS_JSON),
    )

    vs, stage = _make_value_stream_with_stage(db_session, org)
    cap = _make_capability(db_session, "Fraud Detection")
    _map_capability_to_org(db_session, org, cap, vs, stage)

    from app.modules.architecture.routes import architecture_routes as routes_module

    def _fake_analysis():
        return (
            {
                "capability_scores": [_capability_score_entry(cap, "CRITICAL", 85)],
                "critical_investments": [{"capability_name": cap.name}],
                "high_investments": [],
                "medium_investments": [],
                "low_investments": [],
                "portfolio_metrics": {"total_capabilities": 1},
                "recommendations": [],
            },
            1,
        )

    monkeypatch.setattr(routes_module, "_assemble_investment_priorities_context", _fake_analysis)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_investment_suggestions_drops_empty_items_but_keeps_valid_ones(
    db_session, make_org, logged_in_org, client, monkeypatch
):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module
    monkeypatch.setattr(
        svc_module.LLMService,
        "generate_from_prompt",
        staticmethod(lambda *a, **k: _MIXED_VALID_INVALID_SUGGESTIONS_JSON),
    )

    vs, stage = _make_value_stream_with_stage(db_session, org)
    cap = _make_capability(db_session, "Fraud Detection")
    _map_capability_to_org(db_session, org, cap, vs, stage)

    from app.modules.architecture.routes import architecture_routes as routes_module

    def _fake_analysis():
        return (
            {
                "capability_scores": [_capability_score_entry(cap, "CRITICAL", 85)],
                "critical_investments": [{"capability_name": cap.name}],
                "high_investments": [],
                "medium_investments": [],
                "low_investments": [],
                "portfolio_metrics": {"total_capabilities": 1},
                "recommendations": [],
            },
            1,
        )

    monkeypatch.setattr(routes_module, "_assemble_investment_priorities_context", _fake_analysis)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert len(data["suggestions"]) == 1
    assert data["suggestions"][0]["item"] == "Fund the top capability"


def test_investment_suggestions_all_dropped_is_502(db_session, make_org, logged_in_org, client, monkeypatch):
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module
    monkeypatch.setattr(
        svc_module.LLMService,
        "generate_from_prompt",
        staticmethod(lambda *a, **k: _ALL_DROPPED_SUGGESTIONS_JSON),
    )

    vs, stage = _make_value_stream_with_stage(db_session, org)
    cap = _make_capability(db_session, "Fraud Detection")
    _map_capability_to_org(db_session, org, cap, vs, stage)

    from app.modules.architecture.routes import architecture_routes as routes_module

    def _fake_analysis():
        return (
            {
                "capability_scores": [_capability_score_entry(cap, "CRITICAL", 85)],
                "critical_investments": [{"capability_name": cap.name}],
                "high_investments": [],
                "medium_investments": [],
                "low_investments": [],
                "portfolio_metrics": {"total_capabilities": 1},
                "recommendations": [],
            },
            1,
        )

    monkeypatch.setattr(routes_module, "_assemble_investment_priorities_context", _fake_analysis)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_investment_suggestions_page_still_works(db_session, make_org, logged_in_org, client, monkeypatch):
    """The refactor that extracted _assemble_investment_priorities_context()
    must not change the page route's own behaviour: with no capability
    mapping rows, it still renders the prereq template."""
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    _clear_auth_caches()
    resp = client.get("/architecture/investment-priorities")
    assert resp.status_code == 200


def test_investment_suggestions_empty_org_footprint_is_200_null_without_llm(
    db_session, make_org, logged_in_org, client, monkeypatch
):
    """This org has mapped nothing, but the whole-portfolio analysis still
    contains capability scores (from other orgs, or none at all). The route
    must short-circuit to a null-suggestion 200 without ever calling the
    LLM — same pattern as value_stream_ai_routes.ai_suggest_mappings."""
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module

    def _must_not_be_called(*a, **k):
        raise AssertionError("LLM must not be called when this org has no mapped capabilities")

    monkeypatch.setattr(svc_module.LLMService, "generate_from_prompt", staticmethod(_must_not_be_called))

    # A capability exists in the raw (cross-org) analysis, but this org has
    # never mapped it via CapabilityValueStreamMapping or
    # UnifiedApplicationCapabilityMapping, so it must not survive the filter.
    unmapped_cap = _make_capability(db_session, "Unmapped Elsewhere")

    from app.modules.architecture.routes import architecture_routes as routes_module

    def _fake_analysis():
        return (
            {
                "capability_scores": [_capability_score_entry(unmapped_cap, "CRITICAL", 85)],
                "critical_investments": [{"capability_name": unmapped_cap.name}],
                "high_investments": [],
                "medium_investments": [],
                "low_investments": [],
                "portfolio_metrics": {"total_capabilities": 1},
                "recommendations": [],
            },
            1,
        )

    monkeypatch.setattr(routes_module, "_assemble_investment_priorities_context", _fake_analysis)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["suggestions"] is None
    assert "message" in data


def test_investment_suggestions_cross_org_capability_not_in_prompt(
    db_session, make_org, logged_in_org, client, monkeypatch
):
    """The whole-portfolio analysis mixes in another org's capability. The
    prompt handed to the LLM must contain this org's own mapped capability
    name, and must NOT contain the other org's capability name — proving the
    cross-org egress is closed, not merely capped."""
    org, user = logged_in_org

    from app.services.feature_flag_service import FeatureFlagService
    monkeypatch.setattr(FeatureFlagService, "is_ai_enabled", lambda feature: True)

    vs, stage = _make_value_stream_with_stage(db_session, org)
    own_cap = _make_capability(db_session, "Payments Platform")
    _map_capability_to_org(db_session, org, own_cap, vs, stage)

    # Another org's capability — present in the raw cross-org analysis but
    # never mapped by this org.
    other_org_cap = _make_capability(db_session, "Confidential Org B Roadmap Capability")

    captured_prompts = []

    import app.modules.dashboard.v2.services.executive_briefing_service as svc_module

    def _capture(prompt, *a, **k):
        captured_prompts.append(prompt)
        return _VALID_SUGGESTIONS_JSON

    monkeypatch.setattr(svc_module.LLMService, "generate_from_prompt", staticmethod(_capture))

    from app.modules.architecture.routes import architecture_routes as routes_module

    def _fake_analysis():
        return (
            {
                "capability_scores": [
                    _capability_score_entry(own_cap, "CRITICAL", 85),
                    _capability_score_entry(other_org_cap, "CRITICAL", 90),
                ],
                "critical_investments": [
                    {"capability_name": own_cap.name},
                    {"capability_name": other_org_cap.name},
                ],
                "high_investments": [],
                "medium_investments": [],
                "low_investments": [],
                "portfolio_metrics": {"total_capabilities": 2},
                "recommendations": [],
            },
            1,
        )

    monkeypatch.setattr(routes_module, "_assemble_investment_priorities_context", _fake_analysis)

    _clear_auth_caches()
    resp = client.post("/architecture/api/investment-priorities/ai-suggest")
    assert resp.status_code == 200, resp.get_data(as_text=True)

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert own_cap.name in prompt
    assert other_org_cap.name not in prompt
