"""Business-first Transformation Programme aggregate commands."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import db
from app.models.relationship_tables import work_package_events as work_package_events_table
from app.models.strategic import StrategicInitiative
from app.models.transformation_programme import (
    IMPROVEMENT_DIRECTIONS,
    ISO_4217_CURRENCIES,
    MEASURE_AGGREGATIONS,
    PROGRAMME_ROLES,
    WORKSTREAM_TYPES,
    MeasureDefinition,
    ProgrammeOutcomeCommitment,
    ProgrammeRoleAssignment,
    ProgrammeWorkstream,
)
from app.models.user import Permission, User
from app.modules.transformation_room.command_service import CommandService, OperationAuthorizer
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandConflict,
    CommandResult,
    DomainMutationResult,
    NotAuthorised,
    NotFound,
    ProgrammeIntake,
    ProgrammeView,
)


# Programme WRITE authority is persona- or assignment-based only. Admin flags,
# the legacy Role name and the enterprise_role column default ("platform_admin")
# grant no programme write (SRS section 6; security.md S1).
CREATE_ROLES = frozenset({"enterprise_architect", "chief_architect", "cto"})
OBJECTIVE_ROLES = CREATE_ROLES | frozenset({"programme_owner", "workstream_lead"})
ROLE_ASSIGNMENT_ROLES = CREATE_ROLES | frozenset({"programme_owner"})
LINK_ROLES = CREATE_ROLES | frozenset({"programme_owner", "workstream_lead", "contributor"})
# Org governance escape hatch: an org admin may retire a programme whose owner
# has left. This is the only admin-derived write, and it is non-destructive.
ARCHIVE_ROLES = frozenset({"programme_owner", "organization_admin"})
# Admins keep READ within their own organisation (support, governance).
READ_ROLES = CREATE_ROLES | frozenset({"organization_admin"}) | frozenset({
    "portfolio_manager", "business_architect", "application_architect", "arb_member",
    "programme_owner", "workstream_lead", "evidence_owner", "decision_authority",
    "delivery_lead", "outcome_owner", "contributor",
})


def _required_text(value: Any, field: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _optional_text(value: Any) -> str | None:
    normalized = value.strip() if isinstance(value, str) else ""
    return normalized or None


def _date(value: Any, field: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} must be an ISO date") from error
    raise ValueError(f"{field} must be an ISO date")


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def canonical_role_assignment_key(payload: Mapping[str, Any]) -> str:
    """Stable identity for one effective-dated programme role assignment."""
    identity = {
        "programme_id": payload["programme_id"],
        "workstream_id": payload.get("workstream_id"),
        "user_id": payload["user_id"],
        "role": payload["role"],
        "effective_from": payload["effective_from"],
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return f"role-assignment:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _require_write_permission(user) -> None:
    """Programme writes require Permission.GENERAL; a read-only Viewer persona
    must not write programme content even if their enterprise_role string
    matches a write role (security.md S2)."""
    if not user.can(Permission.GENERAL):
        raise NotAuthorised("write_permission_required")


class TransformationProgrammeService:
    """Owns programme intake and aggregate-level mutations."""

    @classmethod
    def list_programmes(cls, *, actor: ActorContext) -> tuple[ProgrammeView, ...]:
        """Return the actor's tenant-scoped programme portfolio.

        Enterprise portfolio roles see the organisation portfolio.  Users who
        have no such enterprise role see only programmes for which they hold a
        currently-effective assignment.  A caller with neither is refused,
        including when the tenant has no programmes, so an empty collection
        cannot become an authorisation bypass.
        """
        with Session(db.engine) as session:
            user = cls._load_runtime_user(session, actor)
            server_roles = cls._server_roles(user)
            statement = select(StrategicInitiative.id).where(
                StrategicInitiative.organization_id == actor.organization_id,
                StrategicInitiative.record_kind == "transformation_programme",
            )
            if not server_roles.intersection(READ_ROLES):
                today = date.today()
                assigned_programme_ids = select(
                    ProgrammeRoleAssignment.programme_id
                ).where(
                    ProgrammeRoleAssignment.organization_id
                    == actor.organization_id,
                    ProgrammeRoleAssignment.user_id == actor.user_id,
                    ProgrammeRoleAssignment.role.in_(READ_ROLES),
                    ProgrammeRoleAssignment.effective_from <= today,
                    or_(
                        ProgrammeRoleAssignment.effective_to.is_(None),
                        ProgrammeRoleAssignment.effective_to >= today,
                    ),
                )
                if not session.scalar(select(assigned_programme_ids.exists())):
                    raise NotAuthorised("programme_list_not_authorised")
                statement = statement.where(
                    StrategicInitiative.id.in_(assigned_programme_ids)
                )
            programme_ids = tuple(
                session.scalars(statement.order_by(StrategicInitiative.id)).all()
            )
        return tuple(
            cls.get_programme(actor=actor, programme_id=programme_id)
            for programme_id in programme_ids
        )

    @classmethod
    def create_programme(
        cls,
        *,
        actor: ActorContext,
        command_key: str,
        request: ProgrammeIntake,
    ) -> CommandResult:
        validated = cls.validate_intake(actor=actor, request=request)
        natural_key = f"programme-intake:{command_key}"
        return CommandService.execute(
            actor=actor,
            operation="programme.create",
            idempotency_key=command_key,
            payload=asdict(validated),
            natural_key=natural_key,
            authorizer=cls.authorise_create_programme(validated, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._insert_intake_graph(
                session=session, actor=actor, request=validated, claim=claim
            ),
        )

    @classmethod
    def validate_intake(
        cls, *, actor: ActorContext, request: ProgrammeIntake, require_workstream_type: bool = True
    ) -> ProgrammeIntake:
        """*require_workstream_type* is False for the templated path, where
        each template workstream carries its own type (R1-06)."""
        if not isinstance(request, ProgrammeIntake):
            raise TypeError("request must be ProgrammeIntake")
        name = _required_text(request.name, "name")
        objective = _required_text(request.objective, "objective")
        if require_workstream_type and request.workstream_type not in WORKSTREAM_TYPES:
            raise ValueError("workstream_type is not supported")
        if not isinstance(request.scope_expression, Mapping):
            raise ValueError("scope_expression must be an object")
        target_date = _date(request.target_date, "target_date")
        target_reason = _optional_text(request.target_date_unavailable_reason)
        if target_date is None and target_reason is None:
            raise ValueError("target_date_unavailable_reason is required when target_date is unavailable")

        outcome = dict(request.outcome) if isinstance(request.outcome, Mapping) else {}
        statement = _required_text(outcome.get("statement"), "outcome.statement")
        owner_id = outcome.get("owner_id")
        if not isinstance(request.owner_id, int) or request.owner_id <= 0:
            raise ValueError("owner_id must be a positive integer")
        if not isinstance(owner_id, int) or owner_id <= 0:
            raise ValueError("outcome.owner_id must be a positive integer")
        direction = outcome.get("direction")
        if direction not in IMPROVEMENT_DIRECTIONS:
            raise ValueError("outcome.direction is not supported")
        measure = dict(outcome.get("measure") or {})
        measure["metric_name"] = _required_text(measure.get("metric_name"), "outcome.measure.metric_name")
        measure["unit"] = _required_text(measure.get("unit"), "outcome.measure.unit")
        aggregation = measure.get("aggregation")
        if aggregation not in MEASURE_AGGREGATIONS:
            raise ValueError("outcome.measure.aggregation is not supported")
        currency = _optional_text(measure.get("currency"))
        if currency is not None:
            currency = currency.upper()
            if currency not in ISO_4217_CURRENCIES:
                raise ValueError("outcome.measure.currency must be an ISO 4217 code")
        baseline = _decimal(measure.get("baseline_value"), "outcome.measure.baseline_value")
        target = _decimal(measure.get("target_value"), "outcome.measure.target_value")
        unavailable_reason = _optional_text(measure.get("unavailable_reason"))
        if baseline is None and unavailable_reason is None:
            raise ValueError("outcome.measure.unavailable_reason is required when baseline is unavailable")
        if target is None:
            raise ValueError("outcome.measure.target_value is required")
        measure.update(
            currency=currency,
            baseline_value=baseline,
            target_value=target,
            unavailable_reason=unavailable_reason,
        )
        outcome.update(statement=statement, owner_id=owner_id, direction=direction, measure=measure)

        with Session(db.engine) as session:
            for supplied_id, field in ((request.owner_id, "owner"), (owner_id, "outcome_owner")):
                found = session.scalar(
                    select(User.id).where(
                        User.id == supplied_id,
                        User.organization_id == actor.organization_id,
                    )
                )
                if found is None:
                    raise NotFound(f"{field}_not_found")

        return replace(
            request,
            name=name,
            objective=objective,
            target_date=target_date,
            target_date_unavailable_reason=target_reason,
            scope_expression=dict(request.scope_expression),
            outcome=outcome,
        )

    @classmethod
    def authorise_create_programme(
        cls, validated: ProgrammeIntake, natural_key: str
    ) -> OperationAuthorizer:
        def authorize(session: Session, actor: ActorContext, operation: str, supplied_key: str) -> None:
            if operation != "programme.create" or supplied_key != natural_key:
                raise NotAuthorised("programme_create_command_mismatch")
            user = cls._load_runtime_user(session, actor)
            _require_write_permission(user)
            if not cls._server_roles(user).intersection(CREATE_ROLES):
                raise NotAuthorised("programme_create_not_authorised")
            owner = session.scalar(
                select(User.id).where(
                    User.id == validated.owner_id,
                    User.organization_id == actor.organization_id,
                )
            )
            if owner is None:
                raise NotFound("owner_not_found")

        return authorize

    @staticmethod
    def _build_programme(actor, request):
        """Shared by the intake path and the templated path (R1-06)."""
        return StrategicInitiative(
            organization_id=actor.organization_id,
            name=request.name,
            description=request.objective,
            record_kind="transformation_programme",
            status="draft",
            owner_id=request.owner_id,
            target_completion_date=request.target_date,
            revision=1,
        )

    @staticmethod
    def _build_workstream(
        actor,
        programme,
        *,
        workstream_type,
        objective,
        scope_expression,
        lead_id,
        target_date,
        target_date_unavailable_reason,
        name=None,
        template_key=None,
    ):
        return ProgrammeWorkstream(
            organization_id=actor.organization_id,
            programme=programme,
            workstream_type=workstream_type,
            objective=objective,
            scope_expression=dict(scope_expression),
            lifecycle_stage="objective",
            lead_id=lead_id,
            target_date=target_date,
            target_date_unavailable_reason=target_date_unavailable_reason,
            name=name,
            template_key=template_key,
            revision=1,
        )

    @staticmethod
    def _build_owner_and_outcome(actor, programme, request, *, outcome_workstream):
        """The programme_owner assignment, outcome commitment and measure."""
        outcome_data = request.outcome
        measure_data = outcome_data["measure"]
        assignment = ProgrammeRoleAssignment(
            organization_id=actor.organization_id,
            programme=programme,
            workstream=None,
            user_id=request.owner_id,
            role="programme_owner",
            effective_from=date.today(),
            assigned_by_id=actor.user_id,
        )
        outcome = ProgrammeOutcomeCommitment(
            organization_id=actor.organization_id,
            programme=programme,
            workstream=outcome_workstream,
            statement=outcome_data["statement"],
            owner_id=outcome_data["owner_id"],
            improvement_direction=outcome_data["direction"],
            target_date=request.target_date,
            lifecycle="committed",
        )
        measure_values = {
            "baseline_amount" if measure_data["currency"] else "baseline_value": measure_data["baseline_value"],
            "target_amount" if measure_data["currency"] else "target_value": measure_data["target_value"],
        }
        measure = MeasureDefinition(
            organization_id=actor.organization_id,
            outcome_commitment=outcome,
            metric_name=measure_data["metric_name"],
            unit=measure_data["unit"],
            currency=measure_data["currency"],
            aggregation=measure_data["aggregation"],
            unavailable_reason=measure_data["unavailable_reason"],
            target_date=request.target_date,
            **measure_values,
        )
        return assignment, outcome, measure

    @classmethod
    def _insert_intake_graph(cls, *, session, actor, request, claim) -> DomainMutationResult:
        # Receipt-time authorization is intentionally repeated on the persisted
        # actor row inside the mutation transaction. Holding this row lock until
        # commit serializes account-role revocation with the first programme
        # persistence. User has no separate active/disabled account predicate.
        runtime_user = cls._load_runtime_user(session, actor, lock=True)
        _require_write_permission(runtime_user)
        if not cls._server_roles(runtime_user).intersection(CREATE_ROLES):
            raise NotAuthorised("programme_create_not_authorised")
        programme = cls._build_programme(actor, request)
        workstream = cls._build_workstream(
            actor,
            programme,
            workstream_type=request.workstream_type,
            objective=request.objective,
            scope_expression=request.scope_expression,
            lead_id=request.owner_id,
            target_date=request.target_date,
            target_date_unavailable_reason=request.target_date_unavailable_reason,
        )
        assignment, outcome, measure = cls._build_owner_and_outcome(
            actor, programme, request, outcome_workstream=workstream
        )
        session.add_all([programme, assignment, workstream, outcome, measure])
        session.flush()

        from app.services.archimate_backbone import sync_archimate_element

        sync_archimate_element(workstream, session=session, organization_id=actor.organization_id)

        object_ids = {
            "programme_id": programme.id,
            "role_assignment_id": assignment.id,
            "workstream_id": workstream.id,
            "outcome_commitment_id": outcome.id,
            "measure_definition_id": measure.id,
        }
        response = dict(object_ids)
        return DomainMutationResult(
            object_ids=object_ids,
            response=response,
            outbox_events=(
                {
                    "event_type": "programme.created",
                    "payload": {
                        **object_ids,
                        "organization_id": actor.organization_id,
                        "actor_id": actor.user_id,
                        "command_receipt_id": claim.receipt_id,
                    },
                },
            ),
        )

    @classmethod
    def assign_role(
        cls,
        *,
        actor: ActorContext,
        programme_id: int,
        workstream_id: int | None,
        user_id: int,
        role: str,
        effective_from: date,
        effective_to: date | None,
        expected_revision: int,
        command_key: str,
    ) -> CommandResult:
        if role not in PROGRAMME_ROLES:
            raise ValueError("role is not supported")
        effective_from = _date(effective_from, "effective_from")
        if effective_from is None:
            raise ValueError("effective_from is required")
        effective_to = _date(effective_to, "effective_to")
        if effective_to is not None and effective_to < effective_from:
            raise ValueError("effective_to must not precede effective_from")
        programme, workstream, user = cls.load_assignment_scope(
            actor=actor,
            programme_id=programme_id,
            workstream_id=workstream_id,
            user_id=user_id,
        )
        cls.authorise_role_assignment(actor, programme, workstream, role)
        payload = {
            "programme_id": programme.id,
            "workstream_id": workstream_id,
            "user_id": user.id,
            "role": role,
            "effective_from": effective_from.isoformat(),
            "effective_to": effective_to.isoformat() if effective_to else None,
            "expected_revision": expected_revision,
        }
        return CommandService.execute(
            actor=actor,
            operation="programme.assign_role",
            idempotency_key=command_key,
            payload=payload,
            natural_key=canonical_role_assignment_key(payload),
            authorizer=cls.authorise_role_assignment_replay(payload),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._insert_role_assignment(
                session, actor, programme, workstream, user, payload, claim
            ),
        )

    @classmethod
    def load_assignment_scope(cls, *, actor, programme_id, workstream_id, user_id):
        with Session(db.engine) as session:
            programme = cls._programme_query(session, actor, programme_id).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme_not_found")
            workstream = None
            if workstream_id is not None:
                workstream = session.execute(
                    select(ProgrammeWorkstream).where(
                        ProgrammeWorkstream.id == workstream_id,
                        ProgrammeWorkstream.programme_id == programme_id,
                        ProgrammeWorkstream.organization_id == actor.organization_id,
                    )
                ).scalar_one_or_none()
                if workstream is None:
                    raise NotFound("workstream_not_found")
            user = session.execute(
                select(User).where(User.id == user_id, User.organization_id == actor.organization_id)
            ).scalar_one_or_none()
            if user is None:
                raise NotFound("user_not_found")
            session.expunge_all()
            return programme, workstream, user

    @classmethod
    def authorise_role_assignment(cls, actor, programme, workstream, role) -> None:
        with Session(db.engine) as session:
            cls._require_programme_authority(
                session,
                actor,
                programme.id,
                workstream.id if workstream else None,
                ROLE_ASSIGNMENT_ROLES,
                "role_assignment_not_authorised",
            )

    @classmethod
    def authorise_role_assignment_replay(cls, payload) -> OperationAuthorizer:
        expected_key = canonical_role_assignment_key(payload)

        def authorize(session, actor, operation, natural_key):
            if operation != "programme.assign_role" or natural_key != expected_key:
                raise NotAuthorised("role_assignment_command_mismatch")
            programme = cls._programme_query(session, actor, payload["programme_id"]).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme_not_found")
            cls._require_active_programme(programme)
            workstream_id = payload.get("workstream_id")
            if workstream_id is not None:
                workstream = session.scalar(
                    select(ProgrammeWorkstream.id).where(
                        ProgrammeWorkstream.id == workstream_id,
                        ProgrammeWorkstream.programme_id == programme.id,
                        ProgrammeWorkstream.organization_id == actor.organization_id,
                    )
                )
                if workstream is None:
                    raise NotFound("workstream_not_found")
            assigned_user = session.scalar(
                select(User.id).where(
                    User.id == payload["user_id"],
                    User.organization_id == actor.organization_id,
                )
            )
            if assigned_user is None:
                raise NotFound("user_not_found")
            cls._require_programme_authority(
                session,
                actor,
                programme.id,
                workstream_id,
                ROLE_ASSIGNMENT_ROLES,
                "role_assignment_not_authorised",
            )

        return authorize

    @classmethod
    def _insert_role_assignment(cls, session, actor, programme, workstream, user, payload, claim):
        locked_programme = cls._programme_query(
            session, actor, payload["programme_id"], lock=True
        ).scalar_one_or_none()
        if locked_programme is None:
            raise NotFound("programme_not_found")
        cls._require_active_programme(locked_programme)
        locked_stream = None
        if payload["workstream_id"] is not None:
            locked_stream = session.execute(
                select(ProgrammeWorkstream).where(
                    ProgrammeWorkstream.id == payload["workstream_id"],
                    ProgrammeWorkstream.programme_id == payload["programme_id"],
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                ).with_for_update()
            ).scalar_one_or_none()
            if locked_stream is None:
                raise NotFound("workstream_not_found")
            if locked_stream.revision != payload["expected_revision"]:
                raise CommandConflict("stale_revision")
            aggregate = locked_stream
            aggregate_type = "workstream"
        else:
            if locked_programme.revision != payload["expected_revision"]:
                raise CommandConflict("stale_revision")
            aggregate = locked_programme
            aggregate_type = "programme"
        cls._require_programme_authority(
            session,
            actor,
            payload["programme_id"],
            payload["workstream_id"],
            ROLE_ASSIGNMENT_ROLES,
            "role_assignment_not_authorised",
            lock=True,
        )
        assignment = ProgrammeRoleAssignment(
            organization_id=actor.organization_id,
            programme_id=payload["programme_id"],
            workstream_id=payload["workstream_id"],
            user_id=payload["user_id"],
            role=payload["role"],
            effective_from=date.fromisoformat(payload["effective_from"]),
            effective_to=date.fromisoformat(payload["effective_to"]) if payload["effective_to"] else None,
            assigned_by_id=actor.user_id,
        )
        session.add(assignment)
        aggregate.revision += 1
        session.flush()
        response = {
            "role_assignment_id": assignment.id,
            "revision": aggregate.revision,
            "aggregate_type": aggregate_type,
        }
        return DomainMutationResult(
            response,
            response,
            ({"event_type": "programme.role_assigned", "payload": {**response, "programme_id": payload["programme_id"]}},),
        )

    @classmethod
    def update_objective(
        cls,
        *,
        actor: ActorContext,
        workstream_id: int,
        objective: str,
        scope_expression: Mapping[str, Any],
        expected_revision: int,
        command_key: str,
    ) -> CommandResult:
        objective = _required_text(objective, "objective")
        if not isinstance(scope_expression, Mapping):
            raise ValueError("scope_expression must be an object")
        payload = {
            "workstream_id": workstream_id,
            "objective": objective,
            "scope_expression": dict(scope_expression),
            "expected_revision": expected_revision,
        }
        return CommandService.execute(
            actor=actor,
            operation="workstream.update_objective",
            idempotency_key=command_key,
            payload=payload,
            natural_key=f"objective:{workstream_id}:{expected_revision}",
            authorizer=cls.authorise_objective_update(workstream_id, expected_revision),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._update_objective_locked(session, actor, payload, claim),
        )

    @classmethod
    def authorise_objective_update(cls, workstream_id, expected_revision) -> OperationAuthorizer:
        expected_key = f"objective:{workstream_id}:{expected_revision}"

        def authorize(session, actor, operation, natural_key):
            if operation != "workstream.update_objective" or natural_key != expected_key:
                raise NotAuthorised("objective_update_command_mismatch")
            stream = session.execute(
                select(ProgrammeWorkstream).where(
                    ProgrammeWorkstream.id == workstream_id,
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                )
            ).scalar_one_or_none()
            if stream is None:
                raise NotFound("workstream_not_found")
            programme = cls._programme_query(session, actor, stream.programme_id).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme_not_found")
            cls._require_active_programme(programme)
            cls._require_programme_authority(
                session, actor, stream.programme_id, stream.id, OBJECTIVE_ROLES, "objective_update_not_authorised"
            )

        return authorize

    @classmethod
    def _update_objective_locked(cls, session, actor, payload, claim):
        stream_scope = session.execute(
            select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.id == payload["workstream_id"],
                ProgrammeWorkstream.organization_id == actor.organization_id,
            )
        ).scalar_one_or_none()
        if stream_scope is None:
            raise NotFound("workstream_not_found")
        programme = cls._programme_query(
            session, actor, stream_scope.programme_id, lock=True
        ).scalar_one_or_none()
        if programme is None:
            raise NotFound("programme_not_found")
        cls._require_active_programme(programme)
        stream = session.execute(
            select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.id == payload["workstream_id"],
                ProgrammeWorkstream.programme_id == programme.id,
                ProgrammeWorkstream.organization_id == actor.organization_id,
            ).with_for_update()
        ).scalar_one()
        if stream.revision != payload["expected_revision"]:
            raise CommandConflict("stale_revision")
        cls._require_programme_authority(
            session,
            actor,
            stream.programme_id,
            stream.id,
            OBJECTIVE_ROLES,
            "objective_update_not_authorised",
            lock=True,
        )
        before_revision = stream.revision
        stream.objective = payload["objective"]
        stream.scope_expression = dict(payload["scope_expression"])
        stream.revision += 1
        session.flush()
        response = {"workstream_id": stream.id, "revision": stream.revision}
        return DomainMutationResult(
            response,
            response,
            ({"event_type": "workstream.objective_updated", "payload": {**response, "before_revision": before_revision}},),
        )

    @classmethod
    def create_workstream(
        cls,
        *,
        actor: ActorContext,
        programme_id: int,
        workstream_type: str,
        objective: str,
        scope_expression: Mapping[str, Any],
        target_date: Any,
        target_date_unavailable_reason: Any,
        lead_id: int,
        command_key: str,
    ) -> CommandResult:
        """DEF-010, Capgemini dry-run: the new-programme wizard could create
        exactly one workstream and there was no route to add another —
        `/solutions/programmes/<id>/workstreams` had no create action, so
        every programme was permanently limited to its founding workstream.
        Mirrors update_objective's validate-then-CommandService.execute shape.
        """
        if workstream_type not in WORKSTREAM_TYPES:
            raise ValueError("workstream_type is not supported")
        objective = _required_text(objective, "objective")
        if not isinstance(scope_expression, Mapping):
            raise ValueError("scope_expression must be an object")
        parsed_target_date = _date(target_date, "target_date")
        target_reason = _optional_text(target_date_unavailable_reason)
        if parsed_target_date is None and target_reason is None:
            raise ValueError("target_date_unavailable_reason is required when target_date is unavailable")
        if not isinstance(lead_id, int) or lead_id <= 0:
            raise ValueError("lead_id must be a positive integer")
        payload = {
            "programme_id": programme_id,
            "workstream_type": workstream_type,
            "objective": objective,
            "scope_expression": dict(scope_expression),
            "target_date": parsed_target_date,
            "target_date_unavailable_reason": target_reason,
            "lead_id": lead_id,
        }
        natural_key = f"workstream-create:{command_key}"
        return CommandService.execute(
            actor=actor,
            operation="workstream.create",
            idempotency_key=command_key,
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_create_workstream(programme_id, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._insert_workstream_locked(session, actor, payload, claim),
        )

    @classmethod
    def authorise_create_workstream(cls, programme_id: int, natural_key: str) -> OperationAuthorizer:
        def authorize(session: Session, actor: ActorContext, operation: str, supplied_key: str) -> None:
            if operation != "workstream.create" or supplied_key != natural_key:
                raise NotAuthorised("workstream_create_command_mismatch")
            programme = cls._programme_query(session, actor, programme_id).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme_not_found")
            cls._require_active_programme(programme)
            cls._require_programme_authority(
                session, actor, programme_id, None, CREATE_ROLES | frozenset({"programme_owner"}),
                "workstream_create_not_authorised",
            )

        return authorize

    @classmethod
    def _insert_workstream_locked(cls, session, actor, payload, claim):
        programme = cls._programme_query(
            session, actor, payload["programme_id"], lock=True
        ).scalar_one_or_none()
        if programme is None:
            raise NotFound("programme_not_found")
        cls._require_active_programme(programme)
        cls._require_programme_authority(
            session,
            actor,
            programme.id,
            None,
            CREATE_ROLES | frozenset({"programme_owner"}),
            "workstream_create_not_authorised",
            lock=True,
        )
        lead = session.scalar(
            select(User.id).where(
                User.id == payload["lead_id"],
                User.organization_id == actor.organization_id,
            )
        )
        if lead is None:
            raise NotFound("lead_not_found")
        workstream = cls._build_workstream(
            actor,
            programme,
            workstream_type=payload["workstream_type"],
            objective=payload["objective"],
            scope_expression=payload["scope_expression"],
            lead_id=payload["lead_id"],
            target_date=payload["target_date"],
            target_date_unavailable_reason=payload["target_date_unavailable_reason"],
        )
        session.add(workstream)
        session.flush()

        from app.services.archimate_backbone import sync_archimate_element

        sync_archimate_element(workstream, session=session, organization_id=actor.organization_id)

        response = {
            "programme_id": programme.id,
            "workstream_id": workstream.id,
            "workstream_type": workstream.workstream_type,
            "revision": workstream.revision,
        }
        return DomainMutationResult(
            response,
            response,
            ({"event_type": "workstream.created", "payload": {**response, "actor_id": actor.user_id}},),
        )

    # ------------------------------------------------------------------ #
    # R1-06: instantiate a programme type template into programme records #
    # ------------------------------------------------------------------ #

    @classmethod
    def instantiate_template(
        cls, *, actor: ActorContext, journey_id: int, command_key: str, request: Mapping[str, Any]
    ) -> CommandResult:
        """Turn a typed journey into a real programme (ADR 0012 decision 3).

        *request* keys: name, objective, owner_id, target_date,
        target_date_unavailable_reason, outcome, leads ({template workstream
        key: user id}). One transaction through CommandService, keyed per
        journey; a missing lead is a ValueError naming the workstream --
        never defaulted to the actor.
        """
        from app.modules.transformation_room.programme_types.loader import ProgrammeTypeCatalogue

        with Session(db.engine) as session:
            journey = session.execute(
                db.text(
                    "SELECT programme_type, programme_id FROM architecture_journeys "
                    "WHERE id = :j AND organization_id = :org"
                ),
                {"j": journey_id, "org": actor.organization_id},
            ).first()
        if journey is None:
            raise NotFound("journey_not_found")
        template_key = journey.programme_type
        if not template_key:
            raise ValueError("This journey has no programme type")
        catalogue = ProgrammeTypeCatalogue()
        template = catalogue.get(template_key)
        if template is None:
            raise ValueError("That programme type is not available")
        offered = {offered_type.key for offered_type in catalogue.offered_types()}
        if template_key not in offered:
            raise ValueError("That programme type is not available")

        intake = ProgrammeIntake(
            name=request.get("name"),
            objective=request.get("objective"),
            owner_id=request.get("owner_id"),
            target_date=request.get("target_date"),
            target_date_unavailable_reason=request.get("target_date_unavailable_reason"),
            workstream_type="other",
            scope_expression={},
            outcome=request.get("outcome") or {},
        )
        validated = cls.validate_intake(actor=actor, request=intake, require_workstream_type=False)

        leads = {}
        raw_leads = request.get("leads") or {}
        with Session(db.engine) as session:
            for workstream in template.workstreams:
                key = workstream["key"]
                lead_id = raw_leads.get(key)
                if not isinstance(lead_id, int) or lead_id <= 0:
                    raise ValueError(f"Choose a lead for the workstream: {workstream.get('name', key)}")
                found = session.scalar(
                    select(User.id).where(User.id == lead_id, User.organization_id == actor.organization_id)
                )
                if found is None:
                    raise ValueError(f"The lead chosen for {workstream.get('name', key)} was not found")
                leads[key] = lead_id

        natural_key = f"journey-programme-structure:{journey_id}"
        payload = {
            **asdict(validated),
            "journey_id": journey_id,
            "template_key": template_key,
            "content_sha256": template.content_sha256,
            "leads": leads,
        }
        return CommandService.execute(
            actor=actor,
            operation="programme.instantiate_template",
            idempotency_key=command_key,
            payload=payload,
            natural_key=natural_key,
            authorizer=cls.authorise_instantiate_template(journey_id, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._instantiate_template_locked(
                session, actor, validated, template, leads, journey_id, claim
            ),
        )

    @classmethod
    def authorise_instantiate_template(cls, journey_id: int, natural_key: str) -> OperationAuthorizer:
        def authorize(session: Session, actor: ActorContext, operation: str, supplied_key: str) -> None:
            if operation != "programme.instantiate_template" or supplied_key != natural_key:
                raise NotAuthorised("instantiate_template_command_mismatch")
            user = cls._load_runtime_user(session, actor)
            _require_write_permission(user)
            if not cls._server_roles(user).intersection(CREATE_ROLES):
                raise NotAuthorised("programme_create_not_authorised")
            row = session.execute(
                db.text(
                    "SELECT owner_id FROM architecture_journeys WHERE id = :j AND organization_id = :org"
                ),
                {"j": journey_id, "org": actor.organization_id},
            ).first()
            if row is None:
                raise NotFound("journey_not_found")
            if row.owner_id != actor.user_id:
                member = session.execute(
                    db.text(
                        "SELECT 1 FROM architecture_journey_members WHERE journey_id = :j "
                        "AND user_id = :u AND organization_id = :org"
                    ),
                    {"j": journey_id, "u": actor.user_id, "org": actor.organization_id},
                ).first()
                if member is None:
                    raise NotAuthorised("journey_edit_not_authorised")

        return authorize

    @classmethod
    def _instantiate_template_locked(cls, session, actor, validated, template, leads, journey_id, claim):
        from app.models.architecture_journey import ArchitectureJourney
        from app.models.implementation_migration import Deliverable, ImplementationEvent, WorkPackage
        from app.services.archimate_backbone import sync_archimate_element

        journey = session.scalar(
            select(ArchitectureJourney)
            .where(
                ArchitectureJourney.id == journey_id,
                ArchitectureJourney.organization_id == actor.organization_id,
            )
            .with_for_update()
        )
        if journey is None:
            raise NotFound("journey_not_found")
        if journey.programme_id is not None:
            existing = {"programme_id": journey.programme_id, "journey_id": journey.id}
            return DomainMutationResult(existing, existing, ())

        runtime_user = cls._load_runtime_user(session, actor, lock=True)
        _require_write_permission(runtime_user)
        if not cls._server_roles(runtime_user).intersection(CREATE_ROLES):
            raise NotAuthorised("programme_create_not_authorised")

        org = actor.organization_id
        programme = cls._build_programme(actor, validated)
        assignment, outcome, measure = cls._build_owner_and_outcome(
            actor, programme, validated, outcome_workstream=None
        )
        session.add_all([programme, assignment, outcome, measure])
        session.flush()

        workstream_rows = {}
        for spec in template.workstreams:
            ws = cls._build_workstream(
                actor,
                programme,
                workstream_type=spec["workstream_type"],
                objective=spec.get("objective") or spec["name"],
                scope_expression={},
                lead_id=leads[spec["key"]],
                target_date=validated.target_date,
                target_date_unavailable_reason=validated.target_date_unavailable_reason,
                name=spec["name"],
                template_key=f"{template.key}.{spec['key']}",
            )
            session.add(ws)
            session.flush()
            sync_archimate_element(ws, session=session, organization_id=org)
            session.add(
                ProgrammeRoleAssignment(
                    organization_id=org,
                    programme=programme,
                    workstream=ws,
                    user_id=leads[spec["key"]],
                    role="workstream_lead",
                    effective_from=date.today(),
                    assigned_by_id=actor.user_id,
                )
            )
            workstream_rows[spec["key"]] = ws
        session.flush()

        work_package_ids, deliverable_ids, event_ids = [], [], []
        for stage_key, stage in template.stages.items():
            stage_packages = []
            for spec in stage.get("deliverables") or ():
                ws = workstream_rows[spec["workstream"]]
                digest = hashlib.sha256(f"journey:{journey.id}:{spec['code']}".encode("utf-8")).hexdigest()
                package = WorkPackage(
                    organization_id=org,
                    name=spec["name"],
                    strategic_initiative_id=programme.id,
                    programme_workstream_id=ws.id,
                    togaf_phase=spec.get("adm_phase"),
                    status="planned",
                    materialisation_key=digest,
                )
                session.add(package)
                session.flush()
                sync_archimate_element(package, session=session, organization_id=org)
                deliverable = Deliverable(
                    name=spec["name"],
                    work_package_id=package.id,
                    delivery_status="planned",
                    template_code=spec["code"],
                    journey_stage=stage_key,
                    declared_element_types=list(spec.get("element_types") or ()),
                )
                session.add(deliverable)
                session.flush()
                sync_archimate_element(deliverable, session=session, organization_id=org)
                work_package_ids.append(package.id)
                deliverable_ids.append(deliverable.id)
                stage_packages.append(package)
            gate_text = stage.get("gate")
            if gate_text:
                event = ImplementationEvent(
                    organization_id=org,
                    name=gate_text,
                    event_type="journey_stage_gate",
                    status="planned",
                )
                session.add(event)
                session.flush()
                sync_archimate_element(event, session=session, organization_id=org)
                for package in stage_packages:
                    session.execute(
                        work_package_events_table.insert().values(
                            work_package_id=package.id,
                            implementation_event_id=event.id,
                            relationship_type="gate",
                            is_blocking=False,
                        )
                    )
                event_ids.append(event.id)

        journey.programme_id = programme.id
        journey.programme_template_version = template.content_sha256
        journey.arb_required_at_decide = template.arb_required_at_decide
        journey.outcome_type = "programme"
        session.flush()

        object_ids = {
            "journey_id": journey.id,
            "programme_id": programme.id,
            "workstream_ids": [w.id for w in workstream_rows.values()],
            "work_package_ids": work_package_ids,
            "deliverable_ids": deliverable_ids,
            "event_ids": event_ids,
        }
        response = dict(object_ids)
        return DomainMutationResult(
            object_ids,
            response,
            (
                {
                    "event_type": "programme.template_instantiated",
                    "payload": {
                        **object_ids,
                        "template_key": template.key,
                        "content_sha256": template.content_sha256,
                        "organization_id": org,
                        "actor_id": actor.user_id,
                        "command_receipt_id": claim.receipt_id,
                    },
                },
            ),
        )

    @classmethod
    def add_workstream_to_model(
        cls, *, actor: ActorContext, programme_id: int, workstream_id: int, command_key: str
    ) -> CommandResult:
        """R1-05 (US-11): give an existing workstream its ArchiMate WorkPackage
        element. Idempotent -- sync_archimate_element leaves an already-synced
        row alone, so a replay or a second click creates nothing further."""
        natural_key = f"workstream-add-to-model:{command_key}"
        return CommandService.execute(
            actor=actor,
            operation="workstream.add_to_model",
            idempotency_key=command_key,
            payload={"programme_id": programme_id, "workstream_id": workstream_id},
            natural_key=natural_key,
            authorizer=cls.authorise_add_to_model(programme_id, workstream_id, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._add_workstream_to_model_locked(
                session, actor, programme_id, workstream_id, claim
            ),
        )

    @classmethod
    def authorise_add_to_model(cls, programme_id: int, workstream_id: int, natural_key: str) -> OperationAuthorizer:
        def authorize(session: Session, actor: ActorContext, operation: str, supplied_key: str) -> None:
            if operation != "workstream.add_to_model" or supplied_key != natural_key:
                raise NotAuthorised("workstream_add_to_model_command_mismatch")
            programme = cls._programme_query(session, actor, programme_id).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme_not_found")
            workstream = session.scalar(
                select(ProgrammeWorkstream.id).where(
                    ProgrammeWorkstream.id == workstream_id,
                    ProgrammeWorkstream.programme_id == programme_id,
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                )
            )
            if workstream is None:
                raise NotFound("workstream_not_found")
            cls._require_programme_authority(
                session, actor, programme_id, workstream_id, LINK_ROLES,
                "workstream_add_to_model_not_authorised",
            )

        return authorize

    @classmethod
    def _add_workstream_to_model_locked(cls, session, actor, programme_id, workstream_id, claim):
        workstream = session.scalar(
            select(ProgrammeWorkstream).where(
                ProgrammeWorkstream.id == workstream_id,
                ProgrammeWorkstream.programme_id == programme_id,
                ProgrammeWorkstream.organization_id == actor.organization_id,
            )
            .with_for_update()
        )
        if workstream is None:
            raise NotFound("workstream_not_found")
        cls._require_programme_authority(
            session, actor, programme_id, workstream_id, LINK_ROLES,
            "workstream_add_to_model_not_authorised",
            lock=True,
        )

        from app.services.archimate_backbone import sync_archimate_element

        sync_archimate_element(workstream, session=session, organization_id=actor.organization_id)
        response = {
            "programme_id": programme_id,
            "workstream_id": workstream.id,
            "archimate_element_id": workstream.archimate_element_id,
        }
        return DomainMutationResult(
            response,
            response,
            ({"event_type": "workstream.added_to_model", "payload": {**response, "actor_id": actor.user_id}},),
        )

    @classmethod
    def archive(
        cls,
        *,
        actor: ActorContext,
        programme_id: int,
        rationale: str,
        expected_revision: int,
        command_key: str,
    ) -> CommandResult:
        payload = {
            "programme_id": programme_id,
            "rationale": _required_text(rationale, "archive rationale"),
            "expected_revision": expected_revision,
        }
        return CommandService.execute(
            actor=actor,
            operation="programme.archive",
            idempotency_key=command_key,
            payload=payload,
            natural_key=f"programme-archive:{programme_id}:{expected_revision}",
            authorizer=cls.authorise_programme_archive(programme_id, expected_revision),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._archive_locked(session, actor, payload, claim),
        )

    @classmethod
    def authorise_programme_archive(cls, programme_id, expected_revision) -> OperationAuthorizer:
        expected_key = f"programme-archive:{programme_id}:{expected_revision}"

        def authorize(session, actor, operation, natural_key):
            if operation != "programme.archive" or natural_key != expected_key:
                raise NotAuthorised("programme_archive_command_mismatch")
            programme = cls._programme_query(session, actor, programme_id).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme_not_found")
            cls._require_programme_authority(
                session, actor, programme_id, None, ARCHIVE_ROLES, "programme_archive_not_authorised"
            )

        return authorize

    @classmethod
    def _archive_locked(cls, session, actor, payload, claim):
        programme = cls._programme_query(session, actor, payload["programme_id"], lock=True).scalar_one_or_none()
        if programme is None:
            raise NotFound("programme_not_found")
        if programme.revision != payload["expected_revision"]:
            raise CommandConflict("stale_revision")
        cls._require_programme_authority(
            session,
            actor,
            programme.id,
            None,
            ARCHIVE_ROLES,
            "programme_archive_not_authorised",
            lock=True,
        )
        if programme.status == "archived":
            raise CommandConflict("programme_already_archived")
        programme.status = "archived"
        programme.archived_at = datetime.now(timezone.utc)
        programme.revision += 1
        session.flush()
        response = {
            "programme_id": programme.id,
            "status": programme.status,
            "revision": programme.revision,
            "rationale": payload["rationale"],
        }
        return DomainMutationResult(
            response,
            response,
            (
                {
                    "event_type": "programme.archived",
                    "payload": {**response, "actor_id": actor.user_id},
                },
            ),
        )

    @classmethod
    def get_programme(cls, *, actor: ActorContext, programme_id: int) -> ProgrammeView:
        programme = cls.load_programme_for_tenant(actor, programme_id)
        readable_workstream_ids = cls.authorise_read(actor, programme)
        workstreams = cls.load_workstreams_for_tenant(actor, programme.id)
        if readable_workstream_ids is not None:
            workstreams = tuple(
                row for row in workstreams if row.id in readable_workstream_ids
            )
        from app.modules.transformation_room.gate_service import TransformationGateService

        next_action = None
        if programme.status != "archived" and programme.archived_at is None:
            next_action = TransformationGateService.next_action(actor=actor, workstreams=workstreams)
        return ProgrammeView(
            programme.id,
            tuple(row.id for row in workstreams),
            programme.status,
            programme.owner_id,
            next_action,
        )

    @classmethod
    def get_workstream(
        cls, *, actor: ActorContext, programme_id: int, workstream_id: int
    ) -> ProgrammeWorkstream:
        """Return one workstream only when it is in the actor's read projection."""
        programme = cls.get_programme(actor=actor, programme_id=programme_id)
        if workstream_id not in programme.workstream_ids:
            raise NotFound("workstream_not_found")
        rows = cls.load_workstreams_for_tenant(actor, programme_id)
        workstream = next((row for row in rows if row.id == workstream_id), None)
        if workstream is None:
            raise NotFound("workstream_not_found")
        return workstream

    @classmethod
    def load_programme_for_tenant(cls, actor, programme_id):
        with Session(db.engine) as session:
            programme = cls._programme_query(session, actor, programme_id).scalar_one_or_none()
            if programme is None:
                raise NotFound("programme_not_found")
            session.expunge(programme)
            return programme

    @classmethod
    def load_workstreams_for_tenant(cls, actor, programme_id):
        with Session(db.engine) as session:
            rows = session.scalars(
                select(ProgrammeWorkstream)
                .where(
                    ProgrammeWorkstream.programme_id == programme_id,
                    ProgrammeWorkstream.organization_id == actor.organization_id,
                )
                .order_by(ProgrammeWorkstream.id)
            ).all()
            session.expunge_all()
            return rows

    @classmethod
    def authorise_read(cls, actor, programme):
        with Session(db.engine) as session:
            persisted = cls._programme_query(session, actor, programme.id).scalar_one_or_none()
            if persisted is None:
                raise NotFound("programme_not_found")
            user = cls._load_runtime_user(session, actor)
            if cls._server_roles(user).intersection(READ_ROLES):
                return None
            today = date.today()
            assignments = session.scalars(
                select(ProgrammeRoleAssignment).where(
                    ProgrammeRoleAssignment.organization_id
                    == actor.organization_id,
                    ProgrammeRoleAssignment.programme_id == persisted.id,
                    ProgrammeRoleAssignment.user_id == actor.user_id,
                    ProgrammeRoleAssignment.role.in_(READ_ROLES),
                    ProgrammeRoleAssignment.effective_from <= today,
                    or_(
                        ProgrammeRoleAssignment.effective_to.is_(None),
                        ProgrammeRoleAssignment.effective_to >= today,
                    ),
                )
            ).all()
            if not assignments:
                raise NotAuthorised("programme_read_not_authorised")
            if any(row.workstream_id is None for row in assignments):
                return None
            return frozenset(
                row.workstream_id
                for row in assignments
                if row.workstream_id is not None
            )

    @staticmethod
    def _require_active_programme(programme) -> None:
        if programme.status == "archived" or programme.archived_at is not None:
            raise CommandConflict("programme_archived")

    @staticmethod
    def _programme_query(session, actor, programme_id, lock=False):
        statement = select(StrategicInitiative).where(
            StrategicInitiative.id == programme_id,
            StrategicInitiative.organization_id == actor.organization_id,
            StrategicInitiative.record_kind == "transformation_programme",
        )
        if lock:
            statement = statement.with_for_update()
        return session.execute(statement)

    @staticmethod
    def _load_runtime_user(session, actor, *, lock=False):
        statement = select(User).where(
            User.id == actor.user_id,
            User.organization_id == actor.organization_id,
        )
        if lock:
            statement = statement.execution_options(
                populate_existing=True
            ).with_for_update()
        user = session.execute(statement).scalar_one_or_none()
        if user is None:
            raise NotAuthorised("actor_not_authorised")
        return user

    @staticmethod
    def _server_roles(user) -> set[str]:
        roles = {user.enterprise_role} if user.enterprise_role else set()
        if user.is_admin():
            roles.add("organization_admin")
        if user.is_platform_admin:
            roles.add("platform_admin")
        return roles

    @classmethod
    def can_create_programme(cls, user) -> bool:
        """Whether *user* may start a programme — the same CREATE_ROLES test the
        create command enforces. Exposed so the wizard can gate at ENTRY and the
        list page can hide the CTA: the 2 Sep 2026 audit (F-04) found a six-step
        wizard that let a Solution Architect fill everything in and only rejected
        at the final submit with a raw 'programme_create_not_authorised'. Authorising
        once at the door and once at the command keeps both honest."""
        try:
            return bool(cls._server_roles(user) & CREATE_ROLES) and user.can(Permission.GENERAL)
        except Exception:
            return False

    @classmethod
    def _require_programme_authority(
        cls,
        session,
        actor,
        programme_id,
        workstream_id,
        allowed_roles,
        denial_reason,
        *,
        lock=False,
    ):
        # Aggregate-mutating callers lock programme then workstream before this
        # user/assignment order. PostgreSQL holds these row locks until commit,
        # so a concurrent authority update cannot become effective between the
        # check and the governed mutation.
        user = cls._load_runtime_user(session, actor, lock=lock)
        roles = cls._server_roles(user)
        if allowed_roles is not READ_ROLES:
            _require_write_permission(user)
        today = date.today()
        assignment_statement = (
            select(ProgrammeRoleAssignment)
            .where(
                ProgrammeRoleAssignment.organization_id == actor.organization_id,
                ProgrammeRoleAssignment.programme_id == programme_id,
                or_(
                    ProgrammeRoleAssignment.workstream_id.is_(None),
                    ProgrammeRoleAssignment.workstream_id == workstream_id,
                ),
                ProgrammeRoleAssignment.user_id == actor.user_id,
            )
            .order_by(ProgrammeRoleAssignment.id)
        )
        if lock:
            assignment_statement = assignment_statement.execution_options(
                populate_existing=True
            ).with_for_update()
        assignments = session.scalars(assignment_statement).all()
        roles.update(
            row.role
            for row in assignments
            if row.effective_from <= today
            and (row.effective_to is None or row.effective_to >= today)
        )
        if not roles.intersection(allowed_roles):
            raise NotAuthorised(denial_reason)


__all__ = ["TransformationProgrammeService", "canonical_role_assignment_key"]
