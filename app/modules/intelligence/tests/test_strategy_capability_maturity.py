"""L2 Strategy: ``IntelligenceQueryService.strategy_for_element``.

Fixtures discovered via conftest.py's import of tests.conftest.
"""

from __future__ import annotations

import uuid


def _org_suffix() -> str:
    return uuid.uuid4().hex[:8]


def _capability(db_session, org_id, name, code, *, current=None, target=None):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name=name,
        code=code,
        organization_id=org_id,
        scope="tenant",
        level=1,
        current_maturity_level=current,
        target_maturity_level=target,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _initiative(db_session, element_id, name):
    from app.models.enterprise_intelligence import PortfolioInitiative

    initiative = PortfolioInitiative(
        name=name,
        archimate_element_id=element_id,
        total_budget=10000.0,
        spent_to_date=5000.0,
        status="active",
    )
    db_session.add(initiative)
    db_session.flush()
    return initiative


# --- Test (12): Capability element carries maturity block --------------------------------


def test_capability_element_capability_maturity_on_initiative(app, db_session, make_org, tenant_ctx):
    """Test (12): an initiative on a Capability element at 2 -> 4 ->
    each initiative row carries capability_maturity with under_target is True,
    target_gap == 2.
    """
    from app.models import ArchiMateElement
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac12")
    cap = _capability(db_session, org.id, "Cap", f"MAT3-AC12-{_org_suffix()}", current=2, target=4)
    db_session.flush()

    element = ArchiMateElement(name="Cap Element", type="Capability", organization_id=org.id)
    db_session.add(element)
    db_session.flush()

    cap.archimate_element_id = element.id
    db_session.flush()

    _initiative(db_session, element.id, "Initiative 1")
    db_session.commit()

    with tenant_ctx(org.id):
        result = IntelligenceQueryService.strategy_for_element(element.id)
    assert len(result["initiatives"]) == 1
    initiative_row = result["initiatives"][0]
    assert "capability_maturity" in initiative_row
    block = initiative_row["capability_maturity"]
    assert block["under_target"] is True
    assert block["target_gap"] == 2


# --- Test (13): Non-Capability element carries no capability_maturity key ------------------


def test_application_component_element_no_capability_maturity(
    app, db_session, make_org, tenant_ctx
):
    """Test (13): on an ApplicationComponent element -> no capability_maturity key."""
    from app.models import ArchiMateElement
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac13")
    element = ArchiMateElement(
        name="App Element", type="ApplicationComponent", organization_id=org.id
    )
    db_session.add(element)
    db_session.flush()

    _initiative(db_session, element.id, "Initiative 1")
    db_session.commit()

    with tenant_ctx(org.id):
        result = IntelligenceQueryService.strategy_for_element(element.id)
    assert len(result["initiatives"]) == 1
    initiative_row = result["initiatives"][0]
    assert "capability_maturity" not in initiative_row


# --- Test (14): Cross-organisation: B-owned row pointing at A element - no leak -----------


def test_two_org_cabilidade_maturity_not_leaked(
    app, db_session, make_org, tenant_ctx
):
    """Test (14): B-owned row pointing at A's Capability element ->
    A's block never shows B's maturity.
    """
    from app.models import ArchiMateElement
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("mat3-ac14-a")
    org_b = make_org("mat3-ac14-b")

    element_a = ArchiMateElement(name="Cap A", type="Capability", organization_id=org_a.id)
    db_session.add(element_a)
    db_session.flush()

    cap_b = _capability(
        db_session, org_b.id, "Cap B", f"MAT3-AC14-B-{_org_suffix()}", current=5, target=5
    )
    cap_b.archimate_element_id = element_a.id
    db_session.flush()

    _initiative(db_session, element_a.id, "Initiative A")
    db_session.commit()

    with tenant_ctx(org_a.id):
        result = IntelligenceQueryService.strategy_for_element(element_a.id)
    assert len(result["initiatives"]) == 1
    block = result["initiatives"][0]["capability_maturity"]
    assert block["assessed"] is False
    assert block["current"] is None


# --- Test (15): Unassessed capability -> assessed is False, current is None ---------------


def test_unassessed_capability_maturity(app, db_session, make_org, tenant_ctx):
    """Test (15): unassessed -> assessed is False, current is None."""
    from app.models import ArchiMateElement
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("mat3-ac15")
    cap = _capability(
        db_session, org.id, "Cap", f"MAT3-AC15-{_org_suffix()}", current=None, target=None
    )
    db_session.flush()

    element = ArchiMateElement(name="Cap", type="Capability", organization_id=org.id)
    db_session.add(element)
    db_session.flush()

    cap.archimate_element_id = element.id
    db_session.flush()

    _initiative(db_session, element.id, "Initiative 1")
    db_session.commit()

    with tenant_ctx(org.id):
        result = IntelligenceQueryService.strategy_for_element(element.id)
    assert len(result["initiatives"]) == 1
    block = result["initiatives"][0]["capability_maturity"]
    assert block["assessed"] is False
    assert block["current"] is None


# --- Test (16): One helper call per answer (spy) ------------------------------------------


def test_strategy_for_element_one_maturity_call(
    app, db_session, make_org, monkeypatch, tenant_ctx
):
    """Test (16): maturity_for_elements is called exactly once."""
    from app.models import ArchiMateElement
    from app.modules.capabilities.services.capability_heatmap_service import (
        CapabilityHeatmapService,
    )
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(CapabilityHeatmapService, "maturity_for_elements", spy)

    org = make_org("mat3-ac16")

    element = ArchiMateElement(name="Cap", type="Capability", organization_id=org.id)
    db_session.add(element)
    db_session.flush()

    cap = _capability(
        db_session, org.id, "Cap", f"MAT3-AC16-{_org_suffix()}", current=2, target=4
    )
    cap.archimate_element_id = element.id
    db_session.flush()

    _initiative(db_session, element.id, "Initiative 1")
    _initiative(db_session, element.id, "Initiative 2")
    db_session.commit()

    with tenant_ctx(org.id):
        IntelligenceQueryService.strategy_for_element(element.id)

    assert len(calls) == 1