"""ArchitectureInferenceRelationship and ReviewDecision gained TenantMixin.

Covers:
  - cross-organisation reads are fenced (an org sees only its own rows)
  - bulk UPDATE / DELETE cannot cross tenants
  - a NULL-organisation row is visible to nobody
  - backfill-proposal-tenancy attributes rows it can determine and leaves the
    rest NULL (agreed source/target elements vs. conflicting vs. unresolvable)
  - a second backfill run is a no-op
"""
import uuid

import pytest


def _user(db_session, org, label="u"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label}-{suffix}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _archimate_element(db_session, org, label="ae"):
    """Create an ArchiMateElement owned by *org* (organization_id is NOT NULL)."""
    from app.models.archimate_core import ArchiMateElement

    suffix = uuid.uuid4().hex[:8]
    ae = ArchiMateElement(
        name=f"{label}-{suffix}",
        type="ApplicationComponent",
        layer="application",
        organization_id=org.id,
    )
    db_session.add(ae)
    db_session.flush()
    return ae


def _air(db_session, org_id=None, source_id=None, target_id=None, architecture_id=1,
         source_tag="rule", rel_type="serving"):
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

    row = ArchitectureInferenceRelationship(
        architecture_id=architecture_id,
        organization_id=org_id,
        source_type="ArchiMateElement",
        source_id=source_id,
        target_type="ArchiMateElement",
        target_id=target_id,
        rel_type=rel_type,
        source_tag=source_tag,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _queue_item(db_session, org_id=None, label="item"):
    from app.models.confidence_review import ReviewQueueItem, ReviewStatus

    row = ReviewQueueItem(
        organization_id=org_id,
        item_type="capability_mapping",
        item_id=None,
        item_name=f"{label}-{uuid.uuid4().hex[:8]}",
        confidence_score=0.75,
        status=ReviewStatus.PENDING,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _decision(db_session, review_item, reviewer, org_id=None):
    from app.models.confidence_review import ReviewDecision

    row = ReviewDecision(
        review_item_id=review_item.id,
        organization_id=org_id,
        decision_type="approve",
        decision_reason="looks fine",
        reviewer_id=reviewer.id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _run(dry_run=False):
    """Invoke the backfill CLI command and return its output."""
    from app import create_app

    app = create_app("testing")
    runner = app.test_cli_runner()
    args = ["backfill-proposal-tenancy"]
    if dry_run:
        args.append("--dry-run")
    result = runner.invoke(args=args)
    return result


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a = make_org("proposal-a")
    org_b = make_org("proposal-b")
    return org_a, org_b


# --------------------------------------------------------------------------
# Cross-tenant read fencing
# --------------------------------------------------------------------------


def test_air_cross_org_read_fenced(db_session, two_orgs, tenant_ctx):
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

    org_a, org_b = two_orgs
    src = _archimate_element(db_session, org_b, "src")
    tgt = _archimate_element(db_session, org_b, "tgt")
    rel = _air(db_session, org_id=org_b.id, source_id=src.id, target_id=tgt.id)
    rel_id = rel.id
    org_a_id, org_b_id = org_a.id, org_b.id
    db_session.commit()

    # expunge_all() first: the row is already in this session's identity map
    # from creating it above, and Session.get() only re-applies the tenant
    # filter on an identity-map MISS (see CLAUDE.md's tenant-isolation note).
    db_session.expunge_all()
    with tenant_ctx(org_a_id):
        assert db_session.get(ArchitectureInferenceRelationship, rel_id) is None, (
            "org A must not see org B's inference relationship"
        )

    db_session.expunge_all()
    with tenant_ctx(org_b_id):
        assert db_session.get(ArchitectureInferenceRelationship, rel_id) is not None


def test_review_decision_cross_org_read_fenced(db_session, two_orgs, tenant_ctx):
    from app.models.confidence_review import ReviewDecision

    org_a, org_b = two_orgs
    user_b = _user(db_session, org_b, "reviewer-b")
    item_b = _queue_item(db_session, org_id=org_b.id)
    decision = _decision(db_session, item_b, user_b, org_id=org_b.id)
    decision_id = decision.id
    org_a_id, org_b_id = org_a.id, org_b.id
    db_session.commit()

    db_session.expunge_all()
    with tenant_ctx(org_a_id):
        assert db_session.get(ReviewDecision, decision_id) is None, (
            "org A must not see org B's review decision"
        )

    db_session.expunge_all()
    with tenant_ctx(org_b_id):
        assert db_session.get(ReviewDecision, decision_id) is not None


# --------------------------------------------------------------------------
# NULL-organisation rows are visible to nobody
# --------------------------------------------------------------------------


def test_air_null_org_row_invisible_to_everyone(db_session, two_orgs, tenant_ctx):
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

    org_a, org_b = two_orgs
    # source_id/target_id carry no FK constraint on this table; arbitrary ids
    # are enough here since this test only exercises the row's own tenant
    # fence, not element-based attribution.
    rel = _air(db_session, org_id=None, source_id=555_000_001, target_id=555_000_002)
    rel_id = rel.id
    org_ids = [org_a.id, org_b.id]  # capture before expunge_all detaches the instances
    db_session.commit()

    for org_id in org_ids:
        db_session.expunge_all()
        with tenant_ctx(org_id):
            assert db_session.get(ArchitectureInferenceRelationship, rel_id) is None, (
                f"a NULL-organisation inference relationship must not be visible to org {org_id}"
            )


def test_review_decision_null_org_row_invisible_to_everyone(db_session, two_orgs, tenant_ctx):
    from app.models.confidence_review import ReviewDecision

    org_a, org_b = two_orgs
    user_a = _user(db_session, org_a, "reviewer")
    item = _queue_item(db_session, org_id=None)
    decision = _decision(db_session, item, user_a, org_id=None)
    decision_id = decision.id
    org_ids = [org_a.id, org_b.id]  # capture before expunge_all detaches the instances
    db_session.commit()

    for org_id in org_ids:
        db_session.expunge_all()
        with tenant_ctx(org_id):
            assert db_session.get(ReviewDecision, decision_id) is None, (
                f"a NULL-organisation review decision must not be visible to org {org_id}"
            )


# --------------------------------------------------------------------------
# Bulk UPDATE / DELETE fencing
# --------------------------------------------------------------------------


def test_air_bulk_update_cannot_cross_tenants(db_session, two_orgs, tenant_ctx):
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

    org_a, org_b = two_orgs
    src = _archimate_element(db_session, org_b, "src")
    tgt = _archimate_element(db_session, org_b, "tgt")
    rel = _air(db_session, org_id=org_b.id, source_id=src.id, target_id=tgt.id, rel_type="serving")
    rel_id = rel.id
    db_session.commit()

    with tenant_ctx(org_a.id):
        ArchitectureInferenceRelationship.query.filter_by(id=rel_id).update(
            {"rel_type": "overwritten-from-org-a"}, synchronize_session=False
        )
        db_session.flush()

    org_b_id = org_b.id
    db_session.expunge_all()
    with tenant_ctx(org_b_id):
        after = db_session.get(ArchitectureInferenceRelationship, rel_id)
        assert after is not None and after.rel_type == "serving", (
            "TENANT LEAK: a bulk UPDATE executed in org A's context modified org B's inference relationship."
        )


def test_air_bulk_delete_cannot_cross_tenants(db_session, two_orgs, tenant_ctx):
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

    org_a, org_b = two_orgs
    src = _archimate_element(db_session, org_b, "src")
    tgt = _archimate_element(db_session, org_b, "tgt")
    rel = _air(db_session, org_id=org_b.id, source_id=src.id, target_id=tgt.id)
    rel_id = rel.id
    db_session.commit()

    with tenant_ctx(org_a.id):
        ArchitectureInferenceRelationship.query.filter_by(id=rel_id).delete(synchronize_session=False)
        db_session.flush()

    org_b_id = org_b.id
    db_session.expunge_all()
    with tenant_ctx(org_b_id):
        assert db_session.get(ArchitectureInferenceRelationship, rel_id) is not None, (
            "TENANT LEAK: a bulk DELETE executed in org A's context removed org B's inference relationship."
        )


def test_review_decision_bulk_update_cannot_cross_tenants(db_session, two_orgs, tenant_ctx):
    from app.models.confidence_review import ReviewDecision

    org_a, org_b = two_orgs
    user_b = _user(db_session, org_b, "reviewer-b")
    item_b = _queue_item(db_session, org_id=org_b.id)
    decision = _decision(db_session, item_b, user_b, org_id=org_b.id)
    decision_id = decision.id
    db_session.commit()

    with tenant_ctx(org_a.id):
        ReviewDecision.query.filter_by(id=decision_id).update(
            {"decision_reason": "overwritten from org A"}, synchronize_session=False
        )
        db_session.flush()

    org_b_id = org_b.id
    db_session.expunge_all()
    with tenant_ctx(org_b_id):
        after = db_session.get(ReviewDecision, decision_id)
        assert after is not None and after.decision_reason == "looks fine", (
            "TENANT LEAK: a bulk UPDATE executed in org A's context modified org B's review decision."
        )


def test_review_decision_bulk_delete_cannot_cross_tenants(db_session, two_orgs, tenant_ctx):
    from app.models.confidence_review import ReviewDecision

    org_a, org_b = two_orgs
    user_b = _user(db_session, org_b, "reviewer-b")
    item_b = _queue_item(db_session, org_id=org_b.id)
    decision = _decision(db_session, item_b, user_b, org_id=org_b.id)
    decision_id = decision.id
    db_session.commit()

    with tenant_ctx(org_a.id):
        ReviewDecision.query.filter_by(id=decision_id).delete(synchronize_session=False)
        db_session.flush()

    org_b_id = org_b.id
    db_session.expunge_all()
    with tenant_ctx(org_b_id):
        assert db_session.get(ReviewDecision, decision_id) is not None, (
            "TENANT LEAK: a bulk DELETE executed in org A's context removed org B's review decision."
        )


# --------------------------------------------------------------------------
# Backfill attribution
# --------------------------------------------------------------------------


def test_backfill_review_decision_from_queue_item(db_session, two_orgs):
    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "reviewer-a")
    item_a = _queue_item(db_session, org_id=org_a.id)
    decision = _decision(db_session, item_a, user_a, org_id=None)
    db_session.commit()

    assert decision.organization_id is None

    result = _run()
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(decision)
    assert decision.organization_id == org_a.id, (
        f"decision should get its queue item's org {org_a.id}, got {decision.organization_id}"
    )


def test_backfill_review_decision_unresolvable_stays_null(db_session, two_orgs):
    """A decision whose queue item is itself unattributed must stay NULL."""
    org_a, _org_b = two_orgs
    user_a = _user(db_session, org_a, "reviewer-a")
    item_unassigned = _queue_item(db_session, org_id=None)
    decision = _decision(db_session, item_unassigned, user_a, org_id=None)
    db_session.commit()

    result = _run()
    assert result.exit_code == 0

    db_session.refresh(decision)
    assert decision.organization_id is None, "decision must stay NULL when its queue item has no org"


def test_backfill_air_agreed_case(db_session, two_orgs):
    """Source and target elements agree on organisation -> row gets that org."""
    org_a, _org_b = two_orgs
    src = _archimate_element(db_session, org_a, "src")
    tgt = _archimate_element(db_session, org_a, "tgt")
    rel = _air(db_session, org_id=None, source_id=src.id, target_id=tgt.id, source_tag="agreed-case")
    db_session.commit()

    assert rel.organization_id is None

    result = _run()
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(rel)
    assert rel.organization_id == org_a.id, (
        f"row should get the agreeing element org {org_a.id}, got {rel.organization_id}"
    )


def test_backfill_air_conflicting_case_stays_null(db_session, two_orgs):
    """Source and target elements disagree on organisation -> row stays NULL."""
    org_a, org_b = two_orgs
    src = _archimate_element(db_session, org_a, "src")
    tgt = _archimate_element(db_session, org_b, "tgt")
    rel = _air(db_session, org_id=None, source_id=src.id, target_id=tgt.id, source_tag="conflict-case")
    db_session.commit()

    result = _run()
    assert result.exit_code == 0, f"backfill failed: {result.output}"
    assert "conflicting" in result.output.lower()

    db_session.refresh(rel)
    assert rel.organization_id is None, (
        "a row whose source and target elements disagree must stay NULL, not guess either side"
    )


def test_backfill_air_unresolvable_case_stays_null(db_session, two_orgs):
    """source_id/target_id that do not resolve to any archimate_elements row stay NULL."""
    org_a, _org_b = two_orgs
    rel = _air(db_session, org_id=None, source_id=999_999_001, target_id=999_999_002,
               source_tag="unresolvable-case")
    db_session.commit()

    result = _run()
    assert result.exit_code == 0, f"backfill failed: {result.output}"

    db_session.refresh(rel)
    assert rel.organization_id is None, (
        "a row whose source/target elements do not exist must stay NULL"
    )


def test_backfill_dry_run_changes_nothing(db_session, two_orgs):
    org_a, _org_b = two_orgs
    src = _archimate_element(db_session, org_a, "src")
    tgt = _archimate_element(db_session, org_a, "tgt")
    rel = _air(db_session, org_id=None, source_id=src.id, target_id=tgt.id)
    db_session.commit()

    result = _run(dry_run=True)
    assert result.exit_code == 0
    assert "dry run" in result.output.lower()

    db_session.refresh(rel)
    assert rel.organization_id is None, "dry-run must not write"


def test_backfill_is_idempotent(db_session, two_orgs):
    """A second run must report zero new attributions and change nothing."""
    org_a, org_b = two_orgs
    src = _archimate_element(db_session, org_a, "src")
    tgt = _archimate_element(db_session, org_a, "tgt")
    rel = _air(db_session, org_id=None, source_id=src.id, target_id=tgt.id)

    conflict_src = _archimate_element(db_session, org_a, "csrc")
    conflict_tgt = _archimate_element(db_session, org_b, "ctgt")
    conflict_rel = _air(db_session, org_id=None, source_id=conflict_src.id,
                         target_id=conflict_tgt.id, rel_type="association")
    db_session.commit()

    result1 = _run()
    assert result1.exit_code == 0
    db_session.refresh(rel)
    db_session.refresh(conflict_rel)
    assert rel.organization_id == org_a.id
    assert conflict_rel.organization_id is None
    first_resolved = rel.organization_id

    result2 = _run()
    assert result2.exit_code == 0
    assert "attributed 0 architecture_inference_relationship row(s)" in result2.output

    db_session.refresh(rel)
    db_session.refresh(conflict_rel)
    assert rel.organization_id == first_resolved, "idempotent re-run must not change an already-resolved row"
    assert conflict_rel.organization_id is None, "idempotent re-run must not resolve a conflicting row"


# --------------------------------------------------------------------------
# Writer: journey_v2_routes._sync_capability_realization_links
# --------------------------------------------------------------------------


def test_capability_realization_sync_sets_organization_id(db_session, two_orgs, tenant_ctx):
    """The wizard's Goal/Driver -> Capability writer runs inside a request (not
    a background thread), but ArchitectureInferenceRelationship's organization_id
    override carries no request-context default -- so it must set the column
    explicitly like every other writer, or every row it creates is NULL-org and
    invisible to the tenant that just created it."""
    from app.modules.solutions_strategic.v2.routes.journey_v2_routes import (
        _sync_capability_realization_links,
    )
    from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship
    from app.models.solution_models import Solution, SolutionArchiMateElement

    org_a, _org_b = two_orgs

    solution = Solution(name="Realization sync test", organization_id=org_a.id)
    db_session.add(solution)
    db_session.flush()

    goal = _archimate_element(db_session, org_a, "goal")
    goal.type = "Goal"
    driver = _archimate_element(db_session, org_a, "driver")
    driver.type = "Driver"
    capability = _archimate_element(db_session, org_a, "capability")
    capability.type = "Capability"
    db_session.flush()

    for element, layer_type in (
        (goal, "motivation"), (driver, "motivation"), (capability, "strategy"),
    ):
        db_session.add(SolutionArchiMateElement(
            solution_id=solution.id, element_id=element.id,
            element_table="archimate_elements", layer_type=layer_type,
        ))
    db_session.commit()

    with tenant_ctx(org_a.id):
        inserted = _sync_capability_realization_links(solution.id)
        assert inserted == 2, f"expected one goal- and one driver-link, got {inserted}"

        rows = ArchitectureInferenceRelationship.query.filter_by(architecture_id=solution.id).all()
        assert len(rows) == 2
        for row in rows:
            assert row.organization_id == org_a.id, (
                "a row this writer creates must carry the solution's organisation, "
                f"not {row.organization_id!r}"
            )
