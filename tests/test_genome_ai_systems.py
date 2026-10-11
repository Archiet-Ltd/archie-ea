"""PILLAR 6 — the AI Systems genome projection.

Uses the shared fixtures in tests/conftest.py (db_session rolls back).
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.modules.enterprise_genome.emit.ai_systems_register import emit_ai_systems_register
from app.modules.enterprise_genome.services.ai_system_classification import (
    RISK_TIERS,
    propose_classification,
    accept_classification,
    reject_classification,
    get_classification,
    can_mark_released,
    mark_released,
    propose_assessment,
    accept_assessment,
    release_refusal_reason,
)
from app.modules.enterprise_genome.services.ai_system_profile import (
    RETIRED_MODEL_IDS,
    model_currency,
    profile_from_element,
    risk_flags,
)
from app.modules.enterprise_genome.services.ai_systems_seed import (
    ARCHIE_COPILOT_NAME,
    register_ai_system,
    seed_archie_copilot,
)
from app.modules.enterprise_genome.services.ai_systems_slice import build_ai_systems_slice


@pytest.fixture
def org(make_org):
    return make_org("genome-ai")


def _seed_three(session, org_id):
    """One retired-model system, one high-autonomy ungoverned system, one with
    no governance recorded at all."""
    retired = register_ai_system(
        session, org_id,
        name="Legacy Classifier",
        provider="anthropic",
        model_id="claude-3-5-sonnet-20241022",  # on the denylist
        purpose="ticket routing",
        autonomy_level="assisted",
        data_sensitivity="internal",
        approval_gate=True,
        human_review=True,
    )
    ungoverned = register_ai_system(
        session, org_id,
        name="Autonomous Trader",
        provider="openai",
        model_id="gpt-4o",
        purpose="executes trades",
        autonomy_level="autonomous",
        data_sensitivity="confidential",
        approval_gate=False,   # high autonomy + no approval gate = risk
        human_review=False,
    )
    bare = register_ai_system(
        session, org_id,
        name="Mystery Bot",
        provider="anthropic",
        model_id="claude-opus-5",
        purpose="unclear",
        # no autonomy, no data sensitivity, no governance recorded
    )
    session.flush()
    return retired, ungoverned, bare


def test_model_currency_uses_retired_denylist():
    # retired ids are exactly the denylist
    assert model_currency("claude-3-5-sonnet-20241022") == "retired"
    assert "claude-3-5-sonnet-20241022" in RETIRED_MODEL_IDS
    # a current default model
    assert model_currency("claude-opus-5") == "current"
    assert model_currency("gpt-4o") == "current"
    # a real-but-unsupported id
    assert model_currency("gpt-3.5-turbo") == "stale"
    # nothing recorded -> unknown, never a guess
    assert model_currency("") == "unknown"
    assert model_currency(None) == "unknown"


def test_slice_projects_with_provenance_and_hash(db_session, org):
    _seed_three(db_session, org.id)
    slice_data = build_ai_systems_slice(org.id, db_session)

    assert slice_data["kind"] == "ai_systems"
    assert slice_data["organization_id"] == org.id
    assert slice_data["counts"]["total"] == 3
    # every projected system carries structural provenance
    for s in slice_data["systems"]:
        assert isinstance(s["archimate_element_id"], int)
        assert s["archimate_element_id"] > 0
    assert slice_data["spec_hash"].startswith("sha256:")


def test_slice_is_deterministic_byte_identical(db_session, org):
    _seed_three(db_session, org.id)
    a = build_ai_systems_slice(org.id, db_session)
    b = build_ai_systems_slice(org.id, db_session)
    assert a == b
    assert a["spec_hash"] == b["spec_hash"]
    # emitter is deterministic too
    assert str(emit_ai_systems_register(a)) == str(emit_ai_systems_register(b))


def test_retired_model_flagged(db_session, org):
    retired, _, _ = _seed_three(db_session, org.id)
    slice_data = build_ai_systems_slice(org.id, db_session)
    by_id = {s["archimate_element_id"]: s for s in slice_data["systems"]}
    p = by_id[retired.id]
    assert p["model_currency"] == "retired"
    assert "retired-model" in p["risk_flags"]


def test_ungoverned_high_autonomy_flagged(db_session, org):
    _, ungoverned, _ = _seed_three(db_session, org.id)
    slice_data = build_ai_systems_slice(org.id, db_session)
    by_id = {s["archimate_element_id"]: s for s in slice_data["systems"]}
    p = by_id[ungoverned.id]
    assert p["autonomy_level"] == "autonomous"
    assert p["governance"]["approval_gate"] is False
    assert "ungoverned-high-autonomy" in p["risk_flags"]


def test_no_governance_shows_unknown_not_fabricated(db_session, org):
    _, _, bare = _seed_three(db_session, org.id)
    slice_data = build_ai_systems_slice(org.id, db_session)
    by_id = {s["archimate_element_id"]: s for s in slice_data["systems"]}
    p = by_id[bare.id]
    assert p["governance"]["approval_gate"] == "unknown"
    assert p["governance"]["human_review"] == "unknown"
    assert p["autonomy_level"] == "unknown"
    assert p["data_sensitivity"] == "unknown"
    # unknown conditions never fabricate a risk flag
    assert p["risk_flags"] == []


def test_regulated_no_human_review_flag():
    # unit-level: regulated data + human_review False fires the flag
    profile = {
        "autonomy_level": "assisted",
        "data_sensitivity": "regulated",
        "model_currency": "current",
        "governance": {"approval_gate": True, "human_review": False},
    }
    assert "regulated-no-human-review" in risk_flags(profile)
    # but not when human_review is merely unrecorded
    profile["governance"]["human_review"] = "unknown"
    assert "regulated-no-human-review" not in risk_flags(profile)


def test_archie_copilot_is_modelled_honestly(db_session, org):
    el = seed_archie_copilot(db_session, org.id)
    p = profile_from_element(el)
    assert p["name"] == ARCHIE_COPILOT_NAME
    assert p["provider"] == "anthropic"
    assert p["autonomy_level"] == "human-in-loop"
    assert p["governance"]["approval_gate"] is True
    # honest: human_review is not asserted, so it reads unknown
    assert p["governance"]["human_review"] == "unknown"
    assert p["model_currency"] == "current"


def test_org_scoped(db_session, make_org):
    org_a = make_org("a")
    org_b = make_org("b")
    register_ai_system(db_session, org_a.id, name="A-only", provider="anthropic",
                       model_id="claude-opus-5", autonomy_level="assisted")
    db_session.flush()
    slice_a = build_ai_systems_slice(org_a.id, db_session)
    slice_b = build_ai_systems_slice(org_b.id, db_session)
    assert slice_a["counts"]["total"] == 1
    assert slice_b["counts"]["total"] == 0


def test_seed_is_idempotent_on_name(db_session, org):
    seed_archie_copilot(db_session, org.id)
    seed_archie_copilot(db_session, org.id)
    slice_data = build_ai_systems_slice(org.id, db_session)
    names = [s["name"] for s in slice_data["systems"]]
    assert names.count(ARCHIE_COPILOT_NAME) == 1


# --- Risk class, release state, owner, classification gate -------


def test_risk_tier_vocabulary_defined():
    """The four EU AI Act tiers are defined once."""
    assert RISK_TIERS == ("unacceptable", "high", "limited", "minimal")


def test_profile_shows_risk_class_and_release_state_as_not_set_initially(db_session, org):
    """A system with no classification or release state recorded shows 'not set'."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    p = profile_from_element(el)
    assert p["risk_class"] == "not set"
    assert p["release_state"] == "not set"


