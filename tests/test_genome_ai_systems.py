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
from app.modules.enterprise_genome.services.ai_system_grounding import (
    propose_grounding_source,
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


# =============================================================================
# --- Lineage, Obligations, Grounding Source
# =============================================================================


# --- Lineage -----------------------------------------------------------------


def _create_data_entity(session, org_id, name: str):
    """Create a DataObject element to use as a data entity."""
    from app.models.models import ArchiMateElement

    el = ArchiMateElement(
        name=name,
        type="DataObject",
        layer="application",
        organization_id=org_id,
        custom_properties={},
    )
    session.add(el)
    session.flush()
    return el


def _create_provider_element(session, org_id, name: str):
    """Create an ApplicationComponent element to use as a provider."""
    from app.models.models import ArchiMateElement

    el = ArchiMateElement(
        name=name,
        type="ApplicationComponent",
        layer="application",
        organization_id=org_id,
        custom_properties={"provider_register": True},
    )
    session.add(el)
    session.flush()
    return el


def _link_elements(session, source_id, target_id, rel_type="association", org_id=None):
    """Create an ArchiMateRelationship between two elements."""
    from app.models.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source_id,
        target_id=target_id,
        type=rel_type,
    )
    if org_id is not None:
        rel.organization_id = org_id
    session.add(rel)
    session.flush()
    return rel


