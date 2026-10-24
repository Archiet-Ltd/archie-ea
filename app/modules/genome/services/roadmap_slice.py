"""Implementation-layer genome slice: Plateau + WorkPackage transformation roadmap.

A minimal, DETERMINISTIC, zero-LLM genome slice builder for ADR 0010's
IMPLEMENTATION domain. It reads directly from the populated
implementation-and-migration stores for ONE organization:

    plateaus       (Plateau, TenantMixin)        -- time-phased architecture states
    unified_work_packages (UnifiedWorkPackage)   -- the units of change, the one store
    relationships from a work package's element  -- its plateau and the gaps it closes
                                                    (read through work_package_service)

and each store's ``archimate_element_id`` link, and assembles a validated dict:

    {
      "slice_version": "1.0.0",
      "organization_id": int,
      "domain": "implementation",             # ADR 0010 domain
      "plateaus": [ {id, name, sequence_order, target_date, provenance}, ... ],
      "work_packages": [ {id, name, plateau_id, status, priority,
                          start_date, target_date, percent_complete,
                          closed_gap_ids, provenance}, ... ],
      "spec_hash": "sha256:...",
    }

This is the deterministic emitter half of the codegen pattern applied to a new
domain (ADR 0010) — the same shape as the proven business-layer coverage slice.

Provenance is STRUCTURAL and NON-OPTIONAL, and fail-closed:

  - a row whose ``archimate_element_id`` is set but does NOT resolve to a real
    ArchiMateElement is a build error (SliceProvenanceError) — never a silent
    gap, exactly as the coverage slice treats a dangling anchor;
  - a row that legitimately carries NO element id gets an HONEST *synthetic*
    provenance record (``origin="synthetic"`` with a ``reason``), never a
    fabricated id. Per ADR 0010's 02_schema note, an absent anchor is recorded
    as synthetic, not invented — because unlike capabilities, plateaus and work
    packages have no auto-anchor listener and may pre-date one.
"""
from __future__ import annotations

import hashlib
import json

from app.extensions import db

SLICE_VERSION = "1.0.0"
DOMAIN = "implementation"

# The ArchiMate type each store maps to when its own element is absent — used to
# label the honest synthetic provenance record (never to fabricate an id).
_PLATEAU_TYPE = "Plateau"
_WORK_PACKAGE_TYPE = "WorkPackage"


class SliceProvenanceError(ValueError):
    """A row's archimate_element_id is set but does not resolve to a real element."""


def _element_type_by_id() -> dict:
    """Map of ArchiMateElement id -> its ArchiMate type (for provenance stamping).

    Element ids are validated by existence; the plateau/work-package rows that
    reference them are already org-scoped by the caller.
    """
    from app.models.archimate_core import ArchiMateElement

    rows = (
        db.session.query(ArchiMateElement.id, ArchiMateElement.type)
        .filter(ArchiMateElement.id.isnot(None))
        .all()
    )
    return {r[0]: r[1] for r in rows}


def _provenance(element_id, expected_type: str, element_types: dict, label: str) -> dict:
    """Resolve one row's provenance, fail-closed.

    Returns a structural provenance record when the element id resolves, or an
    honest synthetic record when the row legitimately carries no element id.
    Raises when an id is present but dangling.
    """
    if element_id is None:
        # Legitimately unanchored — record it honestly, do NOT invent an id.
        return {
            "origin": "synthetic",
            "archimate_element_id": None,
            "archimate_type": expected_type,
            "reason": (
                f"{label} has no ArchiMateElement anchor; recorded as synthetic "
                f"provenance rather than fabricating an id (ADR 0010 02_schema)."
            ),
        }
    if element_id not in element_types:
        raise SliceProvenanceError(
            f"{label} references ArchiMateElement id {element_id!r} which does not "
            f"resolve to a real element; a roadmap row cannot carry a dangling anchor."
        )
    return {
        "origin": "structural",
        "archimate_element_id": element_id,
        "archimate_type": element_types[element_id] or expected_type,
    }


