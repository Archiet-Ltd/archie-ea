"""Composer-QA 3.2 / 3.3: the ArchiMate validator must not fire false positives
from un-normalized comparisons.

3.2: relationship types are stored short-form / mixed-case ('realization',
     'Realization', 'Flow') but the cross-layer rule lists use
     'RealizationRelationship'. A valid strategy->business realization was
     wrongly warned ("only [...RealizationRelationship...] allowed, got realization").
3.3: element layers are stored 'implementation & migration' but the layer-type
     table keys on 'implementation'. A valid WorkPackage was wrongly flagged
     "WorkPackage is not valid for implementation & migration layer".
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _svc():
    from app.modules.architecture.services.archimate_validation_service import (
        ArchiMateValidationService,
    )
    return ArchiMateValidationService()


def test_norm_helpers():
    from app.modules.architecture.services.archimate_validation_service import (
        _norm_layer, _norm_rel,
    )
    assert _norm_layer("implementation & migration") == "implementation"
    assert _norm_layer("Implementation & Migration") == "implementation"
    assert _norm_layer("implementation_migration") == "implementation"
    assert _norm_layer("business") == "business"
    assert _norm_rel("RealizationRelationship") == "realization"
    assert _norm_rel("Realization") == "realization"
    assert _norm_rel("realization") == "realization"
    assert _norm_rel("Flow") == "flow"
    assert _norm_rel("Uses") == "serving"


def test_cmp33_workpackage_valid_in_implementation_migration_layer():
    svc = _svc()
    assert svc._is_valid_type_for_layer("WorkPackage", "implementation & migration") is True
    assert svc._is_valid_type_for_layer("WorkPackage", "Implementation & Migration") is True
    # a genuinely wrong pairing still fails
    assert svc._is_valid_type_for_layer("BusinessActor", "implementation & migration") is False


def _make_el(db_session, org_id, layer, type_):
    from app.models.archimate_core import ArchiMateElement
    el = ArchiMateElement(name=f"E-{uuid.uuid4().hex[:6]}", type=type_, layer=layer,
                          organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def test_cmp32_strategy_to_business_realization_not_flagged(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateRelationship
    org = make_org("v")
    with tenant_ctx(org.id):
        src = _make_el(db_session, org.id, "strategy", "Capability")
        tgt = _make_el(db_session, org.id, "business", "BusinessService")
        # short-form lowercase, as stored in the DB
        rel = ArchiMateRelationship(type="realization", source_id=src.id, target_id=tgt.id)
        db_session.add(rel)
        db_session.flush()
        issues = _svc()._check_cross_layer(rel)
    msgs = " ".join(i.get("message", "") for i in issues)
    assert "only" not in msgs and "allowed, got" not in msgs, \
        f"valid strategy->business realization was falsely flagged: {issues}"


def test_cmp32_mixed_case_realization_also_ok(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateRelationship
    org = make_org("v")
    with tenant_ctx(org.id):
        src = _make_el(db_session, org.id, "strategy", "Capability")
        tgt = _make_el(db_session, org.id, "business", "BusinessService")
        rel = ArchiMateRelationship(type="Realization", source_id=src.id, target_id=tgt.id)
        db_session.add(rel)
        db_session.flush()
        issues = _svc()._check_cross_layer(rel)
    assert not [i for i in issues if "allowed, got" in i.get("message", "")]


def test_cmp32_genuinely_invalid_cross_layer_still_warns(db_session, make_org, tenant_ctx):
    """The fix must not silence REAL violations — a strategy->business
    composition (not in the allowed set) should still be flagged."""
    from app.models.archimate_core import ArchiMateRelationship
    org = make_org("v")
    with tenant_ctx(org.id):
        src = _make_el(db_session, org.id, "strategy", "Capability")
        tgt = _make_el(db_session, org.id, "business", "BusinessService")
        rel = ArchiMateRelationship(type="composition", source_id=src.id, target_id=tgt.id)
        db_session.add(rel)
        db_session.flush()
        issues = _svc()._check_cross_layer(rel)
    assert any("allowed, got" in i.get("message", "") for i in issues), \
        "a real cross-layer violation must still be reported"