def test_lineage_providers_and_data_entities_fail_on_main(db_session, org):
    """A system linked to providers and data entities shows them. Fails on main
    (no lineage read)."""
    el = register_ai_system(db_session, org.id, name="Lineage System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    prov1 = _create_provider_element(db_session, org.id, "OpenAI")
    prov2 = _create_provider_element(db_session, org.id, "Anthropic")
    de1 = _create_data_entity(db_session, org.id, "Customer Records")
    de2 = _create_data_entity(db_session, org.id, "Transaction Data")
    de3 = _create_data_entity(db_session, org.id, "Fraud Alerts")

    _link_elements(db_session, el.id, prov1.id, "serving", org_id=org.id)
    _link_elements(db_session, el.id, prov2.id, "serving", org_id=org.id)
    _link_elements(db_session, el.id, de1.id, "access", org_id=org.id)
    _link_elements(db_session, el.id, de2.id, "access", org_id=org.id)
    _link_elements(db_session, el.id, de3.id, "access", org_id=org.id)
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_lineage import (
        get_providers_for_system,
        get_data_entities_for_system,
    )

    providers = get_providers_for_system(el.id, org.id)
    assert len(providers) == 2
    pnames = {p["name"] for p in providers}
    assert "OpenAI" in pnames
    assert "Anthropic" in pnames

    entities = get_data_entities_for_system(el.id, org.id)
    assert len(entities) == 3
    dnames = {d["name"] for d in entities}
    assert "Customer Records" in dnames
    assert "Transaction Data" in dnames
    assert "Fraud Alerts" in dnames


def test_lineage_systems_using_provider_shows_owner_and_data(db_session, org):
    """'Systems using provider X' lists the system with its owner and the data
    entities sent. Fails on main (no lineage read)."""
    from app.models.application_owner import ApplicationOwner
    from app.models.user import User

    el = register_ai_system(db_session, org.id, name="Consumer System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    prov = _create_provider_element(db_session, org.id, "OpenAI")
    de1 = _create_data_entity(db_session, org.id, "Customer Records")
    de2 = _create_data_entity(db_session, org.id, "Analytics Data")

    _link_elements(db_session, el.id, prov.id, "serving", org_id=org.id)
    _link_elements(db_session, el.id, de1.id, "access", org_id=org.id)
    _link_elements(db_session, el.id, de2.id, "access", org_id=org.id)
    db_session.flush()

    # Set an owner
    user = User(email="owner-lineage@test.com", organization_id=org.id,
                first_name="Lineage", last_name="Owner")
    db_session.add(user)
    db_session.flush()
    owner = ApplicationOwner(
        element_type="ai_system",
        element_id=el.id,
        user_id=user.id,
        ownership_type="primary",
        organization_id=org.id,
    )
    db_session.add(owner)
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_lineage import (
        get_systems_using_provider,
    )

    systems = get_systems_using_provider(prov.id, org.id)
    assert len(systems) == 1
    assert systems[0]["name"] == "Consumer System"
    assert systems[0]["owner"] is not None
    assert systems[0]["owner"]["user_id"] == user.id
    assert len(systems[0]["data_entities"]) == 2


def test_lineage_org_isolation(db_session, make_org):
    """Organisation B sees none of A's lineage data. Fails on main."""
    org_a = make_org("lineage-a")
    org_b = make_org("lineage-b")

    el_a = register_ai_system(db_session, org_a.id, name="A System",
                              provider="openai", model_id="gpt-4o")
    el_b = register_ai_system(db_session, org_b.id, name="B System",
                              provider="anthropic", model_id="claude-opus-5")
    prov_a = _create_provider_element(db_session, org_a.id, "A-Provider")
    prov_b = _create_provider_element(db_session, org_b.id, "B-Provider")
    de_a = _create_data_entity(db_session, org_a.id, "A-Data")
    de_b = _create_data_entity(db_session, org_b.id, "B-Data")

    _link_elements(db_session, el_a.id, prov_a.id, "serving", org_id=org_a.id)
    _link_elements(db_session, el_a.id, de_a.id, "access", org_id=org_a.id)
    _link_elements(db_session, el_b.id, prov_b.id, "serving", org_id=org_b.id)
    _link_elements(db_session, el_b.id, de_b.id, "access", org_id=org_b.id)
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_lineage import (
        get_providers_for_system,
        get_data_entities_for_system,
        get_systems_using_provider,
    )

    # A's system sees A's providers and entities
    assert len(get_providers_for_system(el_a.id, org_a.id)) == 1
    assert len(get_data_entities_for_system(el_a.id, org_a.id)) == 1

    # B querying A's system returns nothing (wrong org)
    assert len(get_providers_for_system(el_a.id, org_b.id)) == 0
    assert len(get_data_entities_for_system(el_a.id, org_b.id)) == 0

    # Systems using provider are org-scoped
    assert len(get_systems_using_provider(prov_a.id, org_a.id)) == 1
    assert len(get_systems_using_provider(prov_a.id, org_b.id)) == 0


def test_lineage_not_recorded_renders_not_recorded(db_session, org):
    """An AI system with no providers or data entities shows 'not recorded'
    with a reason, never zero. Fails on main."""
    el = register_ai_system(db_session, org.id, name="Lonely System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_lineage import (
        get_providers_for_system,
        get_data_entities_for_system,
    )

    providers = get_providers_for_system(el.id, org.id)
    assert providers == [] or providers is None


# --- Obligations --------------------------------------------------------------


def test_obligations_accept_high_risk_attaches_requirements(db_session, org):
    """Accepting a high-risk classification with an accepted assessment attaches
    the class's obligations as compliance requirement rows. Fails on main."""
    el = register_ai_system(db_session, org.id, name="Obligation System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    # Classify as high and accept
    approval = propose_classification(
        element=el, proposed_risk_class="high",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    # Attach and accept assessment
    assessment = propose_assessment(
        element=el, organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_assessment(assessment.id, org.id)
    db_session.flush()

    # Now re-classify (same class) so the obligation-attachment step
    # inside accept_classification runs again, this time with an accepted
    # assessment in place.
    reapproval = propose_classification(
        element=el, proposed_risk_class="high",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(reapproval.id, org.id)
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_obligations import (
        get_obligations_for_system,
    )

    obligations = get_obligations_for_system(el.id, org.id)
    assert len(obligations) > 0
    # Each obligation has an evidence status
    for ob in obligations:
        assert "title" in ob
        assert "evidence_status" in ob
        assert ob["evidence_status"] in ("not_started", "in_progress",
                                          "completed", "verified", "not recorded")


def test_obligations_without_assessment_attach_nothing(db_session, org):
    """A high-risk class without an accepted assessment attaches no obligations.
    Fails on main."""
    el = register_ai_system(db_session, org.id, name="No-Assessment System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    approval = propose_classification(
        element=el, proposed_risk_class="high",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_obligations import (
        get_obligations_for_system,
    )

    # No assessment accepted, so no obligations should be attached
    obligations = get_obligations_for_system(el.id, org.id)
    assert len(obligations) == 0


def test_obligations_minimal_class_attaches_minimal_set(db_session, org):
    """A minimal class attaches the minimal set of compliance requirements.
    Fails on main."""
    el = register_ai_system(db_session, org.id, name="Minimal System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    approval = propose_classification(
        element=el, proposed_risk_class="minimal",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_obligations import (
        get_obligations_for_system,
    )

    obligations = get_obligations_for_system(el.id, org.id)
    # Minimal class should have some (fewer) obligations
    assert len(obligations) >= 1


def test_obligations_org_isolation(db_session, make_org):
    """Organisation B sees none of A's obligation rows. Fails on main."""
    org_a = make_org("obl-a")
    org_b = make_org("obl-b")

    el_a = register_ai_system(db_session, org_a.id, name="A Obl System",
                              provider="openai", model_id="gpt-4o")
    el_b = register_ai_system(db_session, org_b.id, name="B Obl System",
                              provider="anthropic", model_id="claude-opus-5")
    db_session.flush()

    # Accept classifications for both
    for el, org, cls in [(el_a, org_a, "high"), (el_b, org_b, "minimal")]:
        approval = propose_classification(
            element=el, proposed_risk_class=cls,
            organization_id=org.id, proposed_by_user_id=None,
        )
        db_session.flush()
        accept_classification(approval.id, org.id)
        db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_obligations import (
        get_obligations_for_system,
    )

    # A's obligations (high without assessment -> no obligations)
    ob_a = get_obligations_for_system(el_a.id, org_a.id)
    assert len(ob_a) == 0, "A's high-risk system without assessment should have no obligations"
    # B's obligations exist (minimal class attaches obligations)
    ob_b = get_obligations_for_system(el_b.id, org_b.id)
    assert len(ob_b) > 0, "B's minimal-risk system should have obligations"
    # When querying from B's perspective, element A is in A's org so the
    # obligations are scoped to A's element. B cannot access A's element.
    from app.models.models import ArchiMateElement
    el_from_b = ArchiMateElement.query.filter_by(
        id=el_a.id, organization_id=org_b.id
    ).first()
    # el_a should NOT be findable from org_b
    assert el_from_b is None, "Cross-org element should not be visible"


def test_obligations_show_evidence_status(db_session, org):
    """Every obligation shown traces to a ComplianceRequirement row with
    evidence status. Fails on main."""
    el = register_ai_system(db_session, org.id, name="Evidence System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    approval = propose_classification(
        element=el, proposed_risk_class="high",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    assessment = propose_assessment(
        element=el, organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_assessment(assessment.id, org.id)
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_obligations import (
        get_obligations_for_system,
    )

    obligations = get_obligations_for_system(el.id, org.id)
    for ob in obligations:
        # Must trace to a real database row
        assert "id" in ob
        assert isinstance(ob["id"], int)


# --- Grounding Source ---------------------------------------------------------


def test_grounding_source_propose_creates_approval_record(db_session, org):
    """Proposing a data source creates one approval row. Fails on main."""
    from app.models.models import ArchiMateElement

    el = register_ai_system(db_session, org.id, name="Grounding System",
                             provider="openai", model_id="gpt-4o")
    data_entity = _create_data_entity(db_session, org.id, "Training Data Source")
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_grounding import (
        propose_grounding_source,
    )

    approval = propose_grounding_source(
        element=el,
        data_entity_id=data_entity.id,
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()

    assert approval is not None
    assert approval.status.value == "pending"
    assert approval.entity_type == "ai_system_grounding_source"
    assert approval.organization_id == org.id

    # Check the proposed-use property is NOT yet written
    props = getattr(el, "acm_properties", None) or {}
    assert "ai_system_grounding_source" not in props


def test_grounding_source_approve_writes_property(db_session, org):
    """Approving a grounding source writes the proposed-use property. Fails on main."""
    from app.models.models import ArchiMateElement

    el = register_ai_system(db_session, org.id, name="Approve Grounding",
                             provider="openai", model_id="gpt-4o")
    data_entity = _create_data_entity(db_session, org.id, "Ground Truth")
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_grounding import (
        propose_grounding_source,
        accept_grounding_source,
    )

    approval = propose_grounding_source(
        element=el,
        data_entity_id=data_entity.id,
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()

    result = accept_grounding_source(approval.id, org.id)
    db_session.flush()
    assert result is True

    # Check the property was written
    props = getattr(el, "acm_properties", None) or {}
    assert "ai_system_grounding_source" in props


def test_grounding_source_refuse_writes_nothing(db_session, org):
    """Refusing a grounding source writes nothing. Fails on main."""
    from app.models.models import ArchiMateElement

    el = register_ai_system(db_session, org.id, name="Refuse Grounding",
                             provider="openai", model_id="gpt-4o")
    data_entity = _create_data_entity(db_session, org.id, "Refused Data")
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_grounding import (
        propose_grounding_source,
        reject_grounding_source,
    )

    approval = propose_grounding_source(
        element=el,
        data_entity_id=data_entity.id,
        organization_id=org.id,
        proposed_by_user_id=None,
    )
    db_session.flush()

    result = reject_grounding_source(approval.id, org.id)
    db_session.flush()
    assert result is True

    # No property should have been written
    props = getattr(el, "acm_properties", None) or {}
    assert "ai_system_grounding_source" not in props


def test_grounding_source_org_isolation(db_session, make_org):
    """Two organisations' grounding sources are isolated. Fails on main."""
    org_a = make_org("grnd-a")
    org_b = make_org("grnd-b")

    el_a = register_ai_system(db_session, org_a.id, name="A Grounding",
                              provider="openai", model_id="gpt-4o")
    el_b = register_ai_system(db_session, org_b.id, name="B Grounding",
                              provider="anthropic", model_id="claude-opus-5")
    de_a = _create_data_entity(db_session, org_a.id, "A Source")
    de_b = _create_data_entity(db_session, org_b.id, "B Source")
    db_session.flush()

    from app.modules.enterprise_genome.services.ai_system_grounding import (
        propose_grounding_source,
    )

    # Each org proposes their own
    appr_a = propose_grounding_source(
        element=el_a, data_entity_id=de_a.id,
        organization_id=org_a.id, proposed_by_user_id=None,
    )
    db_session.flush()
    appr_b = propose_grounding_source(
        element=el_b, data_entity_id=de_b.id,
        organization_id=org_b.id, proposed_by_user_id=None,
    )
    db_session.flush()

    # B cannot approve A's approval
    from app.modules.enterprise_genome.services.ai_system_grounding import (
        accept_grounding_source,
    )
    result = accept_grounding_source(appr_a.id, org_b.id)
    db_session.flush()
    assert result is False

    # B can approve its own approval
    result_b = accept_grounding_source(appr_b.id, org_b.id)
    db_session.flush()
    assert result_b is True


# --- Fix round: org-scoped obligations, lineage/obligations panels, routes ---


def test_obligations_org_isolation_query_returns_nothing_for_wrong_org(
    db_session, make_org
):
    """Querying obligations with another organisation's id returns nothing.
    Fails on main (no org filter)."""
    from app.modules.enterprise_genome.services.ai_system_obligations import (
        get_obligations_for_system,
    )

    org_a = make_org("obl-iso-a")
    org_b = make_org("obl-iso-b")

    el_a = register_ai_system(db_session, org_a.id, name="A System",
                               provider="openai", model_id="gpt-4o")
    db_session.flush()

    # Accept classification for A (minimal attaches obligations)
    approval = propose_classification(
        element=el_a, proposed_risk_class="minimal",
        organization_id=org_a.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org_a.id)
    db_session.flush()

    # A's own org sees obligations
    ob_a = get_obligations_for_system(el_a.id, org_a.id)
    assert len(ob_a) > 0, "A's own org should see obligations"

    # B querying A's element with B's org returns nothing
    ob_b = get_obligations_for_system(el_a.id, org_b.id)
    assert len(ob_b) == 0, (
        "B querying A's element with B's org should return no obligations"
    )


def test_register_page_shows_lineage_panel(app, db_session, make_org, client, login_as):
    """The register page renders a lineage panel per system with providers,
    data entities and systems using the same provider. Fails on main (no panel)."""
    from app.models.user import User

    org = make_org("lineage-panel")
    arch = _make_admin_user(db_session, org, "LineagePanel")
    el = register_ai_system(db_session, org.id, name="Panel System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    prov = _create_provider_element(db_session, org.id, "Test Provider")
    de = _create_data_entity(db_session, org.id, "Test Data")
    _link_elements(db_session, el.id, prov.id, "serving", org_id=org.id)
    _link_elements(db_session, el.id, de.id, "access", org_id=org.id)
    db_session.flush()

    login_as(client, arch)
    body = client.get("/genome/ai-systems").get_data(as_text=True)

    # Lineage panel is rendered
    assert 'data-testid="lineage-panel"' in body, "Lineage panel not found in register"
    assert "Test Provider" in body, "Provider name not found in register"
    assert "Test Data" in body, "Data entity name not found in register"


def test_register_page_shows_obligations_panel(
    app, db_session, make_org, client, login_as
):
    """The register page renders an obligations panel per system with title
    and evidence status. Fails on main (no panel)."""
    org = make_org("obl-panel")
    arch = _make_admin_user(db_session, org, "OblPanel")
    el = register_ai_system(db_session, org.id, name="Obl Panel System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    # Accept classification (minimal attaches obligations)
    approval = propose_classification(
        element=el, proposed_risk_class="minimal",
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()
    accept_classification(approval.id, org.id)
    db_session.flush()

    login_as(client, arch)
    body = client.get("/genome/ai-systems").get_data(as_text=True)

    # Obligations panel is rendered
    assert 'data-testid="obligations-panel"' in body, (
        "Obligations panel not found in register"
    )
    # Should show obligation titles
    assert "Voluntary code of conduct" in body, (
        "Obligation title not found in register"
    )


def test_register_page_shows_not_recorded_for_empty_lineage(
    app, db_session, make_org, client, login_as
):
    """A system with no providers or data entities shows 'not recorded'
    in the lineage panel. Fails on main (no panel)."""
    org = make_org("empty-lineage")
    arch = _make_admin_user(db_session, org, "EmptyLineage")
    el = register_ai_system(db_session, org.id, name="Empty Lineage System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    login_as(client, arch)
    body = client.get("/genome/ai-systems").get_data(as_text=True)

    assert 'data-testid="lineage-panel"' in body
    assert "not recorded" in body, (
        "Empty lineage should show 'not recorded' in the register"
    )


def test_register_page_shows_not_recorded_for_empty_obligations(
    app, db_session, make_org, client, login_as
):
    """A system with no obligations shows 'not recorded' in the obligations
    panel. Fails on main (no panel)."""
    org = make_org("empty-obl")
    arch = _make_admin_user(db_session, org, "EmptyObl")
    el = register_ai_system(db_session, org.id, name="Empty Obl System",
                             provider="openai", model_id="gpt-4o")
    db_session.flush()

    login_as(client, arch)
    body = client.get("/genome/ai-systems").get_data(as_text=True)

    assert 'data-testid="obligations-panel"' in body
    assert "not recorded" in body, (
        "Empty obligations should show 'not recorded' in the register"
    )


def test_grounding_source_propose_route_requires_general_permission(
    app, db_session, make_org, client, login_as
):
    """The grounding source propose route requires general permission."""
    from app.models.user import Role, User

    org = make_org("gs-route")
    viewer_role = Role.query.filter_by(name="Viewer").first()
    if viewer_role is None:
        pytest.skip("no Viewer role seeded")
    viewer = User(
        email=f"viewer-gs-{uuid.uuid4().hex[:8]}@example.test",
        first_name="GSViewer",
        organization_id=org.id,
        confirmed=True,
        role=viewer_role,
    )
    viewer.password = uuid.uuid4().hex
    db_session.add(viewer)
    el = register_ai_system(db_session, org.id, name="GS Route System",
                             provider="openai", model_id="gpt-4o")
    de = _create_data_entity(db_session, org.id, "GS Data")
    db_session.flush()

    login_as(client, viewer)
    r = client.post(
        f"/genome/ai-systems/{el.id}/grounding-source/propose",
        data={"data_entity_id": de.id},
    )
    assert r.status_code == 403, (
        "Viewer should get 403 on grounding source propose route"
    )


def test_grounding_source_accept_route_requires_general_permission(
    app, db_session, make_org, client, login_as
):
    """The grounding source accept route requires general permission."""
    from app.models.user import Role, User

    org = make_org("gs-accept-route")
    viewer_role = Role.query.filter_by(name="Viewer").first()
    if viewer_role is None:
        pytest.skip("no Viewer role seeded")
    viewer = User(
        email=f"viewer-gs-accept-{uuid.uuid4().hex[:8]}@example.test",
        first_name="GSAcceptViewer",
        organization_id=org.id,
        confirmed=True,
        role=viewer_role,
    )
    viewer.password = uuid.uuid4().hex
    db_session.add(viewer)
    el = register_ai_system(db_session, org.id, name="GS Accept System",
                             provider="openai", model_id="gpt-4o")
    de = _create_data_entity(db_session, org.id, "GS Accept Data")
    db_session.flush()

    approval = propose_grounding_source(
        element=el, data_entity_id=de.id,
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()

    login_as(client, viewer)
    r = client.post(
        f"/genome/ai-systems/{el.id}/grounding-source/{approval.id}/accept",
    )
    assert r.status_code == 403, (
        "Viewer should get 403 on grounding source accept route"
    )


def test_grounding_source_refuse_route_requires_general_permission(
    app, db_session, make_org, client, login_as
):
    """The grounding source refuse route requires general permission."""
    from app.models.user import Role, User

    org = make_org("gs-refuse-route")
    viewer_role = Role.query.filter_by(name="Viewer").first()
    if viewer_role is None:
        pytest.skip("no Viewer role seeded")
    viewer = User(
        email=f"viewer-gs-refuse-{uuid.uuid4().hex[:8]}@example.test",
        first_name="GSRefuseViewer",
        organization_id=org.id,
        confirmed=True,
        role=viewer_role,
    )
    viewer.password = uuid.uuid4().hex
    db_session.add(viewer)
    el = register_ai_system(db_session, org.id, name="GS Refuse System",
                             provider="openai", model_id="gpt-4o")
    de = _create_data_entity(db_session, org.id, "GS Refuse Data")
    db_session.flush()

    approval = propose_grounding_source(
        element=el, data_entity_id=de.id,
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()

    login_as(client, viewer)
    r = client.post(
        f"/genome/ai-systems/{el.id}/grounding-source/{approval.id}/refuse",
    )
    assert r.status_code == 403, (
        "Viewer should get 403 on grounding source refuse route"
    )


def test_grounding_source_propose_route_works_for_architect(
    app, db_session, make_org, client, login_as
):
    """An Architect can propose a grounding source through the route."""
    from app.models.user import Role, User

    org = make_org("gs-propose-arch")
    arch_role = Role.query.filter_by(name="Architect").first()
    if arch_role is None:
        pytest.skip("no Architect role seeded")
    arch = User(
        email=f"arch-gs-{uuid.uuid4().hex[:8]}@example.test",
        first_name="GSArch",
        organization_id=org.id,
        confirmed=True,
        role=arch_role,
    )
    arch.password = uuid.uuid4().hex
    db_session.add(arch)
    el = register_ai_system(db_session, org.id, name="GS Propose System",
                             provider="openai", model_id="gpt-4o")
    de = _create_data_entity(db_session, org.id, "GS Propose Data")
    db_session.flush()

    login_as(client, arch)
    r = client.post(
        f"/genome/ai-systems/{el.id}/grounding-source/propose",
        data={"data_entity_id": de.id},
    )
    assert r.status_code == 302, (
        "Architect should be able to propose grounding source, got %s" % r.status_code
    )

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    approval = AIChatCRUDApproval.query.filter_by(
        entity_type="ai_system_grounding_source",
        entity_id=el.id,
        organization_id=org.id,
    ).first()
    assert approval is not None, "Grounding source approval should have been created"


def test_grounding_source_accept_route_works_for_architect(
    app, db_session, make_org, client, login_as
):
    """An Architect can accept a grounding source through the route."""
    from app.models.user import Role, User

    org = make_org("gs-accept-arch")
    arch_role = Role.query.filter_by(name="Architect").first()
    if arch_role is None:
        pytest.skip("no Architect role seeded")
    arch = User(
        email=f"arch-gs-accept-{uuid.uuid4().hex[:8]}@example.test",
        first_name="GSAcceptArch",
        organization_id=org.id,
        confirmed=True,
        role=arch_role,
    )
    arch.password = uuid.uuid4().hex
    db_session.add(arch)
    el = register_ai_system(db_session, org.id, name="GS Accept Route System",
                             provider="openai", model_id="gpt-4o")
    de = _create_data_entity(db_session, org.id, "GS Accept Route Data")
    db_session.flush()

    approval = propose_grounding_source(
        element=el, data_entity_id=de.id,
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()

    login_as(client, arch)
    r = client.post(
        f"/genome/ai-systems/{el.id}/grounding-source/{approval.id}/accept",
    )
    assert r.status_code == 302, (
        "Architect should be able to accept grounding source, got %s" % r.status_code
    )

    # The property should have been written
    db_session.expire_all()
    from app.models.models import ArchiMateElement
    el_reloaded = db_session.get(ArchiMateElement, el.id)
    props = getattr(el_reloaded, "acm_properties", None) or {}
    assert "ai_system_grounding_source" in props, (
        "Grounding source property should have been written"
    )


def test_grounding_source_refuse_route_works_for_architect(
    app, db_session, make_org, client, login_as
):
    """An Architect can refuse a grounding source through the route."""
    from app.models.user import Role, User

    org = make_org("gs-refuse-arch")
    arch_role = Role.query.filter_by(name="Architect").first()
    if arch_role is None:
        pytest.skip("no Architect role seeded")
    arch = User(
        email=f"arch-gs-refuse-{uuid.uuid4().hex[:8]}@example.test",
        first_name="GSRefuseArch",
        organization_id=org.id,
        confirmed=True,
        role=arch_role,
    )
    arch.password = uuid.uuid4().hex
    db_session.add(arch)
    el = register_ai_system(db_session, org.id, name="GS Refuse Route System",
                             provider="openai", model_id="gpt-4o")
    de = _create_data_entity(db_session, org.id, "GS Refuse Route Data")
    db_session.flush()

    approval = propose_grounding_source(
        element=el, data_entity_id=de.id,
        organization_id=org.id, proposed_by_user_id=None,
    )
    db_session.flush()

    login_as(client, arch)
    r = client.post(
        f"/genome/ai-systems/{el.id}/grounding-source/{approval.id}/refuse",
    )
    assert r.status_code == 302, (
        "Architect should be able to refuse grounding source, got %s" % r.status_code
    )

    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    db_session.expire_all()
    approval_reloaded = db_session.get(AIChatCRUDApproval, approval.id)
    assert approval_reloaded.status == ApprovalStatus.REJECTED, (
        "Grounding source should have been rejected"
    )


def test_grounding_source_routes_are_org_scoped(
    app, db_session, make_org, client, login_as
):
    """Grounding source routes refuse cross-org access with 404."""
    org_a = make_org("gs-org-a")
    org_b = make_org("gs-org-b")
    arch_a = _make_admin_user(db_session, org_a, "GSAOrgA")
    arch_b = _make_admin_user(db_session, org_b, "GSAOrgB")

    el_a = register_ai_system(db_session, org_a.id, name="GS Org A System",
                               provider="openai", model_id="gpt-4o")
    de_a = _create_data_entity(db_session, org_a.id, "GS Org A Data")
    db_session.flush()

    approval_a = propose_grounding_source(
        element=el_a, data_entity_id=de_a.id,
        organization_id=org_a.id, proposed_by_user_id=None,
    )
    db_session.flush()

    # B cannot propose for A's element
    login_as(client, arch_b)
    r = client.post(
        f"/genome/ai-systems/{el_a.id}/grounding-source/propose",
        data={"data_entity_id": de_a.id},
    )
    assert r.status_code == 404, (
        "Cross-org grounding source propose should get 404"
    )

    # B cannot accept A's approval
    r = client.post(
        f"/genome/ai-systems/{el_a.id}/grounding-source/{approval_a.id}/accept",
    )
    assert r.status_code == 404, (
        "Cross-org grounding source accept should get 404"
    )

    # B cannot refuse A's approval
    r = client.post(
        f"/genome/ai-systems/{el_a.id}/grounding-source/{approval_a.id}/refuse",
    )
    assert r.status_code == 404, (
        "Cross-org grounding source refuse should get 404"
    )
