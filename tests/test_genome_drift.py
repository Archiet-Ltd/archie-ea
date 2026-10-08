"""Tests for the genome drift detector + governed remediation (ADR 0009).

Two layers:
  * Pure, DB-free unit tests for the two detectors that take plain element/rel
    lists (near-duplicate clustering; motivation-without-realization), so the
    core logic is pinned without a database.
  * DB-backed tests against the shared fixtures (`tests/conftest.py`): seed a
    small drifted model for one org, assert the detector finds EXACTLY those
    signals with provenance + spec_hash + byte-identical determinism, that a
    remediation proposal QUEUES through the governed gate (nothing applied), and
    that org A's drift is invisible to org B.
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.modules.genome.services import drift_detector as dd
from app.modules.genome.services.drift_detector import (
    FINDING_CAP_NO_SUPPORT,
    FINDING_DECOMM_MAPPED,
    FINDING_MOTIVATION_NO_REALIZATION,
    FINDING_NEAR_DUPLICATE,
    FINDING_ORPHANED,
    detect_model_drift,
)


# --------------------------------------------------------------------------- #
# Pure unit tests (no DB)                                                      #
# --------------------------------------------------------------------------- #
def _el(id, name, type_, layer):
    return SimpleNamespace(id=id, name=name, type=type_, layer=layer)


def test_near_duplicate_clusters_same_layer_only():
    els = [
        _el(1, "Order Management", "Node", "technology"),
        _el(2, "Order Management System", "Node", "technology"),
        _el(3, "Payroll", "Node", "technology"),
        # same name but a different layer — legitimately distinct, must NOT cluster
        _el(4, "Order Management", "ApplicationComponent", "application"),
    ]
    findings = dd._detect_near_duplicates(els)
    assert len(findings) == 1
    f = findings[0]
    assert f["type"] == FINDING_NEAR_DUPLICATE
    ids = {e["archimate_element_id"] for e in f["elements"]}
    assert ids == {1, 2}
    # every element in the finding carries its provenance id
    assert all(e.get("archimate_element_id") is not None for e in f["elements"])


def test_motivation_without_realization_flags_untargeted_only():
    els = [
        _el(10, "Reduce cost", "Goal", "motivation"),        # no incoming -> flagged
        _el(11, "GDPR compliance", "Requirement", "motivation"),  # realized -> clean
        _el(12, "Billing", "ApplicationComponent", "application"),
    ]
    rels = [SimpleNamespace(id=1, source_id=12, target_id=11)]  # 12 realizes 11
    findings = dd._detect_motivation_without_realization(els, rels)
    ids = {f["elements"][0]["archimate_element_id"] for f in findings}
    assert ids == {10}
    assert findings[0]["type"] == FINDING_MOTIVATION_NO_REALIZATION


# --------------------------------------------------------------------------- #
# DB-backed seed helpers                                                        #
# --------------------------------------------------------------------------- #
def _make_element(db_session, org_id, name, type_, layer, deleted=False):
    from app.models.archimate_core import ArchiMateElement

    e = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    if deleted:
        from app.datetime_helpers import utcnow

        e.deleted_at = utcnow()
    db_session.add(e)
    db_session.flush()
    return e


def _make_capability(db_session, org_id, name, element_id=None):
    from app.models.business_capabilities import BusinessCapability

    c = BusinessCapability(
        name=name,
        code=f"CAP-{uuid.uuid4().hex[:8]}",
        organization_id=org_id,
        archimate_element_id=element_id,
    )
    db_session.add(c)
    db_session.flush()
    return c


def _make_application(db_session, org_id, name, lifecycle=None, element_id=None):
    from app.models.application_portfolio import ApplicationComponent

    a = ApplicationComponent(
        name=name,
        organization_id=org_id,
        lifecycle_status=lifecycle,
        archimate_element_id=element_id,
    )
    db_session.add(a)
    db_session.flush()
    return a


def _map(db_session, org_id, app_id, cap_id):
    from app.models.application_capability import ApplicationCapabilityMapping

    m = ApplicationCapabilityMapping(
        organization_id=org_id,
        application_component_id=app_id,
        business_capability_id=cap_id,
        support_level="full",
    )
    db_session.add(m)
    db_session.flush()
    return m


def _seed_drifted_org(db_session, org_id):
    """Seed exactly three drift signals: orphan, unsupported cap, decomm-mapped.

    Returns a dict of the element ids the detector is expected to flag.
    """
    # (a) an orphan: an element wired to nothing and linked from nothing.
    orphan = _make_element(db_session, org_id, "Legacy Widget", "ApplicationComponent", "application")

    # (b) an unsupported capability: a Capability element behind a business_capability
    #     row with NO application-capability mapping.
    cap_elem = _make_element(db_session, org_id, "Fraud Detection", "Capability", "strategy")
    _make_capability(db_session, org_id, "Fraud Detection", element_id=cap_elem.id)

    # (c) a decommissioned-but-mapped application: a retired app still mapped as
    #     supporting a (separate, supported) capability.
    app_elem = _make_element(db_session, org_id, "OldBilling", "ApplicationComponent", "application")
    app_row = _make_application(db_session, org_id, "OldBilling", lifecycle="retired", element_id=app_elem.id)
    supported_cap_elem = _make_element(db_session, org_id, "Billing", "Capability", "strategy")
    supported_cap = _make_capability(db_session, org_id, "Billing", element_id=supported_cap_elem.id)
    _map(db_session, org_id, app_row.id, supported_cap.id)

    return {
        "orphan_element_id": orphan.id,
        "unsupported_cap_element_id": cap_elem.id,
        "decomm_app_element_id": app_elem.id,
    }


# --------------------------------------------------------------------------- #
# DB-backed tests                                                              #
# --------------------------------------------------------------------------- #
def test_detector_finds_exactly_seeded_signals(app, db_session, make_org):
    with app.app_context():
        org = make_org("drift")
        expected = _seed_drifted_org(db_session, org.id)

        report = detect_model_drift(org.id, session=db_session)

        assert report["organization_id"] == org.id
        assert report["spec_hash"].startswith("sha256:")

        by_type = {}
        for f in report["findings"]:
            by_type.setdefault(f["type"], []).append(f)

        # orphan: exactly the seeded orphan element
        orphans = by_type.get(FINDING_ORPHANED, [])
        assert [f["elements"][0]["archimate_element_id"] for f in orphans] == [
            expected["orphan_element_id"]
        ]

        # unsupported capability: exactly the seeded cap element, provenance present
        caps = by_type.get(FINDING_CAP_NO_SUPPORT, [])
        assert [f["elements"][0]["archimate_element_id"] for f in caps] == [
            expected["unsupported_cap_element_id"]
        ]

        # decommissioned-but-mapped: exactly the seeded retiring app element
        decomm = by_type.get(FINDING_DECOMM_MAPPED, [])
        assert [f["elements"][0]["archimate_element_id"] for f in decomm] == [
            expected["decomm_app_element_id"]
        ]
        # the retiring finding also carries the supported capability as provenance
        assert any(
            e["role"] == "supported_capability" for e in decomm[0]["elements"]
        )

        # honest omission surfaced, not silently dropped
        assert "model_age" in report["uncomputable_signals"]


def test_report_is_deterministic_byte_identical(app, db_session, make_org):
    with app.app_context():
        org = make_org("determinism")
        _seed_drifted_org(db_session, org.id)

        r1 = detect_model_drift(org.id, session=db_session)
        r2 = detect_model_drift(org.id, session=db_session)

        assert r1["spec_hash"] == r2["spec_hash"]
        assert json.dumps(r1, sort_keys=True, default=str) == json.dumps(
            r2, sort_keys=True, default=str
        )


def test_drift_is_org_scoped(app, db_session, make_org):
    with app.app_context():
        org_a = make_org("a")
        org_b = make_org("b")
        expected = _seed_drifted_org(db_session, org_a.id)

        report_b = detect_model_drift(org_b.id, session=db_session)

        seen_ids = {
            e["archimate_element_id"]
            for f in report_b["findings"]
            for e in f["elements"]
        }
        assert expected["orphan_element_id"] not in seen_ids
        assert expected["unsupported_cap_element_id"] not in seen_ids
        assert expected["decomm_app_element_id"] not in seen_ids
        assert report_b["summary"]["total"] == 0


def test_index_shows_error_not_empty_state_for_unreadable_stored_report(
    app, db_session, make_org, client, login_as
):
    """PR 304 fix round: a stored row that cannot be rendered must show an
    explicit error, never the "Not yet computed" empty-state copy -- that
    copy claims no report exists, which is false when one does but cannot be
    read. The empty state is only correct when there is truly no stored row.
    """
    from app.datetime_helpers import utcnow
    from app.models.drift_report import DriftReport
    from app.models.user import User

    org_unreadable = make_org("drift-unreadable")
    org_no_row = make_org("drift-no-row")

    user_unreadable = User(
        email=f"drift-unreadable-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_unreadable.id,
        confirmed=True,
    )
    user_unreadable.password = "not-used-in-tests-123"
    db_session.add(user_unreadable)

    user_no_row = User(
        email=f"drift-no-row-{uuid.uuid4().hex[:8]}@example.com",
        organization_id=org_no_row.id,
        confirmed=True,
    )
    user_no_row.password = "not-used-in-tests-123"
    db_session.add(user_no_row)
    db_session.flush()

    # A stored row whose report_json is not the dict shape the emitter
    # expects (e.g. a foreign/future format) -- genuinely unreadable, not a
    # fabricated failure.
    unreadable = DriftReport(
        organization_id=org_unreadable.id,
        report_json=["not", "the", "expected", "dict", "shape"],
        spec_hash="sha256:unreadable",
        computed_at=utcnow(),
        finding_count=0,
    )
    db_session.add(unreadable)
    db_session.commit()

    login_as(client, user_unreadable)
    resp_unreadable = client.get("/genome/model-health/")
    assert resp_unreadable.status_code == 200
    body_unreadable = resp_unreadable.get_data(as_text=True)
    assert "could not be rendered" in body_unreadable
    assert "Not yet computed" not in body_unreadable

    login_as(client, user_no_row)
    resp_no_row = client.get("/genome/model-health/")
    assert resp_no_row.status_code == 200
    body_no_row = resp_no_row.get_data(as_text=True)
    assert "first health scan is being prepared" in body_no_row
    assert "could not be rendered" not in body_no_row


def test_remediation_queues_through_governed_gate_and_applies_nothing(
    app, db_session, make_org
):
    from app.models.archimate_core import ArchiMateElement
    from app.models.user import User
    from app.modules.genome.routes.drift_routes import _build_remediation_patch
    from app.modules.genome.patch.validator import validate_genome_patch
    from app.modules.genome.patch.proposer import APPLY_ENTITY_TYPE, propose_genome_patch

    with app.app_context():
        org = make_org("remediate")
        expected = _seed_drifted_org(db_session, org.id)
        orphan_id = expected["orphan_element_id"]

        user = User(email=f"drift-{uuid.uuid4().hex[:8]}@example.com", organization_id=org.id)
        user.password_hash = "x"  # login not exercised; only user_id/org are needed
        db_session.add(user)
        db_session.flush()

        # The route helper builds a schema-valid modify patch for the orphan.
        patch = _build_remediation_patch(org.id, FINDING_ORPHANED, orphan_id)
        assert patch is not None
        assert validate_genome_patch(patch).valid
        assert patch["operation"] == "modify"
        assert patch["element"]["element_id"] == orphan_id

        # Snapshot the element BEFORE proposing — nothing must change.
        before_status = db_session.get(ArchiMateElement, orphan_id).status

        result = propose_genome_patch(
            request_text="remediate orphan",
            user_id=user.id,
            patch_source=lambda _t, _c: patch,
            context={"organization_id": org.id},
        )

        # It QUEUED — it did not apply.
        assert result["success"] is True, result
        assert result["status"] == "pending_approval"
        approval_id = result["approval_id"]

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

        approval = db_session.get(AIChatCRUDApproval, approval_id)
        assert approval is not None
        assert approval.entity_type == APPLY_ENTITY_TYPE
        assert approval.status == ApprovalStatus.PENDING

        # The genome element is UNCHANGED — the patch is queued, not applied.
        after = db_session.get(ArchiMateElement, orphan_id)
        assert after.status == before_status
