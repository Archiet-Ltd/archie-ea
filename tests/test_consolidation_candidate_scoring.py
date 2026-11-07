"""A consolidation candidate needs something two applications share, and says what it is.

Two organisations, each with applications in one business domain. Sharing a domain
alone once scored every pair 100%; now only a shared capability, a shared business
function or a near-identical name proposes a pair, and the reason is stored with it.
"""
from __future__ import annotations

import uuid

from tests.test_consolidation_isolation import _app


def _capability(db_session, org, name):
    from app.models.business_capabilities import BusinessCapability

    row = BusinessCapability(name=name, organization_id=org.id, level=2, code="C" + uuid.uuid4().hex[:10])
    db_session.add(row)
    db_session.flush()
    return row


def _support(db_session, org, application, capability):
    from app.models.application_capability import ApplicationCapabilityMapping

    db_session.add(
        ApplicationCapabilityMapping(
            organization_id=org.id,
            application_component_id=application.id,
            business_capability_id=capability.id,
            is_active=True,
        )
    )
    db_session.flush()


def _world(db_session, make_org):
    org_a, org_b = make_org("scoreA"), make_org("scoreB")
    tag = uuid.uuid4().hex[:6]
    out = {"org_a": org_a, "org_b": org_b, "tag": tag}
    for key, org in (("a", org_a), ("b", org_b)):
        apps = [_app(db_session, org, f"{n} {key} {tag}") for n in ("Relay", "Identity", "Billing", "Ledger")]
        for row in apps:
            row.business_domain = "IT Operations"
        cap = _capability(db_session, org, f"Order Fulfilment {key} {tag}")
        _support(db_session, org, apps[2], cap)
        _support(db_session, org, apps[3], cap)
        out[key] = apps
        out["cap_" + key] = cap
    db_session.commit()
    return out


def test_only_applications_sharing_a_capability_are_paired(db_session, make_org, tenant_ctx):
    from app.models.consolidation import ConsolidationCandidate
    from app.services.consolidation_service import ConsolidationService as S

    w = _world(db_session, make_org)
    with tenant_ctx(w["org_a"].id):
        result = S.detect_duplicate_candidates()
        assert result["success"], result
        rows = ConsolidationCandidate.query.filter_by(organization_id=w["org_a"].id).all()
    assert len(rows) == 1, [(r.primary_application_id, r.duplicate_application_id, r.similarity_score) for r in rows]
    row = rows[0]
    assert {row.primary_application_id, row.duplicate_application_id} == {w["a"][2].id, w["a"][3].id}
    assert row.detection_method == "capability_overlap"
    assert w["cap_a"].name in row.notes
    assert row.similarity_score == 1.0
    with tenant_ctx(w["org_b"].id):
        assert ConsolidationCandidate.query.filter_by(organization_id=w["org_b"].id).count() == 0


def test_a_partial_overlap_scores_below_a_full_one(db_session, make_org, tenant_ctx):
    from app.models.consolidation import ConsolidationCandidate
    from app.services.consolidation_service import ConsolidationService as S

    w = _world(db_session, make_org)
    extra = _capability(db_session, w["org_a"], f"Billing {w['tag']}")
    _support(db_session, w["org_a"], w["a"][2], extra)
    db_session.commit()
    with tenant_ctx(w["org_a"].id):
        S.detect_duplicate_candidates()
        row = ConsolidationCandidate.query.filter_by(organization_id=w["org_a"].id).one()
    assert 0 < row.similarity_score < 1


def test_an_earlier_unfounded_pair_is_dismissed_on_the_next_run(db_session, make_org, tenant_ctx):
    from app.models.consolidation import ConsolidationCandidate
    from app.services.consolidation_service import ConsolidationService as S

    w = _world(db_session, make_org)
    stale = ConsolidationCandidate(
        organization_id=w["org_a"].id,
        primary_application_id=w["a"][0].id,
        duplicate_application_id=w["a"][1].id,
        similarity_score=1.0,
        detection_method="function_overlap",
        status="pending_review",
    )
    other = ConsolidationCandidate(
        organization_id=w["org_b"].id,
        primary_application_id=w["b"][0].id,
        duplicate_application_id=w["b"][1].id,
        similarity_score=1.0,
        detection_method="function_overlap",
        status="pending_review",
    )
    db_session.add_all([stale, other])
    db_session.commit()
    with tenant_ctx(w["org_a"].id):
        result = S.detect_duplicate_candidates()
        assert result["candidates_dismissed"] == 1
    db_session.expire_all()
    assert db_session.get(ConsolidationCandidate, stale.id).status == "rejected"
    assert "share no capability" in db_session.get(ConsolidationCandidate, stale.id).notes
    assert db_session.get(ConsolidationCandidate, other.id).status == "pending_review"


def test_a_loosely_alike_name_pair_from_the_old_rule_is_dismissed_on_the_next_run(db_session, make_org, tenant_ctx):
    from app.models.consolidation import ConsolidationCandidate
    from app.services.consolidation_service import ConsolidationService as S

    w = _world(db_session, make_org)
    loose = ConsolidationCandidate(
        organization_id=w["org_a"].id,
        primary_application_id=w["a"][0].id,
        duplicate_application_id=w["a"][1].id,
        similarity_score=0.74,
        detection_method="name_match",
        status="pending_review",
    )
    other = ConsolidationCandidate(
        organization_id=w["org_b"].id,
        primary_application_id=w["b"][0].id,
        duplicate_application_id=w["b"][1].id,
        similarity_score=0.74,
        detection_method="name_match",
        status="pending_review",
    )
    db_session.add_all([loose, other])
    db_session.commit()
    with tenant_ctx(w["org_a"].id):
        result = S.detect_duplicate_candidates()
        assert result["candidates_dismissed"] == 1
    db_session.expire_all()
    assert db_session.get(ConsolidationCandidate, loose.id).status == "rejected"
    assert db_session.get(ConsolidationCandidate, other.id).status == "pending_review"


def test_pending_pairs_are_listed_before_dismissed_ones(db_session, make_org, tenant_ctx):
    from app.models.consolidation import ConsolidationCandidate
    from app.services.consolidation_service import ConsolidationService as S

    w = _world(db_session, make_org)
    a = w["a"]
    db_session.add_all([
        ConsolidationCandidate(organization_id=w["org_a"].id, primary_application_id=a[0].id,
                               duplicate_application_id=a[1].id, similarity_score=0.99,
                               detection_method="capability_overlap", status="rejected"),
        ConsolidationCandidate(organization_id=w["org_a"].id, primary_application_id=a[2].id,
                               duplicate_application_id=a[3].id, similarity_score=0.5,
                               detection_method="capability_overlap", status="pending_review"),
    ])
    db_session.commit()
    with tenant_ctx(w["org_a"].id):
        statuses = [c["status"] for c in S.get_all_candidates()]
    assert statuses == ["pending_review", "rejected"]
