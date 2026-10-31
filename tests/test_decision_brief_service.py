"""Immutable Transformation Room decision-brief contracts."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.transformation_decision import (
    DecisionBrief,
    DecisionBriefEvidenceCitation,
    DecisionBriefOptionCitation,
    DecisionBriefVersion,
    DecisionEvent,
    TransformationOption,
)
from app.models.transformation_evidence import (
    EvidenceClaimHead,
    EvidenceRecord,
    EvidenceRequest,
)
from app.models.transformation_execution import (
    CommandIdempotencyRecord,
    CommandMaterialisation,
    OperationResult,
)
from app.models.user import User
from app.models.transformation_db_guards import ensure_transformation_db_guards
from app.modules.transformation_room.decision_service import (
    DecisionBriefService,
    TransformationOptionService,
)
from app.modules.transformation_room.command_service import CommandService
from app.modules.transformation_room.domain import (
    ActorContext,
    BlockedByEvidence,
    CommandConflict,
    HumanAssertions,
    NotAuthorised,
    NotFound,
    TypedEvidenceValue,
)
from app.modules.transformation_room.evidence_service import TransformationEvidenceService

from tests.test_transformation_evidence_service import (
    EvidenceScope,
    _record_named_source,
    _record_inventory,
    evidence_scope,
)
from tests.test_transformation_option_service import (
    DecisionScope,
    _option_values,
    decision_scope,
)


@pytest.fixture(scope="module", autouse=True)
def decision_guard_schema(app, _schema):
    """Install the current guard contract on a long-lived shared test database."""
    from app.commands.reconcile_schema import _reconcile

    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []
    with app.app_context(), db.engine.begin() as connection:
        ensure_transformation_db_guards(connection)


def _freeze_options(scope: DecisionScope):
    return tuple(
        TransformationOptionService.freeze_version(
            actor=scope.actor,
            option_id=option_id,
            expected_revision=1,
            command_key=f"brief-option-{ordinal}",
        ).object_ids["option_version_id"]
        for ordinal, option_id in enumerate(scope.option_ids, start=1)
    )


def _assertions(scope: DecisionScope, *, superseded=()):
    return HumanAssertions(
        reviewed_ai_material=True,
        acknowledged_unknown_codes=("cost_source_unknown",),
        acknowledged_superseded_evidence_ids=tuple(superseded),
        rationale="A human reviewed the cited facts, unknowns and recommendation.",
    )


def _freeze_brief(
    scope: DecisionScope,
    option_version_ids,
    *,
    evidence_ids=None,
    assertions=None,
    key="brief-freeze",
    revision=1,
    actor=None,
    brief_id=None,
):
    return DecisionBriefService.freeze(
        actor=actor or scope.actor,
        brief_id=brief_id or scope.brief_id,
        option_version_ids=option_version_ids,
        evidence_ids=evidence_ids or (scope.evidence_id,),
        assertions=assertions or _assertions(scope),
        expected_revision=revision,
        command_key=key,
    )


def _remove_fixture_brief(scope: DecisionScope) -> None:
    """Expose the fixture's real governed scope to draft-creation tests."""
    with db.engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM decision_briefs "
                "WHERE id = :brief_id AND organization_id = :organization_id"
            ),
            {"brief_id": scope.brief_id, "organization_id": scope.organization_id},
        )


def _create_brief(scope: DecisionScope, *, actor=None, key="brief-create"):
    return DecisionBriefService.create_brief(
        actor=actor or scope.actor,
        workstream_id=scope.workstream_id,
        candidate_id=scope.candidate_id,
        title="Application rationalisation decision",
        recommendation_option_id=scope.option_ids[1],
        decision_authority_id=scope.actor_id,
        unknown_codes=("cost_source_unknown",),
        conflicts=("Operational cutover window requires confirmation",),
        expected_impacts=("Lower run cost after controlled migration",),
        command_key=key,
    )


def test_create_brief_is_governed_and_replays_one_draft(decision_scope):
    scope = decision_scope
    _remove_fixture_brief(scope)

    created = _create_brief(scope)
    replay = _create_brief(scope)

    with Session(db.engine) as session:
        brief = session.get(DecisionBrief, created.object_ids["decision_brief_id"])
        count = session.scalar(
            select(func.count()).select_from(DecisionBrief).where(
                DecisionBrief.organization_id == scope.organization_id,
                DecisionBrief.workstream_id == scope.workstream_id,
                DecisionBrief.candidate_id == scope.candidate_id,
            )
        )
    assert created.created is True and created.idempotent is False
    assert replay.created is False and replay.idempotent is True
    assert replay.operation_result_id == created.operation_result_id
    assert count == 1
    assert brief.status == "draft" and brief.revision == 1
    assert brief.recommendation_option_id == scope.option_ids[1]


def test_create_brief_rejects_cross_tenant_and_current_non_authority(
    app, decision_scope, evidence_scope
):
    scope = decision_scope
    _remove_fixture_brief(scope)

    with pytest.raises(NotFound):
        _create_brief(scope, actor=evidence_scope.foreign_actor, key="cross-tenant")

    with app.app_context():
        user = User(
            email=f"brief-reader-{uuid.uuid4().hex}@example.test",
            organization_id=scope.organization_id,
            confirmed=True,
            enterprise_role="portfolio_manager",
        )
        db.session.add(user)
        db.session.flush()
        reader = ActorContext(
            user.id,
            scope.organization_id,
            frozenset({"chief_architect"}),
            f"brief-reader-{uuid.uuid4().hex}",
        )
        db.session.commit()
        db.session.remove()

    with pytest.raises(NotAuthorised):
        _create_brief(scope, actor=reader, key="non-authority")

    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count()).select_from(DecisionBrief).where(
                DecisionBrief.organization_id == scope.organization_id,
                DecisionBrief.workstream_id == scope.workstream_id,
                DecisionBrief.candidate_id == scope.candidate_id,
            )
        ) == 0


