"""
Value Stream Service

Business logic for Value Streams, Value Stream Stages, and the BIZBOK
capability x value-stream cross-mapping grid.

All public functions are fault-tolerant: they catch and log exceptions rather
than letting a bad row or missing relationship take down a page render, and
return an empty/neutral shape on failure so templates can render an empty
state instead of a 500.
"""

import logging
from typing import Any, Dict, List, Optional

from app import db
from app.datetime_helpers import utcnow
from app.models.unified_capability import (
    CapabilityValueStreamMapping,
    UnifiedCapability,
    ValueStream,
    ValueStreamStage,
)

logger = logging.getLogger(__name__)

VALID_SUPPORT_TYPES = {"primary", "secondary", "supporting"}
VALID_IMPACT_LEVELS = {"critical", "high", "medium", "low"}


# ---------------------------------------------------------------------------
# Value Stream CRUD
# ---------------------------------------------------------------------------


def list_value_streams() -> List[Dict[str, Any]]:
    """Return all value streams with stage counts and mapped-capability counts."""
    results: List[Dict[str, Any]] = []
    try:
        streams = ValueStream.query.order_by(ValueStream.name).all()
    except Exception:
        logger.error("Failed to list value streams", exc_info=True)
        return results

    for vs in streams:
        try:
            stage_count = vs.stages.count()
        except Exception:
            logger.warning("Failed to count stages for value stream %s", vs.id, exc_info=True)
            stage_count = 0

        try:
            mapped_count = (
                db.session.query(CapabilityValueStreamMapping.capability_id)
                .filter(CapabilityValueStreamMapping.value_stream_id == vs.id)
                .distinct()
                .count()
            )
        except Exception:
            logger.warning(
                "Failed to count mapped capabilities for value stream %s", vs.id, exc_info=True
            )
            mapped_count = 0

        results.append(
            {
                "id": vs.id,
                "code": vs.code,
                "name": vs.name,
                "description": vs.description,
                "value_stream_type": vs.value_stream_type,
                "industry_domain": vs.industry_domain,
                "strategic_importance": vs.strategic_importance,
                "business_owner": vs.business_owner,
                "target_cycle_time": vs.target_cycle_time,
                "current_cycle_time": vs.current_cycle_time,
                "quality_target": vs.quality_target,
                "current_quality": vs.current_quality,
                "stage_count": stage_count,
                "mapped_capability_count": mapped_count,
            }
        )
    return results


def get_value_stream(value_stream_id: int) -> Optional[ValueStream]:
    """Fetch a single ValueStream by id, or None."""
    try:
        return ValueStream.query.filter_by(id=value_stream_id).first()
    except Exception:
        logger.error("Failed to fetch value stream %s", value_stream_id, exc_info=True)
        return None


def get_value_stream_with_stages(value_stream_id: int) -> Optional[Dict[str, Any]]:
    """Return a ValueStream dict enriched with its ordered stages."""
    vs = get_value_stream(value_stream_id)
    if not vs:
        return None

    try:
        stages = (
            ValueStreamStage.query.filter(ValueStreamStage.value_stream_id == value_stream_id)
            .order_by(ValueStreamStage.stage_order)
            .all()
        )
    except Exception:
        logger.error(
            "Failed to fetch stages for value stream %s", value_stream_id, exc_info=True
        )
        stages = []

    return {
        "id": vs.id,
        "code": vs.code,
        "name": vs.name,
        "description": vs.description,
        "value_stream_type": vs.value_stream_type,
        "industry_domain": vs.industry_domain,
        "strategic_importance": vs.strategic_importance,
        "business_owner": vs.business_owner,
        "target_cycle_time": vs.target_cycle_time,
        "current_cycle_time": vs.current_cycle_time,
        "quality_target": vs.quality_target,
        "current_quality": vs.current_quality,
        "stages": [_stage_to_dict(s) for s in stages],
    }


def create_value_stream(data: Dict[str, Any]) -> ValueStream:
    """Create and persist a new ValueStream. Raises on failure (caller handles rollback).

    Uses an ORM insert so the ``after_insert`` mapper event on ValueStream
    (app/models/strategy_layer.py::create_archimate_value_stream) fires and mirrors
    the row into the Strategy layer as an ArchiMateElement.

    This deliberately replaces an earlier Core-level INSERT. That workaround existed
    because the listener reads ``target.archimate_element_id`` and the model had no
    such column, so an ORM insert raised AttributeError. Core inserts fire no mapper
    events, which dodged the crash but also meant no value stream ever reached the
    ArchiMate layer — the AI assistant reads that layer and consequently reported
    "no value streams modelled" while the value-stream page listed them. The column
    now exists (see ValueStream.archimate_element_id), so the ORM path is safe.
    """
    now = utcnow().replace(tzinfo=None)
    vs = ValueStream(
        name=data.get("name"),
        code=data.get("code") or None,
        description=data.get("description"),
        value_stream_type=data.get("value_stream_type") or None,
        industry_domain=data.get("industry_domain"),
        strategic_importance=data.get("strategic_importance"),
        business_owner=data.get("business_owner"),
        target_cycle_time=_to_int(data.get("target_cycle_time")),
        current_cycle_time=_to_int(data.get("current_cycle_time")),
        quality_target=_to_float(data.get("quality_target")),
        current_quality=_to_float(data.get("current_quality")),
        created_at=now,
        updated_at=now,
    )
    db.session.add(vs)
    db.session.commit()
    return vs


