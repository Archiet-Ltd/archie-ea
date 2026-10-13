"""Deliverable element credit, counts and completion (R1-07, US-4).

The single writer for "this element is credited to this deliverable" (ADR
0013's 2026-09-25 amendment, tech-lead ruling C5): the R1 manual "Add
element" control uses it now; the R3 assistant's tool handler and its
"credit the existing element" suggestion call the same functions, so there
is one place the crediting rule can be wrong rather than three.

`Deliverable` is untenanted (security.md S6). It is reached ONLY through
`deliverable_for_org`, which joins its WorkPackage with an organization_id
predicate; nothing else in this module selects a Deliverable by id alone.
Counting reads the edge table only -- never element provenance.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.archimate_core import ArchiMateElement
from app.models.archimate_element_types import (
    ALL_ELEMENT_TYPES,
    APPLICATION_ELEMENT_TYPES,
    BUSINESS_ELEMENT_TYPES,
    IMPLEMENTATION_ELEMENT_TYPES,
    MOTIVATION_ELEMENT_TYPES,
    PHYSICAL_ELEMENT_TYPES,
    STRATEGY_ELEMENT_TYPES,
    TECHNOLOGY_ELEMENT_TYPES,
)
from app.models.implementation_migration import (
    CREDIT_KINDS,
    Deliverable,
    DeliverableArchimateElement,
    WorkPackage,
)
from app.modules.transformation_room.command_service import CommandService, OperationAuthorizer
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandResult,
    DomainMutationResult,
    NotAuthorised,
    NotFound,
)
from app.modules.transformation_room.programme_service import (
    LINK_ROLES,
    TransformationProgrammeService,
    _require_write_permission,
)

_LAYER_BY_TYPE = {}
for _layer, _types in (
    ("Strategy", STRATEGY_ELEMENT_TYPES),
    ("Business", BUSINESS_ELEMENT_TYPES),
    ("Application", APPLICATION_ELEMENT_TYPES),
    ("Technology", TECHNOLOGY_ELEMENT_TYPES),
    ("Physical", PHYSICAL_ELEMENT_TYPES),
    ("Motivation", MOTIVATION_ELEMENT_TYPES),
    ("Implementation", IMPLEMENTATION_ELEMENT_TYPES),
):
    for _type in _types:
        _LAYER_BY_TYPE[_type] = _layer

MAX_NAME = 100


def deliverable_for_org(session: Session, org_id: int, deliverable_id: int):
    """The ONE accessor for a Deliverable: joins its WorkPackage with the
    organization predicate. Returns (deliverable, work_package) or None."""
    row = session.execute(
        select(Deliverable, WorkPackage)
        .join(WorkPackage, WorkPackage.id == Deliverable.work_package_id)
        .where(Deliverable.id == deliverable_id, WorkPackage.organization_id == org_id)
    ).first()
    return (row[0], row[1]) if row else None


def credit_status(session: Session, org_id: int, deliverable_id: int) -> dict[str, Any] | None:
    """{declared, n, m, elements} for one deliverable, from the edge table
    only. n counts DECLARED types with at least one credited element of that
    type; zero is reported as n == 0 (rendered "None yet", never 0)."""
    found = deliverable_for_org(session, org_id, deliverable_id)
    if found is None:
        return None
    deliverable, _package = found
    declared = list(deliverable.declared_element_types or [])
    rows = session.execute(
        select(ArchiMateElement.id, ArchiMateElement.name, ArchiMateElement.type, DeliverableArchimateElement.credit_kind)
        .join(
            DeliverableArchimateElement,
            (DeliverableArchimateElement.archimate_element_id == ArchiMateElement.id)
            & (DeliverableArchimateElement.organization_id == ArchiMateElement.organization_id),
        )
        .where(
            DeliverableArchimateElement.organization_id == org_id,
            DeliverableArchimateElement.deliverable_id == deliverable_id,
        )
        .order_by(DeliverableArchimateElement.id)
    ).all()
    credited_types = {r.type for r in rows}
    return {
        "declared": declared,
        "m": len(declared),
        "n": sum(1 for t in declared if t in credited_types),
        "elements": [
            {"id": r.id, "name": r.name, "type": r.type, "credit_kind": r.credit_kind} for r in rows
        ],
        "completed": deliverable.delivery_status == "completed",
        "completion_reason": deliverable.completion_reason,
    }


class DeliverableCreditService:
    """Every write goes through CommandService (receipts, idempotency)."""

    # ---- authority --------------------------------------------------- #

    @staticmethod
    def _authorise(session, actor, deliverable_id):
        found = deliverable_for_org(session, actor.organization_id, deliverable_id)
        if found is None:
            raise NotFound("deliverable_not_found")
        _deliverable, package = found
        if package.strategic_initiative_id is None:
            raise NotFound("deliverable_not_found")
        user = TransformationProgrammeService._load_runtime_user(session, actor)
        _require_write_permission(user)
        TransformationProgrammeService._require_programme_authority(
            session, actor, package.strategic_initiative_id, package.programme_workstream_id,
            LINK_ROLES, "deliverable_not_authorised",
        )
        return found

    @classmethod
    def _authorizer(cls, operation_name, deliverable_id, natural_key) -> OperationAuthorizer:
        def authorize(session, actor, operation, supplied_key):
            if operation != operation_name or supplied_key != natural_key:
                raise NotAuthorised("deliverable_command_mismatch")
            cls._authorise(session, actor, deliverable_id)

        return authorize

    @staticmethod
    def _check_declared(deliverable, element_type):
        if element_type not in ALL_ELEMENT_TYPES:
            raise ValueError("That element type is not part of the ArchiMate model")
        if element_type not in (deliverable.declared_element_types or []):
            raise ValueError("That type is not one this deliverable declares")

    # ---- credit an element that already exists ------------------------ #

    @classmethod
    def credit_existing(cls, *, actor: ActorContext, deliverable_id: int, element_id: int, command_key: str) -> CommandResult:
        natural_key = f"deliverable-credit-add:{command_key}"
        return CommandService.execute(
            actor=actor,
            operation="deliverable.credit_add",
            idempotency_key=command_key,
            payload={"deliverable_id": deliverable_id, "element_id": element_id, "kind": "existing"},
            natural_key=natural_key,
            authorizer=cls._authorizer("deliverable.credit_add", deliverable_id, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._credit_existing_locked(session, actor, deliverable_id, element_id),
        )

    @classmethod
    def _credit_existing_locked(cls, session, actor, deliverable_id, element_id, *, approval_id=None):
        deliverable, _package = cls._authorise(session, actor, deliverable_id)
        element = session.scalar(
            select(ArchiMateElement).where(
                ArchiMateElement.id == element_id, ArchiMateElement.organization_id == actor.organization_id
            )
        )
        if element is None:
            raise NotFound("element_not_found")
        cls._check_declared(deliverable, element.type)
        credit_id = cls._insert_credit(session, actor, deliverable_id, element.id, "existing", approval_id)
        response = {"deliverable_id": deliverable_id, "element_id": element.id, "credit_id": credit_id}
        return DomainMutationResult(response, response, ())

    @staticmethod
    def _insert_credit(session, actor, deliverable_id, element_id, kind, approval_id):
        assert kind in CREDIT_KINDS
        existing = session.scalar(
            select(DeliverableArchimateElement.id).where(
                DeliverableArchimateElement.organization_id == actor.organization_id,
                DeliverableArchimateElement.deliverable_id == deliverable_id,
                DeliverableArchimateElement.archimate_element_id == element_id,
            )
        )
        if existing is not None:
            return existing  # crediting twice is an upsert, not a duplicate
        row = DeliverableArchimateElement(
            organization_id=actor.organization_id,
            deliverable_id=deliverable_id,
            archimate_element_id=element_id,
            credit_kind=kind,
            approval_id=approval_id,
            created_by_id=actor.user_id,
        )
        session.add(row)
        session.flush()
        return row.id

    # ---- create a new element and credit it -------------------------- #

    @classmethod
    def create_and_credit(
        cls, *, actor: ActorContext, deliverable_id: int, element_type: str, name: str,
        description: str | None = None, approval_id: int | None = None, command_key: str,
    ) -> CommandResult:
        clean = name.strip() if isinstance(name, str) else ""
        if not clean or len(clean) > MAX_NAME:
            raise ValueError(f"Give the element a name of 1 to {MAX_NAME} characters")
        natural_key = f"deliverable-credit-add:{command_key}"
        return CommandService.execute(
            actor=actor,
            operation="deliverable.credit_add",
            idempotency_key=command_key,
            payload={
                "deliverable_id": deliverable_id, "element_type": element_type, "name": clean,
                "description": description, "approval_id": approval_id, "kind": "created",
            },
            natural_key=natural_key,
            authorizer=cls._authorizer("deliverable.credit_add", deliverable_id, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._create_and_credit_locked(
                session, actor, deliverable_id, element_type, clean, description, approval_id
            ),
        )

    @classmethod
    def _create_and_credit_locked(cls, session, actor, deliverable_id, element_type, name, description, approval_id):
        from app.services.archimate_backbone import create_backbone_element

        deliverable, _package = cls._authorise(session, actor, deliverable_id)
        cls._check_declared(deliverable, element_type)
        duplicate = session.scalar(
            select(ArchiMateElement.id).where(
                ArchiMateElement.organization_id == actor.organization_id,
                ArchiMateElement.type == element_type,
                ArchiMateElement.name == name,
            )
        )
        if duplicate is not None:
            raise ValueError("An element with that name and type already exists. Credit the existing one instead.")
        element = create_backbone_element(
            element_type=element_type,
            layer=_LAYER_BY_TYPE[element_type],
            name=name,
            description=description,
            organization_id=actor.organization_id,
            session=session,
            provenance={"source_deliverable_id": deliverable_id},  # provenance only; never counted
        )
        credit_id = cls._insert_credit(session, actor, deliverable_id, element.id, "created", approval_id)
        response = {"deliverable_id": deliverable_id, "element_id": element.id, "credit_id": credit_id}
        return DomainMutationResult(response, response, ())

    # ---- remove a credit --------------------------------------------- #

    @classmethod
    def remove_credit(cls, *, actor: ActorContext, deliverable_id: int, element_id: int, command_key: str) -> CommandResult:
        natural_key = f"deliverable-credit-remove:{command_key}"
        return CommandService.execute(
            actor=actor,
            operation="deliverable.credit_remove",
            idempotency_key=command_key,
            payload={"deliverable_id": deliverable_id, "element_id": element_id},
            natural_key=natural_key,
            authorizer=cls._authorizer("deliverable.credit_remove", deliverable_id, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._remove_locked(session, actor, deliverable_id, element_id),
        )

    @classmethod
    def _remove_locked(cls, session, actor, deliverable_id, element_id):
        cls._authorise(session, actor, deliverable_id)
        row = session.scalar(
            select(DeliverableArchimateElement).where(
                DeliverableArchimateElement.organization_id == actor.organization_id,
                DeliverableArchimateElement.deliverable_id == deliverable_id,
                DeliverableArchimateElement.archimate_element_id == element_id,
            )
        )
        if row is None:
            raise NotFound("credit_not_found")
        session.delete(row)  # the credit only; the element itself is never deleted
        session.flush()
        response = {"deliverable_id": deliverable_id, "element_id": element_id}
        return DomainMutationResult(response, response, ())

    # ---- complete ---------------------------------------------------- #

    @classmethod
    def complete(cls, *, actor: ActorContext, deliverable_id: int, reason: str | None, command_key: str) -> CommandResult:
        natural_key = f"deliverable-complete:{command_key}"
        cleaned = reason.strip() if isinstance(reason, str) and reason.strip() else None
        return CommandService.execute(
            actor=actor,
            operation="deliverable.complete",
            idempotency_key=command_key,
            payload={"deliverable_id": deliverable_id, "reason": cleaned},
            natural_key=natural_key,
            authorizer=cls._authorizer("deliverable.complete", deliverable_id, natural_key),
            natural_key_resolver=CommandService.fail_closed_pre_envelope_recovery,
            handler=lambda session, claim: cls._complete_locked(session, actor, deliverable_id, cleaned),
        )

    @classmethod
    def _complete_locked(cls, session, actor, deliverable_id, reason):
        deliverable, _package = cls._authorise(session, actor, deliverable_id)
        status = credit_status(session, actor.organization_id, deliverable_id)
        if status["n"] == 0 and not reason:
            raise ValueError("Say why this deliverable is complete with nothing in the model")
        deliverable.delivery_status = "completed"
        deliverable.delivered_date = date.today()
        deliverable.completion_reason = reason
        session.flush()
        response = {
            "deliverable_id": deliverable_id, "n": status["n"], "m": status["m"], "reason": reason,
        }
        return DomainMutationResult(
            response, response,
            ({"event_type": "deliverable.completed", "payload": {**response, "actor_id": actor.user_id}},),
        )


__all__ = ["DeliverableCreditService", "credit_status", "deliverable_for_org"]