def test_create_brief_concurrency_converges_on_one_draft(app, decision_scope):
    scope = decision_scope
    _remove_fixture_brief(scope)
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def create(key):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                results.append(_create_brief(scope, key=key))
            except Exception as error:  # noqa: BLE001 - capture racing outcome
                errors.append((key, error))

    threads = [
        threading.Thread(target=create, args=(f"brief-race-{index}",), daemon=True)
        for index in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()

    # A contender whose transaction met the unique natural-result boundary
    # must reconcile through its original idempotency key after the winner.
    for key, _error in errors:
        results.append(_create_brief(scope, key=key))

    second_replays = tuple(
        _create_brief(scope, key=f"brief-race-{index}") for index in (1, 2)
    )
    natural_key = (
        f"brief:workstream:{scope.workstream_id}:candidate:{scope.candidate_id}"
    )

    with Session(db.engine) as session:
        brief_ids = tuple(
            session.scalars(
                select(DecisionBrief.id).where(
                    DecisionBrief.organization_id == scope.organization_id,
                    DecisionBrief.workstream_id == scope.workstream_id,
                    DecisionBrief.candidate_id == scope.candidate_id,
                )
            ).all()
        )
        operation_result = session.get(
            OperationResult, results[0].operation_result_id
        )
        materialisation = session.scalar(
            select(CommandMaterialisation).where(
                CommandMaterialisation.organization_id == scope.organization_id,
                CommandMaterialisation.operation == "brief.create",
                CommandMaterialisation.natural_key == natural_key,
            )
        )
        receipts = session.scalars(
            select(CommandIdempotencyRecord).where(
                CommandIdempotencyRecord.organization_id
                == scope.organization_id,
                CommandIdempotencyRecord.operation == "brief.create",
                CommandIdempotencyRecord.natural_key == natural_key,
            )
        ).all()
    assert len(results) == 2
    assert len(brief_ids) == 1
    assert {
        result.object_ids["decision_brief_id"]
        for result in (*results, *second_replays)
    } == {
        brief_ids[0]
    }
    assert all(
        replay.created is False and replay.idempotent is True
        for replay in second_replays
    )
    assert len(receipts) == 2
    assert all(receipt.status == "succeeded" for receipt in receipts)
    assert {receipt.operation_result_id for receipt in receipts} == {
        operation_result.id
    }
    assert operation_result.receipt_id == materialisation.receipt_id
    assert (
        operation_result.receipt_generation
        == materialisation.receipt_generation
        == 1
    )


def test_create_brief_definer_rejects_unsigned_execution_claim(
    monkeypatch, decision_scope
):
    scope = decision_scope
    _remove_fixture_brief(scope)
    monkeypatch.setattr(
        CommandService,
        "_execution_capability",
        classmethod(lambda cls, **_kwargs: ("{}", "0" * 64)),
    )

    with pytest.raises(DBAPIError, match="command capability is invalid"):
        _create_brief(scope, key="brief-create-unsigned-capability")


def test_create_brief_definer_rechecks_role_after_claim(
    monkeypatch, decision_scope
):
    """Catches receipt-time authority being trusted at the locked create boundary."""
    scope = decision_scope
    _remove_fixture_brief(scope)
    execute_claim = CommandService._execute_claim.__func__
    revoked = False

    def revoke_then_execute(service, **kwargs):
        nonlocal revoked
        if not revoked:
            with Session(db.engine) as session, session.begin():
                user = session.get(User, scope.actor_id)
                user.enterprise_role = "portfolio_manager"
            revoked = True
        return execute_claim(service, **kwargs)

    monkeypatch.setattr(
        CommandService,
        "_execute_claim",
        classmethod(revoke_then_execute),
    )
    with pytest.raises(DBAPIError, match="actor is not currently authorized"):
        _create_brief(scope, key="brief-create-role-recheck")

    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count()).select_from(DecisionBrief).where(
                DecisionBrief.organization_id == scope.organization_id,
                DecisionBrief.workstream_id == scope.workstream_id,
                DecisionBrief.candidate_id == scope.candidate_id,
            )
        ) == 0


