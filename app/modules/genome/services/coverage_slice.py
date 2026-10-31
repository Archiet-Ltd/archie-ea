"""Business-layer genome slice: Capability -> Application coverage.

A minimal, DETERMINISTIC, zero-LLM genome slice builder. It reads directly
from the canonical business-layer stores for ONE organization:

    unified_capabilities                   (UnifiedCapability, the capability record;
                                            the organisation's own live rows)
    application_components                 (ApplicationComponent, TenantMixin)
    unified_application_capability_mapping (UnifiedApplicationCapabilityMapping,
                                            the coverage authority)

The capability record and the coverage mapping are the stores the reuse
register names (capability-record); the older business_capability and
application_capability_mapping pair is not read here, so the coverage page,
the chat and the roadmap's work packages (whose capability_id points at
unified_capabilities) name the same capabilities by the same ids.

and each store's `archimate_element_id` link, and assembles a validated dict:

    {
      "slice_version": "1.0.0",
      "organization_id": int,
      "capability_source": "unified_capabilities",  # ADR 0008: named, single store
      "capabilities": [ {id, name, code, archimate_element_id}, ... ],
      "applications":  [ {id, name, archimate_element_id}, ... ],
      "cells": [ {capability_id, application_id, mapping,
                  provenance: {capability_archimate_element_id,
                               application_archimate_element_id}}, ... ],
      "spec_hash": "sha256:...",
    }

This is NOT the 2990-line compile_genome. It is the demo-scoped business-layer
slice described in genome_rearch/01_blueprint.md section 4.

Provenance is STRUCTURAL and NON-OPTIONAL: a cell whose capability or
application does not resolve to a real ArchiMateElement id is a build error
(SliceProvenanceError), never a silent gap. This mirrors genome_to_bundle's
`archimate_source_id` pattern — the id is threaded from the source row, never
inferred and never asked of an LLM.
"""
from __future__ import annotations

import hashlib
import json

from app.extensions import db

SLICE_VERSION = "1.0.0"
CAPABILITY_SOURCE = "unified_capabilities"


class SliceProvenanceError(ValueError):
    """A cell could not be traced to real ArchiMate element ids on both ends."""


def _element_ids_present() -> set:
    """Set of ArchiMateElement ids that exist (for provenance resolution checks).

    ArchiMateElement is not TenantMixin-scoped by organization_id in this model
    slice, so element ids are validated by existence; the capability/application
    rows that reference them are already org-scoped by the caller.
    """
    from app.models.archimate_core import ArchiMateElement

    rows = (
        db.session.query(ArchiMateElement.id)
        .filter(ArchiMateElement.id.isnot(None))
        .all()
    )
    return {r[0] for r in rows}


def build_coverage_slice(org_id: int) -> dict:
    """Build the deterministic Capability->Application coverage slice for one org.

    Args:
        org_id: the organization (tenant) to build for. Passed explicitly so the
            builder is correct both inside a request (TenantMixin also scopes)
            and outside one (CLI/tests/scheduler have no g.current_org_id).

    Returns:
        A validated slice dict (see module docstring).

    Raises:
        SliceProvenanceError: if any populated cell lacks a resolvable element id
            on either the capability or the application end.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability

    # --- Read the three stores, org-scoped and deterministically ordered. ---
    # The organisation's own capability records; a record retired into another
    # is no longer a capability of the organisation.
    caps = (
        db.session.query(UnifiedCapability)
        .filter(UnifiedCapability.organization_id == org_id)
        .filter(UnifiedCapability.retired_into_id.is_(None))
        .order_by(UnifiedCapability.id.asc())
        .all()
    )
    apps = (
        db.session.query(ApplicationComponent)
        .filter(ApplicationComponent.organization_id == org_id)
        .order_by(ApplicationComponent.id.asc())
        .all()
    )
    # The mapping table carries no organisation column: it is fenced through
    # its application, which must be this organisation's.
    mappings = (
        db.session.query(UnifiedApplicationCapabilityMapping)
        .join(
            ApplicationComponent,
            ApplicationComponent.id
            == UnifiedApplicationCapabilityMapping.application_component_id,
        )
        .filter(ApplicationComponent.organization_id == org_id)
        .order_by(UnifiedApplicationCapabilityMapping.id.asc())
        .all()
    )

    cap_by_id = {c.id: c for c in caps}
    app_by_id = {a.id: a for a in apps}

    capabilities = [
        {
            "id": c.id,
            "name": c.name,
            "code": c.code,
            "archimate_element_id": c.archimate_element_id,
        }
        for c in caps
    ]
    applications = [
        {
            "id": a.id,
            "name": a.name,
            "archimate_element_id": a.archimate_element_id,
        }
        for a in apps
    ]

    element_ids = _element_ids_present()

    cells = []
    seen = set()
    for m in mappings:
        cap = cap_by_id.get(m.unified_capability_id)
        app = app_by_id.get(m.application_component_id)
        # A mapping whose endpoints are not in this org's caps/apps is out of
        # scope for the slice (defence against cross-store drift).
        if cap is None or app is None:
            continue

        key = (cap.id, app.id)
        if key in seen:
            # Collapse duplicate mappings between the same pair deterministically
            # (keep the first by mapping id, already ordered).
            continue
        seen.add(key)

        cap_elem = cap.archimate_element_id
        app_elem = app.archimate_element_id

        # Provenance is structural and non-optional (blueprint section 4).
        if cap_elem is None or cap_elem not in element_ids:
            raise SliceProvenanceError(
                f"Capability {cap.id} ({cap.name!r}) has no resolvable "
                f"ArchiMateElement id (got {cap_elem!r}); a coverage cell "
                f"cannot be built without capability provenance."
            )
        if app_elem is None or app_elem not in element_ids:
            raise SliceProvenanceError(
                f"Application {app.id} ({app.name!r}) has no resolvable "
                f"ArchiMateElement id (got {app_elem!r}); a coverage cell "
                f"cannot be built without application provenance."
            )

        cells.append(
            {
                "capability_id": cap.id,
                "application_id": app.id,
                "mapping": {
                    "support_level": m.support_level,
                    "coverage_percentage": m.coverage_percentage,
                    "relationship_type": m.relationship_type,
                    "is_strategic": bool(m.is_strategic),
                },
                "provenance": {
                    "capability_archimate_element_id": cap_elem,
                    "application_archimate_element_id": app_elem,
                },
            }
        )

    # Deterministic order for cells: by (capability_id, application_id).
    cells.sort(key=lambda c: (c["capability_id"], c["application_id"]))

    slice_dict = {
        "slice_version": SLICE_VERSION,
        "organization_id": org_id,
        "capability_source": CAPABILITY_SOURCE,
        "capabilities": capabilities,
        "applications": applications,
        "cells": cells,
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
