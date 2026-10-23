"""Objectives and key results on the existing model.

An objective is a goal element on the ArchiMate backbone; a key result is a
``MeasureDefinition`` whose ``subject_element_id`` is that goal element.
Measurements are recorded by ``OutcomeMeasurementService`` against the
canonical Benefit the measure points at, so they land in the one
``outcome_measurements`` history the Transformation Room already reads.
This module adds no table and no second recording path.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.benefit import Benefit
from app.models.motivation import Goal
from app.models.transformation_execution import OutcomeMeasurement
from app.models.transformation_programme import (
    MEASURE_AGGREGATIONS,
    MeasureDefinition,
)
from app.models.user import User
from app.modules.transformation_room.domain import (
    ActorContext,
    CommandResult,
    NotAuthorised,
    NotFound,
)
from app.modules.transformation_room.outcome_service import OutcomeMeasurementService
from app.modules.transformation_room.programme_service import CREATE_ROLES, READ_ROLES
from app.services.archimate_backbone import sync_archimate_element

WRITE_ROLES = CREATE_ROLES | frozenset({"business_architect", "portfolio_manager"})
HISTORY_LIMIT = 50


def _text(value: Any, field: str, limit: int) -> str:
    normalized = unicodedata.normalize("NFC", value).strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > limit:
        raise ValueError(f"invalid {field}")
    return normalized


def _number(value: Any, field: str) -> Decimal:
    if isinstance(value, (bool, float)) or value is None:
        raise ValueError(f"{field} must be an exact decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not parsed.is_finite() or abs(parsed) >= Decimal("1e16"):
        raise ValueError(f"{field} must be a finite number")
    return parsed


def _require(actor: ActorContext, roles: frozenset[str], code: str) -> None:
    if not actor.roles.intersection(roles):
        raise NotAuthorised(code)


def _goal_element(actor: ActorContext, element_id: int) -> ArchiMateElement:
    element = db.session.scalar(
        select(ArchiMateElement).where(
            ArchiMateElement.id == element_id,
            ArchiMateElement.organization_id == actor.organization_id,
            func.lower(ArchiMateElement.type) == "goal",
        )
    )
    if element is None:
        raise NotFound("objective_not_found")
    return element


class ObjectiveService:
    """Set objectives (goal elements), attach key results, read progress."""

    @classmethod
    def set_objective(
        cls,
        *,
        actor: ActorContext,
        goal_element_id: int | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Pick an existing goal element, or create a goal and its element."""
        _require(actor, WRITE_ROLES, "objective_write_not_authorised")
        if goal_element_id is not None:
            element = _goal_element(actor, goal_element_id)
            return cls._objective_row(actor, element)
        goal = Goal(
            organization_id=actor.organization_id,
            name=_text(name, "name", 255),
            description=description.strip() if isinstance(description, str) and description.strip() else None,
            status="active",
        )
        db.session.add(goal)
        element = sync_archimate_element(goal)
        db.session.flush()
        if element is None:
            element = db.session.get(ArchiMateElement, goal.archimate_element_id)
        row = cls._objective_row(actor, element)
        db.session.commit()
        return row

    @classmethod
    def add_key_result(
        cls,
        *,
        actor: ActorContext,
        goal_element_id: int,
        metric_name: str,
        unit: str,
        baseline_value: Any,
        target_value: Any,
        aggregation: str = "latest",
        owner_id: int | None = None,
        target_date: date | None = None,
    ) -> dict:
        _require(actor, WRITE_ROLES, "objective_write_not_authorised")
        element = _goal_element(actor, goal_element_id)
        metric = _text(metric_name, "metric_name", 255)
        unit_text = _text(unit, "unit", 40)
        if aggregation not in MEASURE_AGGREGATIONS:
            raise ValueError("invalid aggregation")
        baseline = _number(baseline_value, "baseline_value")
        target = _number(target_value, "target_value")
        owner = owner_id if owner_id is not None else actor.user_id
        in_org = db.session.scalar(
            select(User.id).where(User.id == owner, User.organization_id == actor.organization_id)
        )
        if in_org is None:
            raise NotFound("owner_not_found")
        # The Benefit is the measurement subject OutcomeMeasurementService
        # already records against; it is the projection of this key result.
        benefit = Benefit(
            organization_id=actor.organization_id,
            name=f"{element.name}: {metric}"[:255],
            benefit_type=None,
            status="planned",
            measure=metric,
            unit=unit_text,
            baseline_value=baseline,
            target_value=target,
            target_date=target_date,
            owner_id=owner,
        )
        db.session.add(benefit)
        db.session.flush()
        measure = MeasureDefinition(
            organization_id=actor.organization_id,
            subject_element_id=element.id,
            benefit_id=benefit.id,
            metric_name=metric,
            unit=unit_text,
            aggregation=aggregation,
            baseline_value=baseline,
            target_value=target,
            target_date=target_date,
        )
        db.session.add(measure)
        db.session.flush()
        row = cls._key_result_row(actor, measure, benefit)
        db.session.commit()
        return row

    @classmethod
    def record_measurement(
        cls,
        *,
        actor: ActorContext,
        key_result_id: int,
        value: Decimal | None,
        unavailable_reason: str | None,
        observed_at: datetime,
        source_identity: str,
        source_version: str,
        command_key: str,
    ) -> CommandResult:
        measure = cls._measure(actor, key_result_id)
        return OutcomeMeasurementService.record(
            actor=actor,
            benefit_id=measure.benefit_id,
            value=value,
            unavailable_reason=unavailable_reason,
            observed_at=observed_at,
            source_identity=source_identity,
            source_version=source_version,
            command_key=command_key,
        )

    @classmethod
    def list_objectives(cls, actor: ActorContext) -> list[dict]:
        _require(actor, READ_ROLES | WRITE_ROLES, "objective_read_not_authorised")
        elements = db.session.scalars(
            select(ArchiMateElement)
            .where(
                ArchiMateElement.organization_id == actor.organization_id,
                func.lower(ArchiMateElement.type) == "goal",
            )
            .order_by(ArchiMateElement.id)
        ).all()
        return [cls._objective_row(actor, element) for element in elements]

    @classmethod
    def get_objective(cls, actor: ActorContext, goal_element_id: int) -> dict:
        _require(actor, READ_ROLES | WRITE_ROLES, "objective_read_not_authorised")
        element = _goal_element(actor, goal_element_id)
        measures = cls._measures_for(actor, element.id)
        return {
            **cls._objective_row(actor, element),
            "key_results": [
                cls._key_result_row(actor, measure, measure.benefit, history=True)
                for measure in measures
            ],
        }

    @classmethod
    def get_key_result(cls, actor: ActorContext, key_result_id: int) -> dict:
        _require(actor, READ_ROLES | WRITE_ROLES, "objective_read_not_authorised")
        measure = cls._measure(actor, key_result_id)
        return cls._key_result_row(actor, measure, measure.benefit, history=True)

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _measures_for(actor, element_id):
        return db.session.scalars(
            select(MeasureDefinition)
            .where(
                MeasureDefinition.organization_id == actor.organization_id,
                MeasureDefinition.subject_element_id == element_id,
            )
            .order_by(MeasureDefinition.id)
        ).all()

    @staticmethod
    def _measure(actor, key_result_id):
        measure = db.session.scalar(
            select(MeasureDefinition).where(
                MeasureDefinition.id == key_result_id,
                MeasureDefinition.organization_id == actor.organization_id,
                MeasureDefinition.subject_element_id.is_not(None),
                MeasureDefinition.benefit_id.is_not(None),
            )
        )
        if measure is None:
            raise NotFound("key_result_not_found")
        return measure

    @classmethod
    def _objective_row(cls, actor, element):
        count = db.session.scalar(
            select(func.count(MeasureDefinition.id)).where(
                MeasureDefinition.organization_id == actor.organization_id,
                MeasureDefinition.subject_element_id == element.id,
            )
        )
        return {
            "goal_element_id": element.id,
            "name": element.name,
            "description": element.description,
            "key_result_count": count,
        }

    @classmethod
    def _key_result_row(cls, actor, measure, benefit, *, history=False):
        measurements = db.session.scalars(
            select(OutcomeMeasurement)
            .where(
                OutcomeMeasurement.organization_id == actor.organization_id,
                OutcomeMeasurement.benefit_id == measure.benefit_id,
            )
            .order_by(OutcomeMeasurement.observed_at.desc(), OutcomeMeasurement.id.desc())
            .limit(HISTORY_LIMIT)
        ).all()
        latest = measurements[0] if measurements else None
        row = {
            **measure.to_dict(),
            "benefit_id": measure.benefit_id,
            "owner_id": benefit.owner_id if benefit is not None else None,
            "status": benefit.status if benefit is not None else None,
            "latest_value": (
                format(latest.value, "f") if latest is not None and latest.value is not None else None
            ),
            "latest_observed_at": latest.observed_at.isoformat() if latest is not None else None,
            "latest_unavailable_reason": latest.unavailable_reason if latest is not None else None,
        }
        if history:
            row["measurements"] = [
                {
                    "id": m.id,
                    "value": format(m.value, "f") if m.value is not None else None,
                    "unavailable_reason": m.unavailable_reason,
                    "observed_at": m.observed_at.isoformat(),
                    "source_identity": m.source_identity,
                    "source_version": m.source_version,
                }
                for m in measurements
            ]
        return row


__all__ = ["ObjectiveService"]