def test_classification_proposal_creates_approval_record(db_session, org):
    """Proposing a classification creates a pending approval in the queue."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    assert approval is not None
    assert approval.status.value == "pending"
    assert approval.entity_type == "ai_system_classification"
    assert approval.organization_id == org.id
    # The classification is NOT applied yet
    p = profile_from_element(el)
    assert p["risk_class"] == "not set"


def test_accepting_classification_sets_risk_class(db_session, org):
    """Accepting the proposal sets the risk class on the element."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()
    p = profile_from_element(el)
    assert p["risk_class"] == "high"


def test_rejecting_classification_leaves_unclassified(db_session, org):
    """Rejecting the proposal leaves the system unclassified."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    reject_classification(approval.id, org.id)
    db_session.flush()
    p = profile_from_element(el)
    assert p["risk_class"] == "not set"


def test_high_risk_without_assessment_cannot_be_released(db_session, org):
    """An accepted-high system with no assessment cannot be marked released."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    # Accept the high-risk classification — but no assessment is attached.
    approval = propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()
    result = mark_released(el, org.id)
    assert result["success"] is False
    assert profile_from_element(el)["release_state"] == "not set"


def test_refusal_names_missing_assessment(db_session, org):
    """The release refusal names the missing assessment."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()
    reason = release_refusal_reason(el, org.id)
    assert reason is not None
    assert "assessment" in reason.lower()
    result = mark_released(el, org.id)
    assert result["success"] is False
    assert "assessment" in result["reason"].lower()


def test_high_risk_with_assessment_can_be_released(db_session, org):
    """A high-risk system with an accepted assessment can be marked released."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()
    # Classification alone is not the assessment — release is still refused.
    assert can_mark_released(el, org.id) is False
    # Attach an assessment proposal and accept it.
    assessment = propose_assessment(
        element=el,
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    assert accept_assessment(assessment.id, org.id) is True
    db_session.flush()
    assert can_mark_released(el, org.id) is True
    result = mark_released(el, org.id)
    assert result["success"] is True
    p = profile_from_element(el)
    assert p["release_state"] == "released"


def test_low_risk_system_can_be_released_without_assessment(db_session, org):
    """A minimal-risk system can be released without an assessment."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="minimal",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()
    result = mark_released(el, org.id)
    assert result["success"] is True


def test_owner_read_through_application_owner(db_session, org, make_org):
    """The owner shown on the register is read through ApplicationOwner."""
    from app.models.application_owner import ApplicationOwner
    from app.models.user import User

    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    # Create a user in the same org
    user = User(email="owner@test.com", organization_id=org.id,
                first_name="Test", last_name="Owner")
    db_session.add(user)
    db_session.flush()

    # Set owner through ApplicationOwner
    owner = ApplicationOwner(
        element_type="ai_system",
        element_id=el.id,
        user_id=user.id,
        ownership_type="primary",
        organization_id=org.id,
    )
    db_session.add(owner)
    db_session.flush()

    # Read through profile
    p = profile_from_element(el)
    assert "owner" in p
    assert p["owner"]["user_id"] == user.id
    assert p["owner"]["ownership_type"] == "primary"


def test_owner_not_in_profile_custom_properties(db_session, org):
    """There is no owner key in the profile's custom_properties."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    props = el.custom_properties or {}
    ai = props.get("ai_system") or {}
    assert "owner" not in ai


def test_model_restriction_through_provider_register(db_session, org):
    """Models offered come from allowed provider rows; a blocked model is refused."""
    from app.models.model_provider import ModelProvider

    register_ai_system(db_session, org.id, name="Test System",
                       provider="openai", model_id="gpt-4o")
    db_session.flush()

    # Initially gpt-4o is allowed
    assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", org.id) is True

    # Block it for this org
    blocked = ModelProvider(
        provider="openai",
        model_version="gpt-4o",
        organization_id=org.id,
        is_platform_default=False,
        is_allowed=False,
    )
    db_session.add(blocked)
    db_session.flush()

    # Now it should be refused
    assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", org.id) is False


def test_two_org_classification_isolation(db_session, make_org):
    """A's classifications never appear for B."""
    from app.models.application_owner import ApplicationOwner
    from app.models.user import User

    org_a = make_org("a")
    org_b = make_org("b")

    el_a = register_ai_system(db_session, org_a.id, name="System A",
                              provider="anthropic", model_id="claude-opus-5")
    el_b = register_ai_system(db_session, org_b.id, name="System B",
                              provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    # Classify A's system
    approval = propose_classification(
        element=el_a,
        proposed_risk_class="high",
        organization_id=org_a.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org_a.id)
    db_session.flush()

    # A's system is classified
    p_a = profile_from_element(el_a)
    assert p_a["risk_class"] == "high"

    # B's system is NOT classified
    p_b = profile_from_element(el_b)
    assert p_b["risk_class"] == "not set"

    # B's slice does not include A's system
    slice_b = build_ai_systems_slice(org_b.id, db_session)
    names_b = [s["name"] for s in slice_b["systems"]]
    assert "System A" not in names_b


def test_two_org_provider_restriction_independent(db_session, make_org):
    """B's provider restriction does not change A's register."""
    from app.models.model_provider import ModelProvider, set_provider_restriction

    org_a = make_org("a")
    org_b = make_org("b")

    # Block gpt-4o for B only
    set_provider_restriction(org_b.id, "openai", "gpt-4o", allowed=False)
    db_session.flush()

    # A can still use gpt-4o
    assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", org_a.id) is True
    # B cannot
    assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", org_b.id) is False


def test_seed_is_idempotent_and_shows_archie_first(db_session, org):
    """The product's own AI features appear as the first entries; idempotent seed."""
    seed_archie_copilot(db_session, org.id)
    seed_archie_copilot(db_session, org.id)
    slice_data = build_ai_systems_slice(org.id, db_session)
    names = [s["name"] for s in slice_data["systems"]]
    assert names.count(ARCHIE_COPILOT_NAME) == 1
    # Archie Copilot is the first entry
    assert names[0] == ARCHIE_COPILOT_NAME


def test_get_classification_returns_none_when_not_set(db_session, org):
    """get_classification returns None for an unclassified system."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    assert get_classification(el) is None


def test_get_classification_returns_class_after_accept(db_session, org):
    """get_classification returns the risk class after acceptance."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="limited",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()
    assert get_classification(el) == "limited"


def test_can_mark_released_false_for_high_risk_without_assessment(db_session, org):
    """can_mark_released returns False for high-risk without accepted assessment."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    # Propose classification (pending — assessment not yet accepted)
    propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    assert can_mark_released(el, org.id) is False


def test_can_mark_released_true_for_unclassified_system(db_session, org):
    """can_mark_released returns True for an unclassified system (not high-risk)."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    # An unclassified system is not high-risk, so it can be released
    assert can_mark_released(el, org.id) is True


def test_can_mark_released_true_for_high_risk_with_assessment(db_session, org):
    """can_mark_released returns True for high-risk with accepted assessment."""
    el = register_ai_system(db_session, org.id, name="Test System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()
    # Classification alone is not the assessment — still blocked.
    assert can_mark_released(el, org.id) is False
    assessment = propose_assessment(
        element=el,
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_assessment(assessment.id, org.id)
    db_session.flush()
    assert can_mark_released(el, org.id) is True


# --- Release route, refusal message, org isolation (fix round) ----------


def _make_admin_user(db_session, org, label):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name=label,
        organization_id=org.id,
        confirmed=True,
        role=admin_role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def test_release_route_refuses_without_assessment_then_releases_with_it(
    app, db_session, make_org, client, login_as
):
    """POST /genome/ai-systems/<id>/release refuses an accepted-high system
    without an assessment and releases it once the assessment is accepted."""
    org = make_org("release-route")
    admin = _make_admin_user(db_session, org, "ReleaseRoute")
    el = register_ai_system(db_session, org.id, name="Route System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()
    approval = propose_classification(
        element=el,
        proposed_risk_class="high",
        organization_id=org.id,
        proposed_by_user_id=admin.id,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    login_as(client, admin)

    # Accepted-high, no assessment: the route refuses with the named reason
    # and leaves the system unreleased.
    refused = client.post(
        f"/genome/ai-systems/{el.id}/release",
        headers={"Accept": "application/json"},
    )
    assert refused.status_code in (400, 409)
    assert "assessment" in refused.get_data(as_text=True).lower()
    assert profile_from_element(el)["release_state"] == "not set"

    # The register page shows the refusal reason and an Attach-assessment action.
    page = client.get("/genome/ai-systems")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "assessment" in body.lower()
    assert "Attach assessment" in body
    assert f"/genome/ai-systems/{el.id}/assess" in body

    # After the assessment is accepted, the same POST releases the system.
    assessment = propose_assessment(
        element=el,
        organization_id=org.id,
        proposed_by_user_id=admin.id,
    )
    db_session.flush()
    assert accept_assessment(assessment.id, org.id) is True
    db_session.flush()

    released = client.post(f"/genome/ai-systems/{el.id}/release")
    assert released.status_code == 302
    assert profile_from_element(el)["release_state"] == "released"


def test_release_route_limited_to_administrator(app, db_session, make_org, client, login_as):
    """A non-administrator cannot release a system."""
    from app.models.user import Role, User

    org = make_org("release-role")
    architect_role = Role.query.filter_by(name="Architect").first()
    architect = User(
        email=f"arch-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Architect",
        organization_id=org.id,
        confirmed=True,
        role=architect_role,
    )
    architect.password = uuid.uuid4().hex
    db_session.add(architect)
    el = register_ai_system(db_session, org.id, name="Role System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    login_as(client, architect)
    resp = client.post(f"/genome/ai-systems/{el.id}/release")
    assert resp.status_code == 403
    assert profile_from_element(el)["release_state"] == "not set"


def test_release_gate_and_assessment_are_org_scoped(
    app, db_session, make_org, client, login_as
):
    """A's refusal reasons, releases and assessment proposals never reach B."""
    org_a = make_org("gate-a")
    org_b = make_org("gate-b")
    admin_a = _make_admin_user(db_session, org_a, "GateA")
    admin_b = _make_admin_user(db_session, org_b, "GateB")

    el_a = register_ai_system(db_session, org_a.id, name="System A",
                              provider="anthropic", model_id="claude-opus-5")
    el_b = register_ai_system(db_session, org_b.id, name="System B",
                              provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    # A: high-risk classification accepted, no assessment (blocked).
    approval_a = propose_classification(
        element=el_a, proposed_risk_class="high",
        organization_id=org_a.id, proposed_by_user_id=admin_a.id,
    )
    db_session.flush()
    accept_classification(approval_a.id, org_a.id)
    db_session.flush()

    # B: high-risk classification accepted AND assessment accepted (releasable).
    approval_b = propose_classification(
        element=el_b, proposed_risk_class="high",
        organization_id=org_b.id, proposed_by_user_id=admin_b.id,
    )
    db_session.flush()
    accept_classification(approval_b.id, org_b.id)
    db_session.flush()
    assessment_b = propose_assessment(
        element=el_b, organization_id=org_b.id, proposed_by_user_id=admin_b.id,
    )
    db_session.flush()
    accept_assessment(assessment_b.id, org_b.id)
    db_session.flush()

    # B's slice never names A's system and shows no reason for B's own system.
    slice_b = build_ai_systems_slice(org_b.id, db_session)
    by_name_b = {s["name"]: s for s in slice_b["systems"]}
    assert "System A" not in by_name_b
    assert by_name_b["System B"]["release_blocked_reason"] is None
    assert by_name_b["System B"]["assessment_accepted"] is True

    # A's slice is blocked with the named assessment reason.
    slice_a = build_ai_systems_slice(org_a.id, db_session)
    by_name_a = {s["name"]: s for s in slice_a["systems"]}
    assert by_name_a["System A"]["release_blocked_reason"] is not None
    assert "assessment" in by_name_a["System A"]["release_blocked_reason"].lower()
    assert by_name_a["System A"]["assessment_accepted"] is False

    # B's administrator cannot release A's system (not in B's organisation).
    login_as(client, admin_b)
    cross = client.post(f"/genome/ai-systems/{el_a.id}/release")
    assert cross.status_code == 404
    assert profile_from_element(el_a)["release_state"] == "not set"

    # B's administrator cannot attach an assessment proposal to A's system.
    cross_assess = client.post(f"/genome/ai-systems/{el_a.id}/assess")
    assert cross_assess.status_code == 404
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    assert (
        AIChatCRUDApproval.query.filter_by(
            entity_type="ai_system_assessment",
            entity_id=el_a.id,
            organization_id=org_a.id,
        ).count()
        == 0
    )

    # B's own system releases through the route.
    ok = client.post(f"/genome/ai-systems/{el_b.id}/release")
    assert ok.status_code == 302
    assert profile_from_element(el_b)["release_state"] == "released"


# --- Release gate: ordering, stale assessments, authorisation, browser form ---


def test_release_before_classification_resets_release_state(db_session, org):
    """Releasing an unclassified system then accepting a high-risk
    classification must reset the release state to draft."""
    el = register_ai_system(db_session, org.id, name="Released-then-classified System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    # Release while unclassified (gate does not apply).
    result = mark_released(el, org.id)
    assert result["success"] is True
    assert profile_from_element(el)["release_state"] == "released"

    # Now accept a high-risk classification.
    approval = propose_classification(
        element=el, proposed_risk_class="high",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    # The release state must be reset to draft because the system is now
    # high-risk with no accepted assessment.
    p = profile_from_element(el)
    assert p["risk_class"] == "high"
    assert p["release_state"] == "draft", (
        "Expected draft after accepting high on a released system with no assessment, "
        "got %s" % p["release_state"]
    )
    assert p["assessment_accepted"] is False


def test_stale_assessment_does_not_open_gate(db_session, org):
    """An assessment accepted while the system was unclassified must
    not open the high-risk gate after the classification changes."""
    el = register_ai_system(db_session, org.id, name="Stale-assessment System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    # Propose and accept an assessment while the system is unclassified.
    # This should fail because propose_assessment now refuses when there is
    # no accepted class.
    with pytest.raises(ValueError, match="no accepted risk class"):
        propose_assessment(
            element=el,
            organization_id=org.id,
            proposed_by_user_id=None,
        )

    # Classify as high first.
    approval = propose_classification(
        element=el, proposed_risk_class="high",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    # Now propose and accept an assessment against high.
    assessment = propose_assessment(
        element=el,
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_assessment(assessment.id, org.id)
    db_session.flush()

    # Verify the assessment payload records the correct class.
    import json
    payload = json.loads(assessment.operation_payload)
    assert payload["assessed_risk_class"] == "high"

    # Release should now work.
    assert can_mark_released(el, org.id) is True
    result = mark_released(el, org.id)
    assert result["success"] is True


def test_assessment_under_lower_tier_does_not_open_gate(db_session, org):
    """An assessment accepted under a lower tier must not open the
    high-risk gate after reclassification to high."""
    el = register_ai_system(db_session, org.id, name="Lower-tier-assessment System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    # Classify as minimal.
    approval = propose_classification(
        element=el, proposed_risk_class="minimal",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    # Accept an assessment against minimal.
    assessment = propose_assessment(
        element=el,
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()
    accept_assessment(assessment.id, org.id)
    db_session.flush()

    # Verify the assessment payload records minimal.
    import json
    payload = json.loads(assessment.operation_payload)
    assert payload["assessed_risk_class"] == "minimal"

    # Reclassify as high through the approval queue.
    reclassify = propose_classification(
        element=el, proposed_risk_class="high",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(reclassify.id, org.id)
    db_session.flush()

    # The old minimal assessment must not open the high-risk gate.
    assert can_mark_released(el, org.id) is False
    reason = release_refusal_reason(el, org.id)
    assert reason is not None
    assert "assessment" in reason.lower()


def test_viewer_cannot_assess(app, db_session, make_org, client, login_as):
    """A Viewer must get 403 on the assess route."""
    from app.models.user import Role, User

    org = make_org("med1")
    viewer_role = Role.query.filter_by(name="Viewer").first()
    if viewer_role is None:
        pytest.skip("no Viewer role seeded")
    viewer = User(
        email=f"viewer-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Viewer",
        organization_id=org.id,
        confirmed=True,
        role=viewer_role,
    )
    viewer.password = uuid.uuid4().hex
    db_session.add(viewer)
    el = register_ai_system(db_session, org.id, name="Viewer-assess System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    login_as(client, viewer)
    r = client.post(f"/genome/ai-systems/{el.id}/assess")
    assert r.status_code == 403, "Viewer should get 403 on assess route, got %s" % r.status_code

    # No approval row should have been created.
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    count = AIChatCRUDApproval.query.filter_by(
        entity_type="ai_system_assessment",
        entity_id=el.id,
        organization_id=org.id,
    ).count()
    assert count == 0, "Viewer should not create assessment proposals"


def test_propose_classification_checks_org(db_session, make_org):
    """propose_classification must refuse a foreign element."""
    org_a = make_org("low1-a")
    org_b = make_org("low1-b")
    el_b = register_ai_system(db_session, org_b.id, name="B's System",
                              provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    with pytest.raises(ValueError, match="scoped to the element's own organisation"):
        propose_classification(
            element=el_b,
            proposed_risk_class="high",
            organization_id=org_a.id,
            proposed_by_user_id=None,
        )


def test_release_refusal_flashes_for_non_json(app, db_session, make_org, client, login_as):
    """A release refusal for a non-JSON request must flash and redirect,
    not return raw JSON."""
    org = make_org("refusal")
    admin = _make_admin_user(db_session, org, "Low3")
    el = register_ai_system(db_session, org.id, name="Browser-refusal System",
                            provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    # Accept high-risk classification (no assessment -> release refused).
    approval = propose_classification(
        element=el, proposed_risk_class="high",
        organization_id=org.id, proposed_by_user_id=admin.id,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    login_as(client, admin)

    # A browser form submit must redirect, not return JSON.
    resp = client.post(
        f"/genome/ai-systems/{el.id}/release",
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
    )
    assert resp.status_code == 302, (
        "Expected a redirect for a browser release refusal, got %s" % resp.status_code
    )
    # The system must not be released.
    assert profile_from_element(el)["release_state"] != "released"
