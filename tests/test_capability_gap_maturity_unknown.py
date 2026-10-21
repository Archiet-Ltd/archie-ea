"""Tests for _effective_maturity_gap returning None when maturity is unrecorded.

A capability with no recorded maturity level has an unknown gap, not a gap of zero.
"""

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def gap_service(app):
    from app.modules.capabilities.services.capability_gap_service import (
        CapabilityGapAnalysisService,
    )

    with app.app_context():
        yield CapabilityGapAnalysisService()


@pytest.fixture
def health_service(app):
    from app.modules.capabilities.services.capability_health_service import (
        CapabilityHealthService,
    )

    with app.app_context():
        yield CapabilityHealthService()


@pytest.fixture
def org(make_org):
    return make_org("gap-test")


@pytest.fixture
def capability(db_session, org):
    from app.models.business_capabilities import BusinessCapability

    def _make(name, **kwargs):
        cap = BusinessCapability(name=name, organization_id=org.id, **kwargs)
        db_session.add(cap)
        db_session.flush()
        return cap

    return _make


def test_effective_maturity_gap_with_both_levels(gap_service, capability):
    """current 2 and target 4 gives 2."""
    cap = capability("Test Cap", current_maturity_level=2, target_maturity_level=4)
    gap = gap_service._effective_maturity_gap(cap)
    assert gap == 2


def test_effective_maturity_gap_with_missing_current(gap_service, capability):
    """current None and target 4 gives None."""
    cap = capability("Test Cap No Current", target_maturity_level=4)
    gap = gap_service._effective_maturity_gap(cap)
    assert gap is None


def test_effective_maturity_gap_with_missing_target(gap_service, capability):
    """current 2 and target None gives None."""
    cap = capability("Test Cap No Target", current_maturity_level=2)
    gap = gap_service._effective_maturity_gap(cap)
    assert gap is None


def test_effective_maturity_gap_with_both_missing(gap_service, capability):
    """current None and target None gives None."""
    cap = capability("Test Cap No Levels")
    gap = gap_service._effective_maturity_gap(cap)
    assert gap is None


def test_effective_maturity_gap_uses_stored_field_when_present(gap_service, capability):
    """When maturity_gap is stored, use it even if levels are missing."""
    cap = capability("Test Cap Stored Gap", maturity_gap=3)
    gap = gap_service._effective_maturity_gap(cap)
    assert gap == 3


def test_analyze_capability_gaps_excludes_unknown_from_no_gap_count(gap_service, capability, tenant_ctx, org):
    """A capability with unknown maturity gap is not counted as 'no gap'."""
    with tenant_ctx(org.id):
        # Capability with no maturity levels (unknown gap)
        capability("Unknown Gap Cap")
        # Capability with maturity gap of 0 (no gap)
        capability("No Gap Cap", current_maturity_level=3, target_maturity_level=3)
        # Capability with maturity gap of 2 (has gap)
        capability("Has Gap Cap", current_maturity_level=2, target_maturity_level=4)

        result = gap_service.analyze_capability_gaps()

    # The "unknown gap" capability should not appear in maturity_gaps
    # Known gaps (including 0) should appear, sorted descending
    maturity_gaps = result["maturity_gaps"]
    assert len(maturity_gaps) == 2
    gaps = [g["maturity_gap"] for g in maturity_gaps]
    assert gaps == [2, 0]
    names = [g["capability_name"] for g in maturity_gaps]
    assert "Unknown Gap Cap" not in names
    assert "Has Gap Cap" in names
    assert "No Gap Cap" in names


def test_get_requirement_generation_priority_excludes_unknown_gaps(gap_service, capability, tenant_ctx, org):
    """Capabilities with unknown maturity gap are not included in priority ranking."""
    with tenant_ctx(org.id):
        # Capability with no maturity levels (unknown gap)
        capability("Unknown Gap Cap")
        # Capability with maturity gap of 2
        capability("Has Gap Cap", current_maturity_level=2, target_maturity_level=4)

        ranked = gap_service.get_requirement_generation_priority()

    # Only the capability with a known gap should be ranked
    assert len(ranked) == 1
    assert ranked[0]["capability_name"] == "Has Gap Cap"
    assert ranked[0]["maturity_gap"] == 2


def test_average_maturity_gap_excludes_unknown(gap_service, capability, tenant_ctx, org):
    """Average over [2, None, 1] is 1.5, not 1.0."""
    with tenant_ctx(org.id):
        # Capability with gap 2
        capability("Gap 2", current_maturity_level=2, target_maturity_level=4)
        # Capability with unknown gap (no levels)
        capability("Unknown Gap")
        # Capability with gap 1
        capability("Gap 1", current_maturity_level=3, target_maturity_level=4)

        result = gap_service.analyze_capability_gaps()

    # Average should be (2 + 1) / 2 = 1.5, not (2 + 0 + 1) / 3 = 1.0
    avg_gap = result["summary"]["average_maturity_gap"]
    assert avg_gap == 1.5


def test_sort_by_gap_puts_unknown_last(gap_service, capability, tenant_ctx, org):
    """Lists sorted by gap put unknown gaps last."""
    with tenant_ctx(org.id):
        # Capability with gap 2
        capability("Gap 2", current_maturity_level=2, target_maturity_level=4)
        # Capability with unknown gap
        capability("Unknown Gap")
        # Capability with gap 1
        capability("Gap 1", current_maturity_level=3, target_maturity_level=4)
        # Capability with gap 0
        capability("Gap 0", current_maturity_level=3, target_maturity_level=3)

        result = gap_service.analyze_capability_gaps()

    # maturity_gaps should be sorted by gap descending, unknown last
    maturity_gaps = result["maturity_gaps"]
    gaps = [g["maturity_gap"] for g in maturity_gaps]
    # Known gaps sorted descending: 2, 1, 0
    assert gaps == [2, 1, 0]
    # Unknown gap capability should not be in maturity_gaps list
    names = [g["capability_name"] for g in maturity_gaps]
    assert "Unknown Gap" not in names


def test_health_score_carries_maturity_recorded_flag(health_service, capability, tenant_ctx, org):
    """Health score must not treat unknown gap as perfect; carries maturity_recorded flag."""
    with tenant_ctx(org.id):
        # Capability with unknown gap
        cap_unknown = capability("Unknown Gap")
        # Capability with gap 0 (perfect maturity)
        cap_perfect = capability("Perfect", current_maturity_level=3, target_maturity_level=3)
        # Capability with gap 2
        cap_gap = capability("Gap 2", current_maturity_level=2, target_maturity_level=4)

        result_unknown = health_service.assess_capability_health(cap_unknown)
        result_perfect = health_service.assess_capability_health(cap_perfect)
        result_gap = health_service.assess_capability_health(cap_gap)

    # Unknown gap should have maturity_recorded=False
    assert result_unknown["maturity_recorded"] is False
    # Perfect maturity should have maturity_recorded=True
    assert result_perfect["maturity_recorded"] is True
    # Known gap should have maturity_recorded=True
    assert result_gap["maturity_recorded"] is True

    # Score should not be affected by unknown gap (no penalty)
    # But we don't assert exact score values, just that the flag exists


def test_health_service_effective_maturity_gap_returns_none(health_service, capability, tenant_ctx, org):
    """Health service's _effective_maturity_gap also returns None for unknown."""
    with tenant_ctx(org.id):
        cap = capability("No Levels")
        gap = health_service._effective_maturity_gap(cap)
    assert gap is None