def update_value_stream(value_stream_id: int, data: Dict[str, Any]) -> Optional[ValueStream]:
    """Update an existing ValueStream. Returns the updated instance or None if not found."""
    vs = get_value_stream(value_stream_id)
    if not vs:
        return None

    for field in (
        "name",
        "code",
        "description",
        "value_stream_type",
        "industry_domain",
        "strategic_importance",
        "business_owner",
    ):
        if field in data:
            setattr(vs, field, data.get(field) or None)

    if "target_cycle_time" in data:
        vs.target_cycle_time = _to_int(data.get("target_cycle_time"))
    if "current_cycle_time" in data:
        vs.current_cycle_time = _to_int(data.get("current_cycle_time"))
    if "quality_target" in data:
        vs.quality_target = _to_float(data.get("quality_target"))
    if "current_quality" in data:
        vs.current_quality = _to_float(data.get("current_quality"))

    db.session.commit()
    return vs


def delete_value_stream(value_stream_id: int) -> bool:
    """Delete a ValueStream and its stages/mappings. Returns True if deleted."""
    vs = get_value_stream(value_stream_id)
    if not vs:
        return False

    try:
        CapabilityValueStreamMapping.query.filter(
            CapabilityValueStreamMapping.value_stream_id == value_stream_id
        ).delete(synchronize_session=False)
        ValueStreamStage.query.filter(
            ValueStreamStage.value_stream_id == value_stream_id
        ).delete(synchronize_session=False)
        db.session.delete(vs)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        logger.error("Failed to delete value stream %s", value_stream_id, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Stage CRUD
# ---------------------------------------------------------------------------


def _stage_to_dict(stage: ValueStreamStage) -> Dict[str, Any]:
    return {
        "id": stage.id,
        "name": stage.name,
        "description": stage.description,
        "value_stream_id": stage.value_stream_id,
        "stage_order": stage.stage_order,
        "stage_type": stage.stage_type,
        "customer_facing": bool(stage.customer_facing),
        "target_duration": stage.target_duration,
        "current_duration": stage.current_duration,
        "quality_gate": bool(stage.quality_gate),
        "entry_criteria": stage.entry_criteria,
        "exit_criteria": stage.exit_criteria,
        "stakeholders": stage.stakeholders,
        "value_items": stage.value_items,
        "stakeholder_list": _lines(stage.stakeholders),
        "value_item_list": _lines(stage.value_items),
    }


# Stage definition fields captured as free text. An empty submission clears
# the field back to NULL ("not recorded"), never to an empty string.
STAGE_TEXT_FIELDS = ("entry_criteria", "exit_criteria", "stakeholders", "value_items")


def _lines(value: Optional[str]) -> List[str]:
    """Split a one-item-per-line text field into its non-blank items."""
    if not value:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def _clean_text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def create_stage(value_stream_id: int, data: Dict[str, Any]) -> Optional[ValueStreamStage]:
    """Create a stage within a value stream. Auto-assigns the next stage_order if omitted."""
    vs = get_value_stream(value_stream_id)
    if not vs:
        return None

    stage_order = data.get("stage_order")
    if stage_order in (None, ""):
        try:
            max_order = (
                db.session.query(db.func.max(ValueStreamStage.stage_order))
                .filter(ValueStreamStage.value_stream_id == value_stream_id)
                .scalar()
            )
        except Exception:
            max_order = None
        stage_order = (max_order or 0) + 1
    else:
        stage_order = _to_int(stage_order) or 1

    stage = ValueStreamStage(
        name=data.get("name"),
        description=data.get("description"),
        value_stream_id=value_stream_id,
        stage_order=stage_order,
        stage_type=data.get("stage_type"),
        customer_facing=bool(data.get("customer_facing")),
        target_duration=_to_int(data.get("target_duration")),
        current_duration=_to_int(data.get("current_duration")),
        quality_gate=bool(data.get("quality_gate")),
        **{field: _clean_text(data.get(field)) for field in STAGE_TEXT_FIELDS},
    )
    db.session.add(stage)
    db.session.commit()
    return stage


def update_stage(stage_id: int, data: Dict[str, Any]) -> Optional[ValueStreamStage]:
    """Update an existing stage."""
    try:
        stage = ValueStreamStage.query.filter_by(id=stage_id).first()
    except Exception:
        logger.error("Failed to fetch stage %s", stage_id, exc_info=True)
        return None
    if not stage:
        return None

    for field in ("name", "description", "stage_type"):
        if field in data:
            setattr(stage, field, data.get(field) or None)
    if "customer_facing" in data:
        stage.customer_facing = bool(data.get("customer_facing"))
    if "quality_gate" in data:
        stage.quality_gate = bool(data.get("quality_gate"))
    if "stage_order" in data:
        stage.stage_order = _to_int(data.get("stage_order")) or stage.stage_order
    if "target_duration" in data:
        stage.target_duration = _to_int(data.get("target_duration"))
    if "current_duration" in data:
        stage.current_duration = _to_int(data.get("current_duration"))
    for field in STAGE_TEXT_FIELDS:
        if field in data:
            setattr(stage, field, _clean_text(data.get(field)))

    db.session.commit()
    return stage


def delete_stage(stage_id: int) -> bool:
    """Delete a stage and any capability mappings pointing at it."""
    try:
        stage = ValueStreamStage.query.get(stage_id)
        if not stage:
            return False
        CapabilityValueStreamMapping.query.filter(
            CapabilityValueStreamMapping.value_stream_stage_id == stage_id
        ).delete(synchronize_session=False)
        db.session.delete(stage)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        logger.error("Failed to delete stage %s", stage_id, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# BIZBOK Capability x Value-Stream-Stage Grid
# ---------------------------------------------------------------------------


def build_bizbok_grid(value_stream_id: int) -> Dict[str, Any]:
    """
    Build the BIZBOK capability (rows) x value-stream-stage (columns) grid.

    Returns:
        {
            "value_stream": {...} | None,
            "stages": [ {id, name, stage_order, ...}, ... ],
            "capabilities": [ {id, name, code, level}, ... ],
            "cells": { "<capability_id>:<stage_id>": {mapping_id, support_type,
                       support_level, capability_contribution, impact_level,
                       stage_criticality}, ... }
        }
    Only capabilities that have at least one mapping into this value stream are
    included as rows, keeping the grid focused on what's actually been assessed.
    """
    empty: Dict[str, Any] = {
        "value_stream": None,
        "stages": [],
        "capabilities": [],
        "cells": {},
    }

    vs = get_value_stream(value_stream_id)
    if not vs:
        return empty

    try:
        stages = (
            ValueStreamStage.query.filter(ValueStreamStage.value_stream_id == value_stream_id)
            .order_by(ValueStreamStage.stage_order)
            .all()
        )
    except Exception:
        logger.error(
            "Failed to load stages while building grid for value stream %s",
            value_stream_id,
            exc_info=True,
        )
        stages = []

    try:
        mappings = CapabilityValueStreamMapping.query.filter(
            CapabilityValueStreamMapping.value_stream_id == value_stream_id
        ).all()
    except Exception:
        logger.error(
            "Failed to load mappings while building grid for value stream %s",
            value_stream_id,
            exc_info=True,
        )
        mappings = []

    capability_ids = sorted({m.capability_id for m in mappings if m.capability_id})
    capabilities_by_id: Dict[int, UnifiedCapability] = {}
    if capability_ids:
        try:
            for cap in UnifiedCapability.query.filter(
                UnifiedCapability.id.in_(capability_ids)
            ).all():
                capabilities_by_id[cap.id] = cap
        except Exception:
            logger.error(
                "Failed to load capabilities for value stream %s grid",
                value_stream_id,
                exc_info=True,
            )

    cells: Dict[str, Any] = {}
    for m in mappings:
        if not m.capability_id or not m.value_stream_stage_id:
            continue
        key = f"{m.capability_id}:{m.value_stream_stage_id}"
        cells[key] = {
            "mapping_id": m.id,
            "support_type": m.support_type,
            "support_level": m.support_level,
            "capability_contribution": m.capability_contribution,
            "impact_level": m.impact_level,
            "stage_criticality": m.stage_criticality,
        }

    capability_rows = [
        {
            "id": cap.id,
            "name": cap.name,
            "code": cap.code,
            "level": cap.level,
            # Read from the capability record itself; None means nobody has
            # assessed it and the grid renders an em dash, not a level.
            "current_maturity_level": cap.current_maturity_level,
            "target_maturity_level": cap.target_maturity_level,
        }
        for cap_id in capability_ids
        if (cap := capabilities_by_id.get(cap_id)) is not None
    ]
    capability_rows.sort(key=lambda c: (c["name"] or "").lower())

    return {
        "value_stream": {
            "id": vs.id,
            "code": vs.code,
            "name": vs.name,
        },
        "stages": [_stage_to_dict(s) for s in stages],
        "capabilities": capability_rows,
        "cells": cells,
    }


def list_unmapped_capabilities(
    value_stream_id: int, search: str = "", limit: int = 25
) -> List[Dict[str, Any]]:
    """Capabilities not yet present as a row in this value stream's grid, for the 'add row' picker."""
    try:
        mapped_ids = {
            row[0]
            for row in db.session.query(CapabilityValueStreamMapping.capability_id)
            .filter(CapabilityValueStreamMapping.value_stream_id == value_stream_id)
            .distinct()
            .all()
        }
    except Exception:
        logger.error(
            "Failed to load mapped capability ids for value stream %s",
            value_stream_id,
            exc_info=True,
        )
        mapped_ids = set()

    try:
        query = UnifiedCapability.query
        if mapped_ids:
            query = query.filter(~UnifiedCapability.id.in_(mapped_ids))
        if search:
            query = query.filter(UnifiedCapability.name.ilike(f"%{search}%"))
        caps = query.order_by(UnifiedCapability.name).limit(limit).all()
    except Exception:
        logger.error(
            "Failed to search unmapped capabilities for value stream %s",
            value_stream_id,
            exc_info=True,
        )
        caps = []

    return [
        {
            "id": c.id,
            "name": c.name,
            "code": c.code,
            "level": c.level,
            "current_maturity_level": c.current_maturity_level,
            "target_maturity_level": c.target_maturity_level,
        }
        for c in caps
    ]


def upsert_mapping_cell(
    capability_id: int,
    value_stream_id: int,
    value_stream_stage_id: int,
    data: Dict[str, Any],
) -> CapabilityValueStreamMapping:
    """
    Create or update the CapabilityValueStreamMapping cell for
    (capability_id, value_stream_stage_id) within value_stream_id.

    Raises ValueError on invalid input; caller is responsible for the
    db.session transaction boundary on failure (this commits on success).
    """
    if not capability_id or not value_stream_id or not value_stream_stage_id:
        raise ValueError("capability_id, value_stream_id and value_stream_stage_id are required")

    support_level = _to_int(data.get("support_level"), default=3)
    if support_level is not None:
        support_level = max(1, min(5, support_level))

    contribution = _to_int(data.get("capability_contribution"), default=50)
    if contribution is not None:
        contribution = max(0, min(100, contribution))

    support_type = data.get("support_type") or "primary"
    if support_type not in VALID_SUPPORT_TYPES:
        support_type = "primary"

    impact_level = data.get("impact_level") or "medium"
    if impact_level not in VALID_IMPACT_LEVELS:
        impact_level = "medium"

    stage_criticality = data.get("stage_criticality") or "medium"
    if stage_criticality not in VALID_IMPACT_LEVELS:
        stage_criticality = "medium"

    mapping = CapabilityValueStreamMapping.query.filter_by(
        capability_id=capability_id,
        value_stream_id=value_stream_id,
        value_stream_stage_id=value_stream_stage_id,
    ).first()

    if not mapping:
        mapping = CapabilityValueStreamMapping(
            capability_id=capability_id,
            value_stream_id=value_stream_id,
            value_stream_stage_id=value_stream_stage_id,
        )
        db.session.add(mapping)

    mapping.support_type = support_type
    mapping.support_level = support_level
    mapping.impact_level = impact_level
    mapping.capability_contribution = contribution
    mapping.stage_criticality = stage_criticality
    mapping.cycle_time_impact = _to_int(data.get("cycle_time_impact"))
    mapping.quality_impact = _to_int(data.get("quality_impact"))
    mapping.cost_impact = _to_int(data.get("cost_impact"))
    if data.get("assessment_notes") is not None:
        mapping.assessment_notes = data.get("assessment_notes")

    db.session.commit()
    return mapping


def delete_mapping_cell(
    capability_id: int, value_stream_id: int, value_stream_stage_id: int
) -> bool:
    """Remove the mapping cell for (capability, value_stream, stage). Returns True if a row was deleted."""
    mapping = CapabilityValueStreamMapping.query.filter_by(
        capability_id=capability_id,
        value_stream_id=value_stream_id,
        value_stream_stage_id=value_stream_stage_id,
    ).first()
    if not mapping:
        return False
    db.session.delete(mapping)
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_int(value, default=None):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=None):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