def test_evaluate_and_freeze_pin_exact_current_graph_and_verify_hash(decision_scope):
    """Catches a brief omitting exact options, evidence, outcomes, measures or assertions."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    readiness = DecisionBriefService.evaluate(actor=scope.actor, brief_id=scope.brief_id)
    assert readiness.ready is True
    assert tuple(readiness.option_version_ids) == option_version_ids
    assert tuple(readiness.evidence_ids) == (scope.evidence_id,)

    result = _freeze_brief(scope, option_version_ids)
    with Session(db.engine) as session:
        version = session.get(
            DecisionBriefVersion,
            result.object_ids["decision_brief_version_id"],
        )
        option_citations = session.scalars(
            select(DecisionBriefOptionCitation).where(
                DecisionBriefOptionCitation.organization_id == scope.organization_id,
                DecisionBriefOptionCitation.brief_version_id == version.id,
            )
        ).all()
        evidence_citations = session.scalars(
            select(DecisionBriefEvidenceCitation).where(
                DecisionBriefEvidenceCitation.organization_id == scope.organization_id,
                DecisionBriefEvidenceCitation.brief_version_id == version.id,
            )
        ).all()
        event_row = session.scalar(
            select(DecisionEvent).where(
                DecisionEvent.organization_id == scope.organization_id,
                DecisionEvent.brief_version_id == version.id,
            )
        )
        brief = session.get(DecisionBrief, scope.brief_id)

        assert DecisionBriefService.verify_hash(version) is True
        assert version.version == 1
        assert version.policy_version == "transformation-r1.1"
        assert version.recommendation_option_version_id == option_version_ids[1]
        assert tuple(version.option_version_ids) == option_version_ids
        assert tuple(version.cited_evidence_ids) == (scope.evidence_id,)
        assert tuple(version.outcome_ids) == (scope.outcome_id,)
        assert tuple(version.measure_ids) == (scope.measure_id,)
        assert version.human_reviewed_ai is True
        assert version.unknowns_acknowledged is True
        assert version.blockers_cleared is True
        assert version.frozen_payload["human_assertions"]["rationale"].startswith(
            "A human reviewed"
        )
        assert version.frozen_payload["objective"]
        assert len(version.content_hash) == 64
        assert brief.status == "frozen" and brief.revision == 2
        assert {row.option_version_id for row in option_citations} == set(option_version_ids)
        assert len(evidence_citations) == 1
        assert evidence_citations[0].evidence_record_id == scope.evidence_id
        assert evidence_citations[0].was_current is True
        assert evidence_citations[0].acknowledged is False
        assert event_row.event_type == "brief.version_frozen"
        assert event_row.command_receipt_id is not None

        version.frozen_payload["objective"] = "Altered after load"
        assert DecisionBriefService.verify_hash(version) is False


def test_brief_hash_uses_stored_python_canonical_utf8_document(decision_scope):
    """Catches SQL re-rendering exponent floats or Unicode keys before hashing."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    exact_value = {
        "évidence": {
            "tiny": 1e-7,
            "huge": 1e20,
            "nested": [{"zèbre": "résumé", "a": None}],
        }
    }
    with db.engine.begin() as connection:
        # Flagged exception: mutates a committed evidence_records
        # row's own value directly to construct an exact float/Unicode payload SQL
        # itself would re-render, bypassing the application layer this test is
        # specifically checking does NOT round-trip through SQL. The append-only
        # guard correctly refuses every UPDATE on this table; the deletion marker
        # only ever admits a DELETE, never an UPDATE, by design.
        # Needs a new, named admission path on a production append-only guard for a
        # mutation this test intentionally performs outside any real write path —
        # out of scope here. Left for the owner of that guard to decide.
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: mutates a committed evidence_records value directly; the append-only guard correctly refuses every UPDATE on this table
        connection.execute(
            text(
                "UPDATE evidence_records SET value_json = CAST(:value AS json) "
                "WHERE id = :evidence_id AND organization_id = :organization_id"
            ),
            {
                "value": json.dumps(exact_value, ensure_ascii=True),
                "evidence_id": scope.evidence_id,
                "organization_id": scope.organization_id,
            },
        )

    result = _freeze_brief(
        scope,
        option_version_ids,
        key="brief-exact-canonical-document",
    )
    with Session(db.engine) as session:
        version = session.get(
            DecisionBriefVersion,
            result.object_ids["decision_brief_version_id"],
        )
        session.expunge(version)

    assert version.canonical_document.encode("utf-8").decode("utf-8") == (
        version.canonical_document
    )
    assert hashlib.sha256(version.canonical_document.encode("utf-8")).hexdigest() == (
        version.content_hash
    )
    assert json.loads(version.canonical_document)["frozen_payload"]["evidence"][0][
        "value"
    ]["évidence"]["tiny"] == 1e-7
    assert DecisionBriefService.verify_hash(version) is True

    parsed = json.loads(version.canonical_document)
    parsed["frozen_payload"]["objective"] = "tampered canonical document"
    version.canonical_document = json.dumps(
        parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert DecisionBriefService.verify_hash(version) is False


def test_brief_freeze_rejects_canonical_text_for_a_different_snapshot(
    monkeypatch, decision_scope
):
    """Catches hashing supplied bytes without binding their parsed payload."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    from app.modules.transformation_room import decision_service as service_module

    canonical_json = service_module._canonical_json

    def mismatched_document(value):
        document = canonical_json(value)
        if isinstance(value, dict) and value.get("schema_version") == (
            "decision-brief-hash-r1.1"
        ):
            parsed = json.loads(document)
            parsed["frozen_payload"]["objective"] = "different snapshot"
            return json.dumps(
                parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
        return document

    monkeypatch.setattr(service_module, "_canonical_json", mismatched_document)
    with pytest.raises(DBAPIError, match="canonical document does not match snapshot"):
        _freeze_brief(
            scope,
            option_version_ids,
            key="brief-mismatched-canonical-document",
        )


def test_brief_hash_binds_sorted_citation_and_snapshot_membership(decision_scope):
    """Catches membership columns changing without invalidating the brief digest."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    result = _freeze_brief(scope, option_version_ids)
    with Session(db.engine) as session:
        version = session.get(
            DecisionBriefVersion,
            result.object_ids["decision_brief_version_id"],
        )
        session.expunge(version)

    assert DecisionBriefService.verify_hash(version)
    version.option_version_ids = [option_version_ids[0]]
    assert not DecisionBriefService.verify_hash(version)
    version.option_version_ids = list(option_version_ids)
    version.cited_evidence_ids = [scope.evidence_id, 2_000_000_000]
    assert not DecisionBriefService.verify_hash(version)


def test_citation_creation_is_fenced_and_survives_a_nested_savepoint(decision_scope):
    """Catches 32-bit subtransaction xmin being compared with an epoch-wide txid."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    statements = []

    def capture_savepoint(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SAVEPOINT"):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture_savepoint)
    try:
        result = _freeze_brief(
            scope,
            option_version_ids,
            key="brief-citations-inside-savepoint",
        )
    finally:
        event.remove(db.engine, "before_cursor_execute", capture_savepoint)

    with Session(db.engine) as session:
        option_count = session.scalar(
            select(func.count())
            .select_from(DecisionBriefOptionCitation)
            .where(
                DecisionBriefOptionCitation.organization_id == scope.organization_id,
                DecisionBriefOptionCitation.brief_version_id
                == result.object_ids["decision_brief_version_id"],
            )
        )
        evidence_count = session.scalar(
            select(func.count())
            .select_from(DecisionBriefEvidenceCitation)
            .where(
                DecisionBriefEvidenceCitation.organization_id == scope.organization_id,
                DecisionBriefEvidenceCitation.brief_version_id
                == result.object_ids["decision_brief_version_id"],
            )
        )
    assert statements
    assert option_count == len(option_version_ids)
    assert evidence_count == 1


def test_brief_freeze_definer_rejects_unsigned_execution_claim(
    monkeypatch, decision_scope
):
    """Catches a forged/reclaimed receipt being sufficient to impersonate an actor."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    monkeypatch.setattr(
        CommandService,
        "_execution_capability",
        classmethod(lambda cls, **_kwargs: ("{}", "0" * 64)),
    )

    with pytest.raises(DBAPIError, match="command capability is invalid"):
        _freeze_brief(
            scope,
            option_version_ids,
            key="brief-unsigned-execution-capability",
        )


