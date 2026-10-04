"""Invariants for the tenant-scoped typed ARB read boundary.

These use the shared fixtures in ``tests/conftest.py`` (``app``, ``db_session``,
``make_org``, ``tenant_ctx``) so every row is written inside a transaction that
is always rolled back.

The typed graph is built directly rather than through the command services:
those commit through their own sessions, which the rolled-back fixture cannot
see.  Database triggers are suspended for the *setup writes only*
(``session_replication_role``), so the table CHECK constraints — the actual
shape contract — still apply to every row these tests create.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app import db


def _sql(session, statement):
    session.execute(db.text(statement))


class _Graph:
    """The identities one built typed ARB cycle exposes to a test."""

    def __init__(self, **values):
        self.__dict__.update(values)


def _letters_only_token(length=10):
    """Return an uppercase token that never contains decimal digits.

    The cross-tenant successor isolation test proves a foreign review item id
    never appears anywhere in the visible identity payload.  A random decimal id
    can appear as a substring inside an unrelated UUID-derived review number,
    which turns that assertion into an intermittent false positive.  Mapping a
    UUID nibble to ``A``-``P`` keeps the token collision-free for that check
    without weakening the leak assertion itself.
    """

    alphabet = "ABCDEFGHIJKLMNOP"
    return "".join(alphabet[int(ch, 16)] for ch in uuid.uuid4().hex[:length])


def _user(db_session, org, label, *, role="enterprise_architect", admin=False):
    from app.models.user import User

    user = User(
        organization_id=org.id,
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        enterprise_role=role,
        confirmed=True,
        is_org_admin=admin,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _build_adr_cycle(
    db_session,
    org,
    submitter,
    *,
    status="submitted",
    corrupt_hash=False,
    historical=False,
    adr=None,
    cycle_number=1,
    predecessor_cycle_id=None,
):
    """Create one ADR subject, its pinned snapshot, cycle and review item."""
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
    from app.models.transformation_decision import ARBSubjectEvidenceSnapshot

    suffix = uuid.uuid4().hex[:10]
    review_number_token = _letters_only_token()
    _sql(db_session, "SET LOCAL session_replication_role = replica")
    existing_adr = adr
    adr = existing_adr or ArchitectureDecision(
        organization_id=org.id,
        decision_id=f"AD-{suffix[:7]}",
        title=f"Read model ADR {suffix}",
        status="proposed",
        context="A governed choice needs evidence.",
        decision="Adopt the governed option.",
        rationale="It is testable.",
        consequences="Conditions must be verified.",
        created_by_id=submitter.id,
    )
    if existing_adr is None:
        db_session.add(adr)
        db_session.flush()

    snapshot = None
    if not historical:
        snapshot = ARBSubjectEvidenceSnapshot(
            organization_id=org.id,
            subject_type="adr",
            subject_id=adr.id,
            adr_id=adr.id,
            schema_version=1,
            policy_version="adr-arb-r2",
            captured_by_id=submitter.id,
            captured_at=datetime.now(timezone.utc),
            payload={
                "title": adr.title,
                "context": adr.context,
                "decision": adr.decision,
                "rationale": adr.rationale,
                "consequences": adr.consequences,
            },
            citations={"linked_resources": []},
        )
        snapshot.content_hash = (
            "0" * 64 if corrupt_hash else snapshot.recompute_content_hash()
        )
        db_session.add(snapshot)
        db_session.flush()

    open_cycle = status in {"submitted", "under_review", "pending_information"}
    cycle = ARBReviewCycle(
        organization_id=org.id,
        subject_type="adr",
        subject_id=adr.id,
        adr_id=adr.id,
        subject_evidence_snapshot_id=None if historical else snapshot.id,
        review_number=f"REV-{review_number_token}",
        cycle_number=cycle_number,
        predecessor_cycle_id=predecessor_cycle_id,
        status="historical_unverified" if historical else status,
        opened_at=datetime.now(timezone.utc),
        closed_at=None if open_cycle and not historical else datetime.now(timezone.utc),
        terminal_outcome=(
            "historical_unverified"
            if historical
            else (None if open_cycle else status)
        ),
        condition_projection_revision=None,
        migration_gap_reason="No provable immutable evidence." if historical else None,
        legacy_source_type="arb_review_items" if historical else None,
        legacy_source_id=1 if historical else None,
    )
    db_session.add(cycle)
    db_session.flush()

    review = ARBReviewItem(
        organization_id=org.id,
        review_number=f"REV-ITEM-{suffix}",
        title=f"Read model ADR {suffix}",
        review_type="architecture_change",
        subject_type="adr",
        subject_id=adr.id,
        adr_id=adr.id,
        subject_evidence_snapshot_id=None if historical else snapshot.id,
        review_cycle_id=cycle.id,
        status="historical_unverified" if historical else status,
        submitter_id=submitter.id,
        submitted_at=datetime.now(timezone.utc),
    )
    db_session.add(review)
    db_session.flush()
    _sql(db_session, "SET LOCAL session_replication_role = origin")
    return _Graph(adr=adr, snapshot=snapshot, cycle=cycle, review=review)


def _receipt(db_session, org, actor, operation):
    from app.models.transformation_execution import CommandIdempotencyRecord

    suffix = uuid.uuid4().hex
    record = CommandIdempotencyRecord(
        organization_id=org.id,
        actor_id=actor.id,
        operation=operation,
        idempotency_key=suffix,
        request_digest="a" * 64,
        natural_key=f"{operation}:{suffix}",
        status="succeeded",
        lease_generation=1,
        claim_token=suffix[:32],
        claimant_request_id=suffix,
    )
    db_session.add(record)
    db_session.flush()
    return record


def _decide(db_session, org, graph, decider, *, outcome, projected_status):
    """Record a terminal decision event and project the cycle separately."""
    from app.models.arb_decision_event import ARBDecisionEvent

    _sql(db_session, "SET LOCAL session_replication_role = replica")
    receipt = _receipt(db_session, org, decider, "arb.decision.record")
    event = ARBDecisionEvent(
        organization_id=org.id,
        review_cycle_id=graph.cycle.id,
        review_item_id=graph.review.id,
        outcome=outcome,
        from_state=graph.cycle.status,
        to_state=outcome,
        rationale="Recorded for the read-model contract.",
        conditions_json=(
            [{"condition_number": "C-1", "description": "Provide deployment proof."}]
            if outcome == "approved_with_conditions"
            else []
        ),
        subject_type="adr",
        subject_id=graph.adr.id,
        adr_id=graph.adr.id,
        subject_evidence_snapshot_id=graph.snapshot.id,
        actor_id=decider.id,
        command_receipt_id=receipt.id,
        command_generation=1,
    )
    db_session.add(event)
    db_session.flush()

    graph.cycle.status = projected_status
    graph.cycle.terminal_outcome = outcome
    graph.cycle.closed_at = datetime.now(timezone.utc)
    graph.cycle.condition_projection_revision = 1
    graph.review.status = projected_status
    graph.review.decision = outcome
    graph.review.conditions = event.conditions_json
    graph.review.decided_by_id = decider.id
    graph.review.decision_date = datetime.now(timezone.utc)
    graph.review.review_completed_at = datetime.now(timezone.utc)
    db_session.flush()
    _sql(db_session, "SET LOCAL session_replication_role = origin")
    graph.decision_event = event
    graph.conditions = []
    return event


def _condition(db_session, org, graph, *, status, evidence_submitted_by=None):
    """Create one canonical condition, plus its evidence record when submitted."""
    import hashlib
    import json

    from app.models.arb_condition_evidence import ARBConditionEvidenceRecord
    from app.models.arb_decision_event import ARBCondition

    _sql(db_session, "SET LOCAL session_replication_role = replica")
    condition = ARBCondition(
        organization_id=org.id,
        decision_event_id=graph.decision_event.id,
        review_cycle_id=graph.cycle.id,
        review_item_id=graph.review.id,
        condition_number="C-1",
        description="Provide deployment proof.",
        blocks_execution=True,
        status="pending",
        revision=1,
    )
    db_session.add(condition)
    db_session.flush()

    if status == "evidence_submitted":
        assert evidence_submitted_by is not None
        now = datetime.now(timezone.utc)
        document = json.dumps(
            {"condition_id": condition.id, "verified": True},
            sort_keys=True,
            separators=(",", ":"),
        )
        receipt = _receipt(db_session, org, evidence_submitted_by, "arb.condition.evidence")
        record = ARBConditionEvidenceRecord(
            organization_id=org.id,
            condition_id=condition.id,
            condition_revision=1,
            decision_event_id=graph.decision_event.id,
            review_cycle_id=graph.cycle.id,
            review_item_id=graph.review.id,
            subject_type="adr",
            subject_id=graph.adr.id,
            adr_id=graph.adr.id,
            subject_evidence_snapshot_id=graph.snapshot.id,
            value_json={"verified": True},
            canonical_document=document,
            content_hash=hashlib.sha256(document.encode("utf-8")).hexdigest(),
            source_identity="cmdb:read-model-test",
            source_type="cmdb",
            source_version="1",
            source_checksum="a" * 64,
            observed_at=now,
            collected_at=now,
            freshness_status="fresh",
            freshness_rule_version="arb-condition-v1",
            created_by_id=evidence_submitted_by.id,
            command_receipt_id=receipt.id,
            command_generation=1,
        )
        db_session.add(record)
        db_session.flush()
        condition.status = "evidence_submitted"
        condition.revision = 2
        condition.submitted_evidence_id = record.id
        condition.evidence_submitted_by_id = evidence_submitted_by.id
        condition.evidence_submitted_at = now
        db_session.flush()
        graph.evidence_record = record

    _sql(db_session, "SET LOCAL session_replication_role = origin")
    graph.conditions = [condition]
    return condition


def _actor(user, org):
    from app.modules.transformation_room.domain import ActorContext

    return ActorContext(user.id, org.id, frozenset(), f"req-{uuid.uuid4().hex[:8]}")


# ---------------------------------------------------------------------------
# tenancy
# ---------------------------------------------------------------------------


def test_cross_tenant_review_id_is_not_found_and_leaks_no_title(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    owner_org = make_org("arb-read-owner")
    other_org = make_org("arb-read-other")
    submitter = _user(db_session, owner_org, "submitter")
    with tenant_ctx(owner_org.id):
        graph = _build_adr_cycle(db_session, owner_org, submitter)
        secret_title = graph.review.title
        owner_review_id = graph.review.id

    intruder = _user(db_session, other_org, "intruder", admin=True)
    db_session.expunge_all()
    with tenant_ctx(other_org.id):
        view = typed_arb_review_view(
            actor=_actor(intruder, other_org), review_item_id=owner_review_id
        )

    assert view["state"] == "failed"
    assert view["reason"] == "arb_review_not_found"
    assert secret_title not in repr(view)
    assert view["subject"]["title"] is None
    assert view["allowed_actions"]["can_decide"] is False
    assert view["command_keys"] == {}


def test_queue_excludes_other_tenants(db_session, make_org, tenant_ctx):
    from app.modules.transformation_room.arb_read_models import typed_arb_queue_view

    owner_org = make_org("arb-queue-owner")
    other_org = make_org("arb-queue-other")
    submitter = _user(db_session, owner_org, "submitter")
    with tenant_ctx(owner_org.id):
        graph = _build_adr_cycle(db_session, owner_org, submitter)
        owner_view = typed_arb_queue_view(actor=_actor(submitter, owner_org))
    assert owner_view["state"] == "available"
    numbers = [item["review_number"] for item in owner_view["items"]]
    assert graph.cycle.review_number in numbers
    item = next(
        entry
        for entry in owner_view["items"]
        if entry["review_number"] == graph.cycle.review_number
    )
    assert item["subject_type"] == "adr"
    assert item["subject_title"] == graph.adr.title
    assert item["canonical_url"] == f"/architecture/decisions/{graph.adr.id}"
    assert item["required_action_label"] == "Record a decision"
    assert item["is_historical_unverified"] is False
    assert item["submitter_display"] == submitter.email

    outsider = _user(db_session, other_org, "outsider", admin=True)
    db_session.expunge_all()
    with tenant_ctx(other_org.id):
        foreign = typed_arb_queue_view(actor=_actor(outsider, other_org))
    assert graph.cycle.review_number not in [
        entry["review_number"] for entry in foreign["items"]
    ]


# ---------------------------------------------------------------------------
# no invented data
# ---------------------------------------------------------------------------


def test_failed_queue_read_yields_null_pagination_not_zero(
    db_session, make_org, tenant_ctx, monkeypatch
):
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.transformation_room import arb_read_models

    org = make_org("arb-queue-failed")
    user = _user(db_session, org, "reader")

    def _boom(*args, **kwargs):
        raise SQLAlchemyError("queue read failed")

    monkeypatch.setattr(arb_read_models.TypedARBReadModel, "_queue_view", _boom)
    with tenant_ctx(org.id):
        view = arb_read_models.typed_arb_queue_view(actor=_actor(user, org))

    assert view["state"] == "failed"
    assert view["reason"] == "arb_queue_unavailable"
    for field in ("page", "page_size", "total_items", "total_pages"):
        assert view[field] is None, f"{field} must be None on failure, never 0"
    assert view["items"] == []
    assert view["filter_options"]["subject_type"]


def test_empty_queue_is_empty_state_with_real_zero_total(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_queue_view

    org = make_org("arb-queue-empty")
    user = _user(db_session, org, "reader")
    with tenant_ctx(org.id):
        view = typed_arb_queue_view(actor=_actor(user, org))
    assert view["state"] == "empty"
    assert view["total_items"] == 0
    assert view["total_pages"] == 0
    assert view["items"] == []


def test_absent_condition_projection_is_none_not_zero(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-projection-null")
    submitter = _user(db_session, org, "submitter")
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter)
        view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=graph.review.id
        )
    assert view["state"] == "available"
    assert view["decision"]["event"] is None
    projection = view["decision"]["projection"]
    assert projection["condition_count"] == 0
    assert projection["terminal_outcome"] is None
    assert projection["closed_at"] is None


# ---------------------------------------------------------------------------
# evidence integrity
# ---------------------------------------------------------------------------


def test_hash_mismatch_yields_integrity_state_with_no_mutations(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-hash")
    submitter = _user(db_session, org, "submitter")
    decider = _user(db_session, org, "decider", admin=True)
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter, corrupt_hash=True)
        view = typed_arb_review_view(
            actor=_actor(decider, org), review_item_id=graph.review.id
        )

    assert view["state"] == "failed"
    assert view["reason"] == "arb_evidence_integrity_failed"
    assert view["evidence"]["hash_state"] == "mismatch"
    assert view["evidence"]["sections"] == []
    assert view["decision"] is None
    assert view["allowed_actions"]["can_decide"] is False
    assert view["allowed_actions"]["decision_outcomes"] == []
    assert view["command_keys"] == {}
    # Identity survives so the page can name what failed, without a dossier.
    assert view["identity"]["review_item_id"] == graph.review.id


def test_verified_evidence_exposes_named_immutable_sections(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-evidence-ok")
    submitter = _user(db_session, org, "submitter")
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter)
        view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=graph.review.id
        )
    evidence = view["evidence"]
    assert evidence["hash_state"] == "verified"
    assert evidence["evidence_type"] == "arb_subject_evidence_snapshot"
    assert evidence["evidence_id"] == graph.snapshot.id
    assert evidence["policy_version"] == "adr-arb-r2"
    sections = {section["key"]: section["value"] for section in evidence["sections"]}
    assert sections["decision"] == graph.snapshot.payload["decision"]
    # A section the snapshot does not carry stays None, never a live-subject value.
    assert sections["pending_obligations"] is None


# ---------------------------------------------------------------------------
# server-derived authority
# ---------------------------------------------------------------------------


def test_submitter_cannot_decide_their_own_review(db_session, make_org, tenant_ctx):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-sod-decide")
    submitter = _user(db_session, org, "submitter", admin=True)
    other = _user(db_session, org, "decider", admin=True)
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter)
        own = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=graph.review.id
        )
        separate = typed_arb_review_view(
            actor=_actor(other, org), review_item_id=graph.review.id
        )

    assert own["allowed_actions"]["can_decide"] is False
    assert "separate authorised decision maker" in (
        own["allowed_actions"]["decision_denial_reason"] or ""
    )
    assert own["command_keys"] == {}

    assert separate["allowed_actions"]["can_decide"] is True
    assert separate["allowed_actions"]["decision_outcomes"] == [
        "approved",
        "approved_with_conditions",
        "returned_for_evidence",
        "rejected",
    ]
    assert separate["command_keys"]["decision"]


def test_evidence_submitter_cannot_verify_their_own_evidence(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-sod-verify")
    submitter = _user(db_session, org, "submitter")
    decider = _user(db_session, org, "decider", admin=True)
    other_authority = _user(db_session, org, "second-authority", admin=True)
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter)
        _decide(
            db_session,
            org,
            graph,
            decider,
            outcome="approved_with_conditions",
            projected_status="approved_with_conditions",
        )
        condition = _condition(
            db_session,
            org,
            graph,
            status="evidence_submitted",
            evidence_submitted_by=decider,
        )
        self_view = typed_arb_review_view(
            actor=_actor(decider, org), review_item_id=graph.review.id
        )
        peer_view = typed_arb_review_view(
            actor=_actor(other_authority, org), review_item_id=graph.review.id
        )

    own_actions = self_view["allowed_actions"]["conditions"][condition.id]
    assert own_actions["can_verify"] is False
    assert "separate authorised person" in (own_actions["verify_denial_reason"] or "")
    assert f"condition:{condition.id}:verify" not in self_view["command_keys"]

    peer_actions = peer_view["allowed_actions"]["conditions"][condition.id]
    assert peer_actions["can_verify"] is True
    assert peer_view["command_keys"][f"condition:{condition.id}:verify"]


def test_review_submitter_cannot_verify_condition_evidence(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-sod-verify-submitter")
    submitter = _user(db_session, org, "submitter", admin=True)
    decider = _user(db_session, org, "decider", admin=True)
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter)
        _decide(
            db_session,
            org,
            graph,
            decider,
            outcome="approved_with_conditions",
            projected_status="approved_with_conditions",
        )
        condition = _condition(
            db_session,
            org,
            graph,
            status="evidence_submitted",
            evidence_submitted_by=decider,
        )
        view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=graph.review.id
        )
    actions = view["allowed_actions"]["conditions"][condition.id]
    assert actions["can_verify"] is False


# ---------------------------------------------------------------------------
# decision event vs projection, historical state
# ---------------------------------------------------------------------------


def test_decision_event_label_survives_a_projection_change(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-projection")
    submitter = _user(db_session, org, "submitter")
    decider = _user(db_session, org, "decider", admin=True)
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter)
        _decide(
            db_session,
            org,
            graph,
            decider,
            outcome="approved_with_conditions",
            projected_status="approved",
        )
        view = typed_arb_review_view(
            actor=_actor(decider, org), review_item_id=graph.review.id
        )

    assert view["decision"]["event"]["outcome"] == "approved_with_conditions"
    assert view["decision"]["projection"]["status"] == "approved"
    assert view["decision"]["projection"]["terminal_outcome"] == "approved_with_conditions"
    # A decided cycle offers no further decision.
    assert view["allowed_actions"]["can_decide"] is False
    assert "closed" in (view["allowed_actions"]["decision_denial_reason"] or "")


def test_historical_unverified_is_locked_and_offers_no_mutation(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import (
        typed_arb_queue_view,
        typed_arb_review_view,
    )

    org = make_org("arb-historical")
    submitter = _user(db_session, org, "submitter", admin=True)
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter, historical=True)
        view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=graph.review.id
        )
        queue = typed_arb_queue_view(
            actor=_actor(submitter, org), filters={"state": "historical"}
        )

    assert view["state"] == "historical_unverified"
    assert view["reason"] == "No provable immutable evidence."
    assert view["evidence"]["hash_state"] == "unavailable"
    assert view["evidence"]["sections"] == []
    assert view["decision"]["event"] is None
    assert view["decision"]["recorded_historical_outcome"] == "historical_unverified"
    assert view["allowed_actions"] == {
        "can_decide": False,
        "decision_denial_reason": None,
        "decision_outcomes": [],
        "conditions": {},
    }
    assert view["command_keys"] == {}

    item = next(
        entry
        for entry in queue["items"]
        if entry["review_number"] == graph.cycle.review_number
    )
    assert item["is_historical_unverified"] is True
    assert item["required_action_anchor"] is None


def test_legacy_generic_review_is_labelled_not_typed(db_session, make_org, tenant_ctx):
    from app.models.architecture_review_board import ARBReviewItem
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-legacy")
    submitter = _user(db_session, org, "submitter", admin=True)
    suffix = uuid.uuid4().hex[:10]
    with tenant_ctx(org.id):
        _sql(db_session, "SET LOCAL session_replication_role = replica")
        review = ARBReviewItem(
            organization_id=org.id,
            review_number=f"LEGACY-{suffix}",
            title=f"Legacy generic {suffix}",
            review_type="architecture_change",
            status="submitted",
            submitter_id=submitter.id,
        )
        db_session.add(review)
        db_session.flush()
        _sql(db_session, "SET LOCAL session_replication_role = origin")
        view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=review.id
        )
    assert view["state"] == "legacy_generic"
    assert view["evidence"] is None
    assert view["command_keys"] == {}
    assert view["allowed_actions"]["can_decide"] is False


def test_command_keys_are_fresh_per_get(db_session, make_org, tenant_ctx):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-command-keys")
    submitter = _user(db_session, org, "submitter")
    decider = _user(db_session, org, "decider", admin=True)
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter)
        first = typed_arb_review_view(
            actor=_actor(decider, org), review_item_id=graph.review.id
        )
        second = typed_arb_review_view(
            actor=_actor(decider, org), review_item_id=graph.review.id
        )
    assert first["command_keys"]["decision"] != second["command_keys"]["decision"]


# ---------------------------------------------------------------------------
# predecessor / successor addressing
# ---------------------------------------------------------------------------


def test_historical_cycle_exposes_resolvable_successor_review_item(
    db_session, make_org, tenant_ctx
):
    """The successor link must address /arb/reviews/<review_item_id>."""
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-successor")
    submitter = _user(db_session, org, "submitter", admin=True)
    with tenant_ctx(org.id):
        historical = _build_adr_cycle(db_session, org, submitter, historical=True)
        successor = _build_adr_cycle(
            db_session,
            org,
            submitter,
            adr=historical.adr,
            cycle_number=2,
            predecessor_cycle_id=historical.cycle.id,
        )
        historical_view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=historical.review.id
        )
        successor_view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=successor.review.id
        )

    assert historical_view["state"] == "historical_unverified"
    identity = historical_view["identity"]
    assert identity["successor_cycle_id"] == successor.cycle.id
    assert identity["successor_review_item_id"] == successor.review.id
    assert identity["predecessor_cycle_id"] is None
    assert identity["predecessor_review_item_id"] is None

    successor_identity = successor_view["identity"]
    assert successor_identity["predecessor_cycle_id"] == historical.cycle.id
    assert successor_identity["predecessor_review_item_id"] == historical.review.id
    assert successor_identity["successor_review_item_id"] is None


def test_cross_tenant_successor_resolves_to_none_and_leaks_nothing(
    db_session, make_org, tenant_ctx
):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-successor-owner")
    other_org = make_org("arb-successor-other")
    submitter = _user(db_session, org, "submitter", admin=True)
    outsider = _user(db_session, other_org, "outsider", admin=True)
    with tenant_ctx(org.id):
        historical = _build_adr_cycle(db_session, org, submitter, historical=True)
    with tenant_ctx(other_org.id):
        # A foreign cycle claiming our cycle as its predecessor must not become
        # a successor link for this tenant.
        foreign = _build_adr_cycle(
            db_session,
            other_org,
            outsider,
            cycle_number=2,
            predecessor_cycle_id=historical.cycle.id,
        )
    with tenant_ctx(org.id):
        view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=historical.review.id
        )

    identity = view["identity"]
    assert identity["successor_cycle_id"] is None
    assert identity["successor_review_item_id"] is None
    assert foreign.review.title not in repr(view)
    assert str(foreign.review.id) not in str(identity.values())


def test_absent_successor_is_none_not_invented(db_session, make_org, tenant_ctx):
    from app.modules.transformation_room.arb_read_models import typed_arb_review_view

    org = make_org("arb-no-successor")
    submitter = _user(db_session, org, "submitter", admin=True)
    with tenant_ctx(org.id):
        graph = _build_adr_cycle(db_session, org, submitter)
        view = typed_arb_review_view(
            actor=_actor(submitter, org), review_item_id=graph.review.id
        )
    identity = view["identity"]
    assert identity["successor_cycle_id"] is None
    assert identity["successor_review_item_id"] is None
    assert identity["predecessor_review_item_id"] is None