def _date_iso(value):
    """ISO string for a date/datetime, or None. Deterministic and JSON-safe."""
    if value is None:
        return None
    if hasattr(value, "date"):  # a datetime column: the roadmap works in days
        value = value.date()
    return value.isoformat()


def build_roadmap_slice(org_id: int) -> dict:
    """Build the deterministic transformation-roadmap slice for one org.

    Args:
        org_id: the organization (tenant) to build for. Passed explicitly so the
            builder is correct both inside a request (TenantMixin also scopes)
            and outside one (CLI/tests/scheduler have no g.current_org_id).

    Returns:
        A validated slice dict (see module docstring).

    Raises:
        SliceProvenanceError: if any plateau or work package carries an
            archimate_element_id that does not resolve to a real element.
    """
    from app.models.implementation_migration import Plateau
    from app.services import work_package_service

    # --- Read the stores, org-scoped and deterministically ordered. ---
    plateaus = (
        db.session.query(Plateau)
        .filter(Plateau.organization_id == org_id)
        .order_by(
            Plateau.sequence_order.asc().nullslast(),
            Plateau.id.asc(),
        )
        .all()
    )
    # The one work package store, and the one reader of its plateau and gap links.
    work_packages = work_package_service.query_for(org_id).order_by(
        work_package_service.UnifiedWorkPackage.id.asc()
    ).all()
    links = work_package_service.plateau_and_gap_links(work_packages, org_id)

    element_types = _element_type_by_id()
    plateau_ids = {p.id for p in plateaus}

    plateau_dicts = [
        {
            "id": p.id,
            "name": p.name,
            "sequence_order": p.sequence_order,
            "target_date": _date_iso(p.target_date),
            "provenance": _provenance(
                p.archimate_element_id, _PLATEAU_TYPE, element_types,
                f"Plateau {p.id} ({p.name!r})",
            ),
        }
        for p in plateaus
    ]

    work_package_dicts = []
    for w in work_packages:
        mine = links.get(w.id, {})
        # A plateau assignment only counts if the plateau is in this org's set
        # (defence against cross-store drift, mirroring the coverage slice).
        linked = [pid for pid in mine.get("plateau_ids", []) if pid in plateau_ids]
        plateau_id = linked[0] if linked else None
        work_package_dicts.append(
            {
                "id": w.id,
                "name": w.name,
                "plateau_id": plateau_id,
                "status": w.status,
                "priority": w.priority,
                "start_date": _date_iso(w.start_date),
                "target_date": _date_iso(w.end_date),
                "percent_complete": int(w.progress_percentage or 0),
                "application_component_id": w.application_component_id,
                "capability_id": w.capability_id,
                "enterprise_initiative_id": w.enterprise_initiative_id,
                "goal_id": w.goal_id,
                "togaf_phase": w.togaf_phase,
                "element_type": w.element_type,
                "context": w.context,
                "context_id": w.context_id,
                "programme_id": w.enterprise_initiative_id,
                "closed_gap_ids": sorted(mine.get("gap_ids", [])),
                "provenance": _provenance(
                    w.archimate_element_id, _WORK_PACKAGE_TYPE, element_types,
                    f"WorkPackage {w.id} ({w.name!r})",
                ),
            }
        )

    slice_dict = {
        "slice_version": SLICE_VERSION,
        "organization_id": org_id,
        "domain": DOMAIN,
        "plateaus": plateau_dicts,
        "work_packages": work_package_dicts,
    }
    slice_dict["spec_hash"] = _spec_hash(slice_dict)
    return slice_dict


def _spec_hash(slice_dict: dict) -> str:
    """Deterministic sha256 over the slice content (excluding the hash itself).

    Identical input -> identical output. Uses sorted-key canonical JSON so key
    ordering never perturbs the hash.
    """
    payload = {k: v for k, v in slice_dict.items() if k != "spec_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