def test_workstream_brief_rejects_candidate_scoped_options(decision_scope):
    """Catches a NULL-candidate brief silently mixing candidate decisions."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    with Session(db.engine) as session, session.begin():
        brief = DecisionBrief(
            organization_id=scope.organization_id,
            workstream_id=scope.workstream_id,
            candidate_id=None,
            title="Workstream-wide rationalisation decision",
            recommendation_option_id=scope.option_ids[1],
            decision_authority_id=scope.actor_id,
            unknown_codes=["cost_source_unknown"],
            conflicts=[],
            expected_impacts=["Govern the workstream portfolio"],
            status="draft",
            revision=1,
        )
        session.add(brief)
        session.flush()
        brief_id = brief.id

    with pytest.raises(CommandConflict, match="option_version_scope_mismatch"):
        _freeze_brief(
            scope,
            option_version_ids,
            brief_id=brief_id,
            key="reject-candidate-options-for-workstream-brief",
        )


def test_readiness_uses_only_latest_versions_in_exact_candidate_scope(decision_scope):
    """Catches readiness mixing workstream options or historical versions."""
    scope = decision_scope
    first_versions = _freeze_options(scope)
    with Session(db.engine) as session, session.begin():
        option = session.get(TransformationOption, scope.option_ids[0])
        option.title = "Tolerate with revised controls"
    latest = TransformationOptionService.freeze_version(
        actor=scope.actor,
        option_id=scope.option_ids[0],
        expected_revision=3,
        command_key="latest-candidate-option",
    ).object_ids["option_version_id"]
    with Session(db.engine) as session, session.begin():
        workstream_option = TransformationOption(
            organization_id=scope.organization_id,
            workstream_id=scope.workstream_id,
            candidate_id=None,
            title="Portfolio sequencing",
            action_type="sequence",
            description="Workstream-only sequencing alternative",
            assumptions=["Portfolio funding remains available"],
            dependencies=["Candidate decisions are complete"],
            impacts=["Workstream sequencing changes"],
            risks=["Schedule contention"],
            reversibility="Reversible before mobilisation",
            transition_approach="Sequence candidates in governed waves",
            affected_capability_ids=[
                session.get(TransformationOption, scope.option_ids[0]).affected_capability_ids[0]
            ],
            affected_value_stream_ids=[scope.value_stream_id],
            recommendation_rationale="Avoids portfolio contention",
            cost_min=1,
            cost_max=2,
            benefit_min=3,
            benefit_max=4,
            risk_min=Decimal("0.1"),
            risk_max=Decimal("0.2"),
            currency="GBP",
            technology_required=False,
            revision=1,
        )
        session.add(workstream_option)
        session.flush()
        workstream_option_id = workstream_option.id
    workstream_version = TransformationOptionService.freeze_version(
        actor=scope.actor,
        option_id=workstream_option_id,
        expected_revision=1,
        command_key="workstream-only-option",
    ).object_ids["option_version_id"]
    with db.engine.begin() as connection:
        # Flagged exception: mutates a committed, frozen
        # transformation_option_versions row to simulate silent data corruption.
        # archie_reject_transformation_mutation correctly refuses every UPDATE on
        # this table; the deletion marker only ever admits a DELETE, by design.
        # Needs a new, named admission path on a production append-only guard for
        # a mutation outside any real write path — out of scope here. Left for
        # the owner of that guard to decide.
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: mutates a frozen transformation_option_versions row; the append-only guard correctly refuses every UPDATE on this table
        connection.execute(
            text(
                "UPDATE transformation_option_versions "
                "SET cost_min = cost_min + 1 "
                "WHERE id = :version_id AND organization_id = :organization_id"
            ),
            {
                "version_id": first_versions[0],
                "organization_id": scope.organization_id,
            },
        )

    readiness = DecisionBriefService.evaluate(actor=scope.actor, brief_id=scope.brief_id)

    assert tuple(readiness.option_version_ids) == (latest, first_versions[1])
    assert workstream_version not in readiness.option_version_ids


def test_freeze_rejects_a_stale_option_version_and_an_incomplete_latest_set(
    decision_scope,
):
    """Catches callers pinning history or omitting a latest scoped alternative."""
    scope = decision_scope
    first_versions = _freeze_options(scope)
    with Session(db.engine) as session, session.begin():
        option = session.get(TransformationOption, scope.option_ids[0])
        option.title = "Tolerate with current controls"
    latest = TransformationOptionService.freeze_version(
        actor=scope.actor,
        option_id=scope.option_ids[0],
        expected_revision=3,
        command_key="brief-stale-option-v2",
    ).object_ids["option_version_id"]

    with pytest.raises(CommandConflict, match="option_version_not_latest"):
        _freeze_brief(
            scope,
            first_versions,
            key="reject-stale-option-version",
        )
    with pytest.raises(CommandConflict, match="option_version_set_not_current"):
        _freeze_brief(
            scope,
            (latest,),
            key="reject-omitted-latest-option",
        )


def test_freeze_rejects_scoped_option_root_without_any_frozen_version(
    decision_scope,
):
    """Catches an eligible draft alternative disappearing from the selected set."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    with Session(db.engine) as session, session.begin():
        unversioned = TransformationOption(
            **_option_values(
                scope,
                title="Retain with remediation",
                action_type="retain",
                ordinal=3,
            )
        )
        session.add(unversioned)
        session.flush()
        unversioned_id = unversioned.id

    with pytest.raises(CommandConflict, match="option_version_missing"):
        _freeze_brief(
            scope,
            option_version_ids,
            key=f"reject-unversioned-option-root-{unversioned_id}",
        )


