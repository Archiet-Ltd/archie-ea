"""PILLAR 6 — the AI Systems genome projection.

Uses the shared fixtures in tests/conftest.py (db_session rolls back).
"""

from __future__ import annotations

import json

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


# --- R1-B23 PR 2: risk class, release state, owner, classification gate -------


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
    """A high-risk system without an accepted assessment cannot be marked released."""
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
    result = mark_released(el, org.id)
    assert result is False


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
    result = mark_released(el, org.id)
    assert result is True
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
    assert result is True


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
    assert can_mark_released(el, org.id) is True
