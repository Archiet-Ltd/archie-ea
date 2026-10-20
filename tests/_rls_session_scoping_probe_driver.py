"""Subprocess driver for tests/test_transformation_room_rls_session_scoping.py.

Boots the application against DATABASE_URL (the restricted runtime role, RLS
fully enabled) and drives every read site this change fixed across
programme_service.py, decision_service.py, evidence_service.py,
execution_service.py, gate_service.py, http.py and read_models.py: each of
these opens its own ``Session(db.engine)`` (or, for ``authorise_draft`` /
``load_application_for_tenant``, a session it is sometimes handed and
sometimes opens itself), independent of Flask-SQLAlchemy's own
``db.session`` -- the only session ``app/middleware/tenant_isolation.py``
wires its row-level-security tenant-context listeners onto. Before the fix,
every one of these would silently see zero rows against a fully RLS-enabled
database: not an error, "not found"/empty, indistinguishable from the row
genuinely not existing.

Seeds every row through ``db.session`` directly (the already-correct,
already-tenant-scoped path: tenant context is set from ``g.current_org_id``
by the existing middleware), with ``g.current_org_id`` set to the probe
organisation, then re-reads each one through the fixed, independently-opened
sessions and reports whether each saw its row.

Runs in its own process for the same reason tests/_runtime_role_driver.py and
tests/_command_claim_probe_driver.py do: the runtime-role connection string
must never leak into the pytest session's own (superuser) engine.

Prints one ``RESULT <json>`` line: one key per fixed call site, each either
``true`` (the row was found -- the fix works) or an error string.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("FLASK_CONFIG", "testing")

from app import create_app, db  # noqa: E402
from app.middleware.tenant_isolation import set_database_tenant_context  # noqa: E402
from app.models.application_portfolio import ApplicationComponent  # noqa: E402
from app.models.implementation_migration import WorkPackage  # noqa: E402
from app.models.transformation_decision import (  # noqa: E402
    DecisionBrief,
    DecisionBriefVersion,
    TransformationOption,
    TransformationOptionVersion,
)
from app.models.transformation_evidence import (  # noqa: E402
    EvidenceClaimHead,
    EvidenceRecord,
    EvidenceRequest,
    TransformationCandidate,
)
from app.models.transformation_execution import DeliveryExportAttempt  # noqa: E402
from app.modules.transformation_room.decision_service import (  # noqa: E402
    DecisionBriefService,
    TransformationOptionService,
    _sha256_canonical,
)
from app.modules.transformation_room.domain import ActorContext, ProgrammeIntake  # noqa: E402
from app.modules.transformation_room.evidence_service import (  # noqa: E402
    load_application_for_tenant,
    TransformationEvidenceService,
)
from app.modules.transformation_room.execution_service import (  # noqa: E402
    TransformationExecutionService,
)
from app.modules.transformation_room.gate_service import (  # noqa: E402
    TransformationGateService,
)
from app.modules.transformation_room.http import resolve_enterprise_roles  # noqa: E402
from app.modules.transformation_room.programme_service import (  # noqa: E402
    TransformationProgrammeService,
)
from app.modules.transformation_room.read_models import (  # noqa: E402
    ChiefArchitectTransformationReadModel,
    TransformationRoomReadModel,
)


def _utcnow():
    return datetime.now(timezone.utc)


def main() -> None:
    app = create_app("testing")
    app.config["PROPAGATE_EXCEPTIONS"] = False
    results: dict[str, object] = {}

    def check(key, fn):
        try:
            value = fn()
            results[key] = value if isinstance(value, (bool, str, int)) else True
        except Exception as exc:  # noqa: BLE001 -- report every failure shape
            results[key] = f"{type(exc).__name__}: {exc}"

    with app.app_context():
        from app.modules.account.services.account_service import AccountService

        suffix = uuid.uuid4().hex[:10]
        with app.test_request_context("/"):
            owner = AccountService.register_user(
                "Rls", "Owner", f"rls-owner-{suffix}@example.com",
                "Passw0rd!long1", confirmed=True,
            )
            db.session.commit()
            member = AccountService.register_user(
                "Rls", "Member", f"rls-member-{suffix}@example.com",
                "Passw0rd!long1", confirmed=True,
            )
            db.session.commit()

        org_id = owner.organization_id
        # Move the second user into the owner's organisation: this probe only
        # needs one tenant with two users, not two tenants.
        from flask import g

        g.current_org_id = org_id
        member.organization_id = org_id
        db.session.commit()

        owner_actor = ActorContext(
            user_id=owner.id, organization_id=org_id, roles=frozenset(), request_id=str(uuid.uuid4())
        )

        # --- programme_service.py: create_programme (already-fixed CommandService
        # path) then the independent-session reads this change fixes. ---
        programme_result = TransformationProgrammeService.create_programme(
            actor=owner_actor,
            command_key=f"rls-probe-programme-{suffix}",
            request=ProgrammeIntake(
                name="RLS Probe Programme",
                objective="Prove independent sessions see tenant rows",
                owner_id=owner.id,
                target_date=None,
                target_date_unavailable_reason="not scheduled for this probe",
                workstream_type="technology",
                scope_expression={"scope": "probe"},
                outcome={
                    "statement": "Prove the fix",
                    "owner_id": owner.id,
                    "direction": "increase",
                    "measure": {
                        "metric_name": "probe_metric",
                        "unit": "count",
                        "aggregation": "sum",
                        "baseline_value": "0",
                        "target_value": "1",
                    },
                },
            ),
        )
        programme_id = programme_result.object_ids["programme_id"]
        workstream_id = programme_result.object_ids["workstream_id"]

        # list_programmes (programme_service.py)
        check(
            "programme_service.list_programmes",
            lambda: programme_id
            in {
                view.programme_id
                for view in TransformationProgrammeService.list_programmes(actor=owner_actor)
            },
        )

        # get_programme -> load_programme_for_tenant + load_workstreams_for_tenant
        # + authorise_read (programme_service.py), and transitively
        # gate_service.load_policy_snapshot via next_action.
        def _get_programme_ok():
            view = TransformationProgrammeService.get_programme(
                actor=owner_actor, programme_id=programme_id
            )
            return view.programme_id == programme_id and workstream_id in view.workstream_ids

        check("programme_service.get_programme", _get_programme_ok)

        # assign_role -> load_assignment_scope + authorise_role_assignment
        # (programme_service.py)
        TransformationProgrammeService.assign_role(
            actor=owner_actor,
            programme_id=programme_id,
            workstream_id=None,
            user_id=member.id,
            role="workstream_lead",
            effective_from=date.today(),
            effective_to=None,
            expected_revision=1,
            command_key=f"rls-probe-assign-{suffix}",
        )

        # gate_service.py: load_policy_snapshot, called directly.
        check(
            "gate_service.load_policy_snapshot",
            lambda: TransformationGateService.load_policy_snapshot(
                actor=owner_actor, workstream_id=workstream_id
            ).workstream.id
            == workstream_id,
        )

        # http.py: resolve_enterprise_roles must see the ProgrammeRoleAssignment
        # just granted to `member` -- the row it silently could not see before
        # the fix.
        check(
            "http.resolve_enterprise_roles",
            lambda: "workstream_lead"
            in resolve_enterprise_roles(
                member, org_id, programme_id=programme_id, workstream_id=None
            ),
        )

        # read_models.py: TransformationRoomReadModel.room / .stage
        check(
            "read_models.room",
            lambda: TransformationRoomReadModel.room(
                actor=owner_actor, programme_id=programme_id, workstream_id=workstream_id
            )["programme"]["id"]
            == programme_id,
        )
        check(
            "read_models.stage",
            lambda: TransformationRoomReadModel.stage(
                actor=owner_actor,
                programme_id=programme_id,
                workstream_id=workstream_id,
                stage="objective",
            )
            is not None,
        )

        # read_models.py: ChiefArchitectTransformationReadModel.load_programmes
        check(
            "read_models.load_programmes",
            lambda: programme_id
            in {
                view.programme_id
                for view in ChiefArchitectTransformationReadModel.load_programmes(owner_actor)
            },
        )

        # --- seed one TransformationCandidate + EvidenceRequest + EvidenceRecord
        # + EvidenceClaimHead + ApplicationComponent, through db.session (the
        # already-correct, already-tenant-scoped path) ---
        g.current_org_id = org_id
        application = ApplicationComponent(
            name="RLS Probe Application",
            organization_id=org_id,
            description="Probe fixture for evidence_service.load_application_for_tenant.",
        )
        db.session.add(application)
        db.session.flush()

        candidate = TransformationCandidate(
            organization_id=org_id,
            workstream_id=workstream_id,
            subject_type="application",
            subject_id=application.id,
            inclusion_status="accepted",
            inclusion_reason="probe fixture",
            accepted_by_id=owner.id,
            accepted_at=_utcnow(),
        )
        db.session.add(candidate)
        db.session.flush()

        evidence_request = EvidenceRequest(
            organization_id=org_id,
            workstream_id=workstream_id,
            candidate_id=candidate.id,
            subject_type="application",
            subject_id=application.id,
            claim_key="probe_claim",
            assigned_to_id=owner.id,
            required=True,
            status="open",
            created_by_id=owner.id,
        )
        db.session.add(evidence_request)
        db.session.flush()

        evidence_record = EvidenceRecord(
            organization_id=org_id,
            programme_id=programme_id,
            workstream_id=workstream_id,
            candidate_id=candidate.id,
            subject_type="application",
            subject_id=application.id,
            claim_key="probe_claim",
            value_json={"value": "probe"},
            value_type="json",
            classification="observed",
            source_identity="probe:1",
            source_type="manual",
            source_version="1",
            source_checksum="0" * 64,
            source_system="probe",
            collected_at=_utcnow(),
            observed_at=_utcnow(),
            freshness_status="fresh",
            freshness_rule_version="probe-r1",
            created_by_id=owner.id,
        )
        db.session.add(evidence_record)
        db.session.flush()

        # A claim head can only be created empty (revision 0); advancing it needs
        # a signed command receipt, so this probe cannot point one at a record.
        # active_evidence therefore proves visibility through its candidate and
        # workstream reads (NotFound if hidden), not through the returned records.
        claim_head = EvidenceClaimHead(
            organization_id=org_id,
            subject_type="application",
            subject_id=application.id,
            claim_key="probe_claim",
            source_identity="probe:1",
            current_record_id=None,
            revision=0,
        )
        db.session.add(claim_head)
        db.session.commit()

        # evidence_service.py: load_application_for_tenant (owns_session branch)
        check(
            "evidence_service.load_application_for_tenant",
            lambda: load_application_for_tenant(owner_actor, application.id).id
            == application.id,
        )

        # evidence_service.py: load_assigned_request
        check(
            "evidence_service.load_assigned_request",
            lambda: TransformationEvidenceService.load_assigned_request(
                owner_actor, evidence_request.id
            ).id
            == evidence_request.id,
        )

        # evidence_service.py: active_evidence
        check(
            "evidence_service.active_evidence",
            lambda: isinstance(
                TransformationEvidenceService.active_evidence(
                    actor=owner_actor, subject_type="application", subject_id=application.id
                ),
                tuple,
            ),
        )

        # read_models.py: ChiefArchitectTransformationReadModel.evidence_debt
        def _evidence_debt_ok():
            programmes = ChiefArchitectTransformationReadModel.load_programmes(owner_actor)
            debt = ChiefArchitectTransformationReadModel.evidence_debt(owner_actor, programmes)
            return debt["value"] == 1

        check("read_models.evidence_debt", _evidence_debt_ok)

        # --- seed a TransformationOption + frozen version + decision brief +
        # decision brief version, through db.session ---
        g.current_org_id = org_id
        workstream = TransformationProgrammeService.load_workstreams_for_tenant(
            owner_actor, programme_id
        )[0]
        # Force the workstream directly into the "options" lifecycle stage:
        # this probe is for RLS read visibility, not the gate workflow, so it
        # bypasses TransformationGateService.transition deliberately.
        from app.models.transformation_programme import ProgrammeWorkstream

        persisted_workstream = db.session.get(ProgrammeWorkstream, workstream.id)
        persisted_workstream.lifecycle_stage = "options"
        db.session.commit()

        option = TransformationOption(
            organization_id=org_id,
            workstream_id=workstream_id,
            candidate_id=None,
            title="Probe option",
            action_type="invest",
            description="Probe fixture for decision_service.load_option_for_tenant.",
            assumptions=[],
            dependencies=[],
            impacts=[],
            risks=[],
            reversibility="reversible",
            transition_approach="big_bang",
            affected_capability_ids=[],
            affected_value_stream_ids=[],
            recommendation_rationale="probe",
            cost_min=Decimal("1.00"),
            cost_max=Decimal("2.00"),
            benefit_min=Decimal("1.00"),
            benefit_max=Decimal("2.00"),
            risk_min=Decimal("0.1"),
            risk_max=Decimal("0.2"),
            currency="USD",
            technology_required=False,
            revision=1,
        )
        db.session.add(option)
        db.session.commit()

        # decision_service.py: load_option_for_tenant
        check(
            "decision_service.load_option_for_tenant",
            lambda: TransformationOptionService.load_option_for_tenant(owner_actor, option.id).id
            == option.id,
        )

        # decision_service.py: authorise_draft (owns_session branch)
        def _authorise_draft_ok():
            TransformationOptionService.authorise_draft(
                owner_actor,
                TransformationOptionService.load_option_for_tenant(owner_actor, option.id),
            )
            return True

        check("decision_service.authorise_draft", _authorise_draft_ok)

        captured_at = _utcnow()
        version = TransformationOptionVersion(
            organization_id=org_id,
            option_id=option.id,
            workstream_id=workstream_id,
            candidate_id=None,
            version=1,
            source_revision=1,
            content_json={
                "title": option.title,
                "action_type": option.action_type,
                "description": option.description,
                "assumptions": [],
                "dependencies": [],
                "impacts": [],
                "risks": [],
                "reversibility": option.reversibility,
                "transition_approach": option.transition_approach,
                "affected_capability_ids": [],
                "affected_value_stream_ids": [],
                "recommendation_rationale": option.recommendation_rationale,
                "cost_min": str(option.cost_min),
                "cost_max": str(option.cost_max),
                "benefit_min": str(option.benefit_min),
                "benefit_max": str(option.benefit_max),
                "risk_min": str(option.risk_min),
                "risk_max": str(option.risk_max),
                "currency": option.currency,
                "technology_required": option.technology_required,
            },
            cost_min=option.cost_min,
            cost_max=option.cost_max,
            benefit_min=option.benefit_min,
            benefit_max=option.benefit_max,
            risk_min=option.risk_min,
            risk_max=option.risk_max,
            currency=option.currency,
            technology_required=option.technology_required,
            captured_by_id=owner.id,
            captured_at=captured_at,
            content_hash="0" * 64,
        )
        version.content_hash = _sha256_canonical(
            TransformationOptionService.reconstruct_canonical_version(version)
        )
        db.session.add(version)
        db.session.commit()

        # decision_service.py: load_versions_for_tenant
        check(
            "decision_service.load_versions_for_tenant",
            lambda: {
                row.id
                for row in TransformationOptionService.load_versions_for_tenant(
                    owner_actor, [version.id]
                )
            }
            == {version.id},
        )

        brief = DecisionBrief(
            organization_id=org_id,
            workstream_id=workstream_id,
            candidate_id=None,
            title="Probe brief",
            recommendation_option_id=option.id,
            decision_authority_id=owner.id,
            unknown_codes=[],
            conflicts=[],
            expected_impacts=[],
            status="draft",
            revision=1,
        )
        # The runtime role may not INSERT into decision_briefs / decision_brief_versions
        # (only the signed archie_* functions may), so these two fixtures are written
        # through the schema owner's connection; every read below is still the runtime role.
        owner_engine = create_engine(os.environ["PROBE_OWNER_URL"])
        owner_session = Session(owner_engine)
        owner_session.add(brief)
        owner_session.commit()
        brief_id_value = brief.id

        # decision_service.py: load_brief_for_tenant
        check(
            "decision_service.load_brief_for_tenant",
            lambda: DecisionBriefService.load_brief_for_tenant(owner_actor, brief_id_value).id
            == brief_id_value,
        )

        # decision_service.py: current_option_version_ids
        loaded_brief = DecisionBriefService.load_brief_for_tenant(owner_actor, brief_id_value)
        check(
            "decision_service.current_option_version_ids",
            lambda: version.id
            in DecisionBriefService.current_option_version_ids(loaded_brief, actor=owner_actor),
        )

        # decision_service.py: current_evidence_ids (no active head can be
        # seeded here, so this only proves the call completes under RLS)
        check(
            "decision_service.current_evidence_ids",
            lambda: isinstance(
                DecisionBriefService.current_evidence_ids(loaded_brief, actor=owner_actor),
                tuple,
            ),
        )

        brief_version = DecisionBriefVersion(
            organization_id=org_id,
            brief_id=brief_id_value,
            workstream_id=workstream_id,
            version=1,
            source_revision=1,
            frozen_payload={"probe": True},
            recommendation_option_version_id=version.id,
            option_version_ids=[version.id],
            cited_evidence_ids=[evidence_record.id],
            outcome_ids=[],
            measure_ids=[],
            policy_version="probe-r1",
            created_by_id=owner.id,
            content_hash="1" * 64,
            canonical_document="{}",
            submitted_by_id=owner.id,
            submitter_authorized=True,
            decision_authority_id=owner.id,
            human_reviewed_ai=True,
            blockers_cleared=True,
            unknowns_acknowledged=True,
        )
        owner_session.add(brief_version)
        owner_session.commit()
        brief_version_id_value = brief_version.id

        # decision_service.py: require_latest_frozen_version
        check(
            "decision_service.require_latest_frozen_version",
            lambda: DecisionBriefService.require_latest_frozen_version(loaded_brief).id
            == brief_version_id_value,
        )

        # decision_service.py: require_version_for_tenant
        check(
            "decision_service.require_version_for_tenant",
            lambda: DecisionBriefService.require_version_for_tenant(
                owner_actor, brief_version_id_value
            ).id
            == brief_version_id_value,
        )

        # --- execution_service.py: _await_or_recover_export_attempt's
        # indeterminate -> successor lookup branch ---
        work_package = WorkPackage(
            organization_id=org_id,
            name="Probe work package",
        )
        db.session.add(work_package)
        db.session.flush()

        predecessor = DeliveryExportAttempt(
            organization_id=org_id,
            work_package_id=work_package.id,
            provider_key="probe-provider",
            attempt_key=(suffix + "a" * 64)[:64],
            request_json={"probe": True},
            status="indeterminate",
            completed_at=_utcnow(),
            error_class="ProbeError",
            error_message="probe",
            attempted_by_id=owner.id,
        )
        db.session.add(predecessor)
        db.session.flush()

        successor = DeliveryExportAttempt(
            organization_id=org_id,
            work_package_id=work_package.id,
            provider_key="probe-provider",
            attempt_key=(suffix + "b" * 64)[:64],
            predecessor_attempt_id=predecessor.id,
            request_json={"probe": True},
            status="succeeded",
            external_key="probe-external-key",
            completed_at=_utcnow(),
            response_digest="c" * 64,
            attempted_by_id=owner.id,
        )
        db.session.add(successor)
        db.session.commit()

        check(
            "execution_service.await_or_recover_export_attempt",
            lambda: TransformationExecutionService._await_or_recover_export_attempt(
                actor=owner_actor, attempt_id=predecessor.id
            )[0].id
            == successor.id,
        )

        # Confirm the primitive itself behaves as documented: setting the
        # tenant context on a connection makes RLS admit exactly that
        # organisation's rows and none other -- the regression this whole
        # change guards against.
        with app.test_request_context("/"):
            other = AccountService.register_user(
                "Rls", "Other", f"rls-other-{suffix}@example.com", "Passw0rd!long1",
                confirmed=True,
            )
            db.session.commit()
        other_org_id = other.organization_id

        def _cross_tenant_hidden():
            with db.engine.connect() as connection:
                set_database_tenant_context(connection, other_org_id)
                row = connection.execute(
                    db.text(
                        "SELECT 1 FROM programme_workstreams WHERE id = :id"
                    ),
                    {"id": workstream_id},
                ).first()
                return row is None

        check("cross_tenant_rows_stay_hidden", _cross_tenant_hidden)

        # --- solution_design_routes._engine_archimate_cleanup: runs on
        # db.engine.begin(), so it needs the tenant context set explicitly.
        from app.models.archimate_core import ArchiMateElement, ArchitectureModel
        from app.models.solution_models import Solution
        from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
            _engine_archimate_cleanup,
        )

        g.current_org_id = org_id
        solution = Solution(organization_id=org_id, name="Cleanup probe solution")
        db.session.add(solution)
        db.session.flush()
        arch_model = ArchitectureModel(
            organization_id=org_id, name="Cleanup probe model", solution_id=solution.id
        )
        db.session.add(arch_model)
        db.session.flush()
        element1 = ArchiMateElement(
            organization_id=org_id,
            name="Cleanup probe element 1",
            type="ApplicationComponent",
            layer="Application",
            architecture_id=arch_model.id,
        )
        element2 = ArchiMateElement(
            organization_id=org_id,
            name="Cleanup probe element 2",
            type="ApplicationComponent",
            layer="Application",
            architecture_id=arch_model.id,
        )
        db.session.add_all([element1, element2])
        db.session.flush()
        # Create a relationship between the two elements
        from app.models.archimate_core import ArchiMateRelationship
        relationship = ArchiMateRelationship(
            organization_id=org_id,
            type="Association",
            source_id=element1.id,
            target_id=element2.id,
        )
        db.session.add(relationship)
        db.session.commit()
        element1_id = element1.id
        element2_id = element2.id
        relationship_id = relationship.id
        solution_id_value = solution.id

        def _element_exists():
            with db.engine.connect() as connection:
                set_database_tenant_context(connection, org_id)
                return (
                    connection.execute(
                        db.text("SELECT 1 FROM archimate_elements WHERE id = :id"),
                        {"id": element1_id},
                    ).first()
                    is not None
                )

        def _element2_exists():
            with db.engine.connect() as connection:
                set_database_tenant_context(connection, org_id)
                return (
                    connection.execute(
                        db.text("SELECT 1 FROM archimate_elements WHERE id = :id"),
                        {"id": element2_id},
                    ).first()
                    is not None
                )

        def _relationship_exists():
            with db.engine.connect() as connection:
                set_database_tenant_context(connection, org_id)
                return (
                    connection.execute(
                        db.text("SELECT 1 FROM archimate_relationships WHERE id = :id"),
                        {"id": relationship_id},
                    ).first()
                    is not None
                )

        def _other_org_cannot_clean():
            g.current_org_id = other_org_id
            _engine_archimate_cleanup([solution_id_value])
            return _element_exists()

        def _own_org_cleans():
            g.current_org_id = org_id
            _engine_archimate_cleanup([solution_id_value])
            return not _element_exists() and not _element2_exists() and not _relationship_exists()

        check("archimate_cleanup_other_org_leaves_rows", _other_org_cannot_clean)
        check("archimate_cleanup_deletes_own_org_rows", _own_org_cleans)

    print("RESULT " + json.dumps(results))


if __name__ == "__main__":
    main()