def test_new_option_version_and_brief_freeze_serialize_on_scope_lock(
    app, decision_scope
):
    """Pins a concurrent v2 commit ahead of brief freeze to a stale-version rejection."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    engine = db.engine
    option_has_scope_lock = threading.Event()
    release_option = threading.Event()
    brief_entered = threading.Event()
    option_results = []
    brief_errors = []

    with Session(engine) as session, session.begin():
        option = session.get(TransformationOption, scope.option_ids[0])
        option.title = "Tolerate after concurrent review"

    def pause_option(_conn, _cursor, statement, _params, _context, _many):
        if (
            threading.current_thread().name == "new-option-version"
            and statement.lstrip().upper().startswith("SELECT")
            and "programme_workstreams" in statement.lower()
            and "for update" in statement.lower()
        ):
            option_has_scope_lock.set()
            if not release_option.wait(timeout=10):
                raise TimeoutError("option version pause was not released")

    def freeze_option():
        with app.app_context():
            try:
                option_results.append(
                    TransformationOptionService.freeze_version(
                        actor=replace(scope.actor, request_id="option-v2-race"),
                        option_id=scope.option_ids[0],
                        expected_revision=3,
                        command_key="option-v2-race",
                    )
                )
            finally:
                db.session.remove()

    def freeze_brief():
        with app.app_context():
            brief_entered.set()
            try:
                _freeze_brief(
                    scope,
                    option_version_ids,
                    key="brief-vs-option-race",
                    actor=replace(scope.actor, request_id="brief-vs-option-race"),
                )
            except Exception as error:  # asserted after both workers finish
                brief_errors.append(error)
            finally:
                db.session.remove()

    event.listen(engine, "after_cursor_execute", pause_option)
    option_worker = threading.Thread(
        target=freeze_option, name="new-option-version", daemon=True
    )
    brief_worker = threading.Thread(
        target=freeze_brief, name="brief-after-option-version", daemon=True
    )
    try:
        option_worker.start()
        assert option_has_scope_lock.wait(timeout=10)
        brief_worker.start()
        assert brief_entered.wait(timeout=5)
        time.sleep(0.2)
        assert brief_worker.is_alive() is True
    finally:
        release_option.set()
        option_worker.join(timeout=10)
        brief_worker.join(timeout=10)
        event.remove(engine, "after_cursor_execute", pause_option)

    assert option_worker.is_alive() is False and brief_worker.is_alive() is False
    assert len(option_results) == 1
    assert len(brief_errors) == 1
    assert isinstance(brief_errors[0], CommandConflict)
    assert brief_errors[0].reason == "option_version_not_latest"
    with Session(engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(DecisionBriefVersion)
            .where(DecisionBriefVersion.organization_id == scope.organization_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(DecisionEvent)
            .where(DecisionEvent.organization_id == scope.organization_id)
        ) == 0


def test_freeze_rejects_omitted_current_source_from_server_evidence_universe(
    decision_scope, evidence_scope
):
    """Catches caller citations defining completeness instead of global heads."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    second = _record_named_source(
        evidence_scope,
        candidate_id=scope.candidate_id,
        adapter_key=f"brief-source-{uuid.uuid4().hex[:8]}",
        source_identity=f"external:brief-source:{uuid.uuid4().hex}",
        value="Same governed owner claim",
    )
    second_id = second.object_ids["evidence_record_id"]

    with pytest.raises(BlockedByEvidence, match="evidence_snapshot_incomplete"):
        _freeze_brief(
            scope,
            option_version_ids,
            evidence_ids=(scope.evidence_id,),
            key="omit-current-source",
        )

    result = _freeze_brief(
        scope,
        option_version_ids,
        evidence_ids=(scope.evidence_id, second_id),
        key="complete-current-source-set",
    )
    with Session(db.engine) as session:
        version = session.get(
            DecisionBriefVersion,
            result.object_ids["decision_brief_version_id"],
        )
    assert set(version.cited_evidence_ids) == {scope.evidence_id, second_id}


def test_freeze_rejects_unaccepted_request_and_unresolved_current_conflict(
    decision_scope, evidence_scope
):
    """Catches accepted-state/conflict completeness being inferred from citations."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    with Session(db.engine) as session, session.begin():
        request = session.get(EvidenceRequest, evidence_scope.request_id)
        request.status = "open"
        request.submitted_evidence_id = None
        request.accepted_evidence_id = None
        request.submitted_at = None
        request.accepted_at = None
    TransformationEvidenceService.submit_attestation(
        actor=scope.actor,
        request_id=evidence_scope.request_id,
        value=TypedEvidenceValue("string", "A conflicting owner", None, None),
        expected_head_revision=0,
        command_key="brief-unresolved-conflict",
    )
    evidence_ids = DecisionBriefService.current_evidence_ids(
        DecisionBriefService.load_brief_for_tenant(scope.actor, scope.brief_id),
        actor=scope.actor,
    )

    with pytest.raises(
        BlockedByEvidence,
        match="required_evidence_incomplete|evidence_conflict_unresolved",
    ):
        _freeze_brief(
            scope,
            option_version_ids,
            evidence_ids=evidence_ids,
            key="reject-unaccepted-conflicted-evidence",
        )


@pytest.mark.parametrize("authority_kind", ("foreign", "same_tenant_without_role"))
def test_freeze_rejects_invalid_persisted_decision_authority(
    decision_scope, evidence_scope, authority_kind
):
    """Catches an unvalidated authority ID being copied into the frozen brief."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    if authority_kind == "foreign":
        authority_id = evidence_scope.foreign_actor_id
    else:
        suffix = uuid.uuid4().hex[:10]
        user = User(
            organization_id=scope.organization_id,
            email=f"brief-nonauthority-{suffix}@example.test",
            confirmed=True,
            enterprise_role="application_owner",
        )
        db.session.add(user)
        db.session.commit()
        authority_id = user.id
        db.session.remove()
    with Session(db.engine) as session, session.begin():
        session.get(DecisionBrief, scope.brief_id).decision_authority_id = authority_id

    with pytest.raises(NotAuthorised, match="decision_authority_invalid"):
        _freeze_brief(
            scope,
            option_version_ids,
            key=f"reject-{authority_kind}-decision-authority",
            revision=2,
        )


def test_citation_records_effective_expiry_at_freeze(decision_scope, evidence_scope):
    """Catches a past expiry being frozen under its stale stored 'fresh' label."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    expiring = _record_named_source(
        evidence_scope,
        candidate_id=scope.candidate_id,
        adapter_key=f"brief-expiry-{uuid.uuid4().hex[:8]}",
        source_identity=f"external:brief-expiry:{uuid.uuid4().hex}",
        value="Time-bounded owner evidence",
    )
    expiring_id = expiring.object_ids["evidence_record_id"]
    with db.engine.begin() as connection:
        # Flagged exception: mutates a committed evidence_records
        # row to construct a stale stored 'fresh' label past its own expiry.
        # archie_reject_transformation_mutation correctly refuses every UPDATE on
        # this table; the deletion marker only ever admits a DELETE, by design.
        # Needs a new, named admission path on a production append-only guard for
        # a mutation outside any real write path — out of scope here. Left for
        # the owner of that guard to decide.
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")  # session-replication-role-ok: mutates a committed evidence_records row to a stale stored 'fresh' label; the append-only guard correctly refuses every UPDATE on this table
        connection.execute(
            text(
                "UPDATE evidence_records SET freshness_status = 'fresh', "
                "freshness_expires_at = :expired "
                "WHERE id = :record_id AND organization_id = :organization_id"
            ),
            {
                "expired": datetime.now(timezone.utc) - timedelta(days=1),
                "record_id": expiring_id,
                "organization_id": scope.organization_id,
            },
        )

    result = _freeze_brief(
        scope,
        option_version_ids,
        evidence_ids=(scope.evidence_id, expiring_id),
        assertions=_assertions(scope, superseded=(expiring_id,)),
        key="freeze-effective-expiry",
    )
    with Session(db.engine) as session:
        citation = session.scalar(
            select(DecisionBriefEvidenceCitation).where(
                DecisionBriefEvidenceCitation.organization_id == scope.organization_id,
                DecisionBriefEvidenceCitation.brief_version_id
                == result.object_ids["decision_brief_version_id"],
                DecisionBriefEvidenceCitation.evidence_record_id == expiring_id,
            )
        )
    assert citation.freshness_status == "expired"
    assert citation.acknowledged is True


def test_freeze_rejects_duplicate_ids_client_totals_and_unreviewed_ai(decision_scope):
    """Catches ambiguous citations, client-derived totals or absent human review."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    with pytest.raises(ValueError, match="duplicate option_version_ids"):
        _freeze_brief(
            scope,
            (option_version_ids[0], option_version_ids[0]),
            key="brief-duplicate-options",
        )
    with pytest.raises(ValueError, match="duplicate evidence_ids"):
        _freeze_brief(
            scope,
            option_version_ids,
            evidence_ids=(scope.evidence_id, scope.evidence_id),
            key="brief-duplicate-evidence",
        )
    with pytest.raises(ValueError, match="client totals"):
        DecisionBriefService.build_freeze_request(
            scope.actor,
            scope.brief_id,
            option_version_ids,
            (scope.evidence_id,),
            {
                "reviewed_ai_material": True,
                "acknowledged_unknown_codes": ("cost_source_unknown",),
                "acknowledged_superseded_evidence_ids": (),
                "rationale": "Human rationale",
                "client_totals": {"cost_min": "0"},
            },
            1,
        )
    with pytest.raises(BlockedByEvidence, match="human_ai_review_required"):
        _freeze_brief(
            scope,
            option_version_ids,
            assertions=replace(_assertions(scope), reviewed_ai_material=False),
            key="brief-unreviewed-ai",
        )


def test_single_option_requires_persisted_named_policy_or_legal_exception(decision_scope):
    """Catches a one-option brief with an ephemeral or unnamed exception."""
    scope = decision_scope
    with Session(db.engine) as session, session.begin():
        session.delete(session.get(TransformationOption, scope.option_ids[0]))
    one_version = (
        TransformationOptionService.freeze_version(
            actor=scope.actor,
            option_id=scope.option_ids[1],
            expected_revision=1,
            command_key="single-option-version",
        ).object_ids["option_version_id"],
    )
    with pytest.raises(BlockedByEvidence) as blocked:
        _freeze_brief(scope, one_version, key="single-option-no-exception")
    assert any(
        item.code == "viable_options_required"
        for item in blocked.value.details.get("blockers", ())
    )

    with Session(db.engine) as session, session.begin():
        brief = session.get(DecisionBrief, scope.brief_id)
        brief.option_exception_type = "legal"
        brief.option_exception_name = "Contractual exit restriction"
        brief.option_exception_reason = "The current contract legally prohibits migration."
        brief.option_exception_authority_id = scope.actor_id
        brief.recommendation_option_id = scope.option_ids[1]

    result = _freeze_brief(
        scope, one_version, key="single-option-with-exception", revision=2
    )
    with Session(db.engine) as session:
        version = session.get(
            DecisionBriefVersion,
            result.object_ids["decision_brief_version_id"],
        )
    assert version.frozen_payload["option_exception"] == {
        "authority_id": scope.actor_id,
        "name": "Contractual exit restriction",
        "reason": "The current contract legally prohibits migration.",
        "type": "legal",
    }


def test_superseded_global_head_citation_blocks_without_exact_acknowledgement(
    decision_scope,
):
    """Catches a brief silently citing a stale global evidence leaf."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    with Session(db.engine) as session, session.begin():
        application = session.get(ApplicationComponent, scope.application_id)
        application.application_owner = "Changed canonical owner"
    evidence_scope = EvidenceScope(
        organization_id=scope.organization_id,
        foreign_organization_id=0,
        actor_id=scope.actor_id,
        foreign_actor_id=0,
        workstream_id=scope.workstream_id,
        candidate_id=scope.candidate_id,
        application_id=scope.application_id,
        capability_id=0,
        dependency_id=0,
        request_id=0,
        actor=scope.actor,
        foreign_actor=scope.actor,
    )
    corrected = _record_inventory(
        evidence_scope,
        expected_revision=1,
        key="brief-evidence-correction",
    )
    corrected_id = corrected.object_ids["evidence_record_id"]
    with Session(db.engine) as session, session.begin():
        request = session.scalar(
            select(EvidenceRequest).where(
                EvidenceRequest.organization_id == scope.organization_id,
                EvidenceRequest.candidate_id == scope.candidate_id,
                EvidenceRequest.claim_key == "application_owner",
            )
        )
        request.submitted_evidence_id = corrected_id
        request.accepted_evidence_id = corrected_id

    with pytest.raises(BlockedByEvidence, match="evidence_acknowledgement_required"):
        _freeze_brief(
            scope,
            option_version_ids,
            evidence_ids=(scope.evidence_id, corrected_id),
            key="brief-stale-unacknowledged",
        )

    result = _freeze_brief(
        scope,
        option_version_ids,
        evidence_ids=(scope.evidence_id, corrected_id),
        assertions=_assertions(scope, superseded=(scope.evidence_id,)),
        key="brief-stale-acknowledged",
    )
    with Session(db.engine) as session:
        citation = session.scalar(
            select(DecisionBriefEvidenceCitation).where(
                DecisionBriefEvidenceCitation.organization_id == scope.organization_id,
                DecisionBriefEvidenceCitation.brief_version_id
                == result.object_ids["decision_brief_version_id"],
                DecisionBriefEvidenceCitation.evidence_record_id == scope.evidence_id,
            )
        )
    assert citation.was_current is False and citation.acknowledged is True
    assert citation.current_record_id_at_freeze == corrected_id


def test_replay_reauthorizes_before_returning_persisted_brief_result(decision_scope):
    """Catches Task 3 replay returning a frozen brief after current authority is revoked."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    result = _freeze_brief(scope, option_version_ids, key="brief-replay-authority")
    with Session(db.engine) as session, session.begin():
        user = session.get(User, scope.actor_id)
        user.enterprise_role = "portfolio_manager"

    with pytest.raises(NotAuthorised, match="brief_freeze_not_authorised"):
        _freeze_brief(scope, option_version_ids, key="brief-replay-authority")
    with Session(db.engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(DecisionBriefVersion)
            .where(DecisionBriefVersion.organization_id == scope.organization_id)
        ) == 1
        assert result.operation_result_id is not None


def test_locked_brief_handler_rechecks_role_revoked_after_command_authorization(
    app, decision_scope
):
    """Catches receipt-time authority being reused after a real committed revocation."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    engine = db.engine
    handler_has_brief_lock = threading.Event()
    release_handler = threading.Event()
    errors = []

    def pause_handler(_conn, _cursor, statement, _params, _context, _many):
        if (
            threading.current_thread().name == "brief-authority-worker"
            and statement.lstrip().upper().startswith("SELECT")
            and "decision_briefs" in statement.lower()
            and "for update" in statement.lower()
        ):
            handler_has_brief_lock.set()
            if not release_handler.wait(timeout=10):
                raise TimeoutError("brief authority pause was not released")

    def freeze():
        with app.app_context():
            try:
                _freeze_brief(
                    scope,
                    option_version_ids,
                    key="brief-authority-race",
                    actor=replace(scope.actor, request_id="brief-authority-race"),
                )
            except Exception as error:  # asserted after worker finishes
                errors.append(error)
            finally:
                db.session.remove()

    event.listen(engine, "after_cursor_execute", pause_handler)
    worker = threading.Thread(
        target=freeze,
        name="brief-authority-worker",
        daemon=True,
    )
    try:
        worker.start()
        assert handler_has_brief_lock.wait(timeout=10)
        with Session(engine) as session, session.begin():
            user = session.get(User, scope.actor_id)
            user.enterprise_role = "portfolio_manager"
    finally:
        release_handler.set()
        worker.join(timeout=10)
        event.remove(engine, "after_cursor_execute", pause_handler)

    assert worker.is_alive() is False
    assert len(errors) == 1 and isinstance(errors[0], NotAuthorised)
    assert errors[0].reason == "brief_freeze_not_authorised"
    with Session(engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(DecisionBriefVersion)
            .where(DecisionBriefVersion.organization_id == scope.organization_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(DecisionEvent)
            .where(DecisionEvent.organization_id == scope.organization_id)
        ) == 0


def test_brief_freeze_locks_actor_and_authorities_in_global_user_order(
    decision_scope,
):
    """Catches swapped actor/authority pairs taking singleton user locks."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    with Session(db.engine) as session, session.begin():
        authority = User(
            email=f"ordered-authority-{uuid.uuid4().hex[:10]}@example.test",
            organization_id=scope.organization_id,
            confirmed=True,
            enterprise_role="enterprise_architect",
        )
        authority.role = None
        session.add(authority)
        session.flush()
        brief = session.get(DecisionBrief, scope.brief_id)
        brief.decision_authority_id = authority.id

    locked_user_queries = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        normalized = " ".join(statement.lower().split())
        if (
            normalized.startswith("select")
            and " from users " in f" {normalized} "
            and "for update" in normalized
        ):
            locked_user_queries.append(normalized)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        _freeze_brief(
            scope,
            option_version_ids,
            key="brief-global-principal-lock-order",
            revision=2,
        )
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)

    assert locked_user_queries
    assert "users.id in" in locked_user_queries[0]
    assert "order by users.id" in locked_user_queries[0]


def test_concurrent_same_revision_brief_freeze_serializes_one_snapshot(app, decision_scope):
    """Catches two immutable brief versions escaping one logical draft revision."""
    scope = decision_scope
    option_version_ids = _freeze_options(scope)
    engine = db.engine
    first_has_lock = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    results = []
    errors = []

    def pause_first(_conn, _cursor, statement, _params, _context, _many):
        if (
            threading.current_thread().name == "brief-version-first"
            and statement.lstrip().upper().startswith("SELECT")
            and "decision_briefs" in statement.lower()
            and "for update" in statement.lower()
        ):
            first_has_lock.set()
            if not release_first.wait(timeout=10):
                raise TimeoutError("brief version race pause was not released")

    def freeze(key, entered=None):
        with app.app_context():
            if entered is not None:
                entered.set()
            try:
                results.append(
                    _freeze_brief(
                        scope,
                        option_version_ids,
                        key=key,
                        actor=replace(scope.actor, request_id=key),
                    )
                )
            except Exception as error:  # asserted after workers finish
                errors.append(error)
            finally:
                db.session.remove()

    event.listen(engine, "after_cursor_execute", pause_first)
    first = threading.Thread(
        target=freeze,
        args=("brief-race-first",),
        name="brief-version-first",
        daemon=True,
    )
    second = threading.Thread(
        target=freeze,
        args=("brief-race-second", second_entered),
        name="brief-version-second",
        daemon=True,
    )
    try:
        first.start()
        assert first_has_lock.wait(timeout=10)
        second.start()
        assert second_entered.wait(timeout=5)
        time.sleep(0.2)
        assert second.is_alive() is True
    finally:
        release_first.set()
        first.join(timeout=10)
        second.join(timeout=10)
        event.remove(engine, "after_cursor_execute", pause_first)

    assert first.is_alive() is False and second.is_alive() is False
    # Two interleavings are legitimate and the outcome depends on timing. If the
    # second worker is blocked on the row lock when the first commits, it finds
    # the revision moved and fails with stale_revision. If it only claims after
    # the first result exists, the database claim reconciles its rotated
    # transport key to that result and it is a replay. Either way exactly one
    # worker created the version and nobody else created a second.
    assert len(errors) <= 1
    for error in errors:
        assert isinstance(error, CommandConflict) and error.reason == "stale_revision"
    assert len(results) + len(errors) == 2
    assert sum(1 for result in results if result.created) == 1
    assert {result.object_ids["decision_brief_version_id"] for result in results} != set() and len(
        {result.object_ids["decision_brief_version_id"] for result in results}
    ) == 1
    with Session(engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(DecisionBriefVersion)
            .where(DecisionBriefVersion.organization_id == scope.organization_id)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(DecisionEvent)
            .where(DecisionEvent.organization_id == scope.organization_id)
        ) == 1
        brief = session.get(DecisionBrief, scope.brief_id)
    assert brief.revision == 2
