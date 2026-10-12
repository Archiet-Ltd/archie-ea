"""OA-001: Gap Register Service — unified gap view across TOGAF ADM Phases B, C, D.

Aggregates gap records from three source tables into a single queryable register:
  - 'capability'      → CapabilityGapAnalysis  (Phase B: Business Architecture)
  - 'implementation'  → Gap                    (Phase D: Technology / Migration)
  - 'roadmap'         → Gap with roadmap types  (Phase E: Opportunities & Solutions)

Contract (docs/adm_phases_d_a_g_e_f_h_tasks.json OA-001):
  - No raw SQL
  - No new ORM models — imports only
  - get_unified_gap_register() handles all 3 source tables being empty gracefully
  - Each item has gap_source in ['capability', 'implementation', 'roadmap']
  - severity value comes from DB column, never fabricated
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.models.capability_gap_analysis import CapabilityGapAnalysis
from app.models.implementation_migration import Gap, GAP_KIND_PLATEAU_TRANSITION, Plateau
from app.models.relationship_tables import gap_work_packages

log = logging.getLogger(__name__)

_ROADMAP_GAP_TYPES = frozenset({"roadmap", "modernization", "retirement", "migration"})


def get_unified_gap_register(
    adm_phase: str = None,
    severity: str = None,
    status: str = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Return a unified list of gaps from all three ADM source tables.

    Args:
        adm_phase:  Optional TOGAF ADM phase filter (e.g. 'B', 'C', 'D').
        severity:   Optional severity filter (low/medium/high/critical).
        status:     Optional status filter (varies by source).
        limit:      Maximum total rows returned per source (default 500).

    Returns:
        List of dicts each containing:
          gap_source, gap_id, title, severity, status, adm_phase, description
        Returns empty list if all source tables are empty or an error occurs.
    """
    results: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Source 1: capability gaps — Phase B Business Architecture
    # ------------------------------------------------------------------
    try:
        q = db.session.query(CapabilityGapAnalysis)
        if severity:
            q = q.filter(CapabilityGapAnalysis.gap_severity == severity)
        if status:
            q = q.filter(CapabilityGapAnalysis.status == status)
        rows = q.limit(limit).all()
        for row in rows:
            results.append({
                "gap_source": "capability",
                "gap_id": row.id,
                "title": (
                    getattr(row, "gap_title", None)
                    or getattr(row, "title", None)
                    or f"CAP-GAP-{row.id}"
                ),
                "severity": row.gap_severity,
                "status": getattr(row, "status", None),
                "adm_phase": "B",
                "description": getattr(row, "gap_description", None),
                "capability_id": getattr(row, "capability_id", None),
            })
    except Exception as exc:
        log.warning("gap_register: capability source unavailable: %s", exc)

    # ------------------------------------------------------------------
    # Source 2: implementation / technology gaps — Phase D
    # ------------------------------------------------------------------
    try:
        q = db.session.query(Gap).filter(Gap.gap_kind != GAP_KIND_PLATEAU_TRANSITION)
        if severity:
            q = q.filter(Gap.severity == severity)
        if status:
            q = q.filter(Gap.resolution_status == status)
        if adm_phase:
            q = q.filter(Gap.togaf_phase == adm_phase)
        rows = q.limit(limit).all()
        for row in rows:
            results.append({
                "gap_source": "implementation",
                "gap_id": row.id,
                "title": getattr(row, "title", None) or f"IMPL-GAP-{row.id}",
                "severity": row.severity,
                "status": row.resolution_status,
                "adm_phase": getattr(row, "togaf_phase", None) or "D",
                "description": row.description,
                "gap_type": row.gap_type,
            })
    except Exception as exc:
        log.warning("gap_register: implementation source unavailable: %s", exc)

    # ------------------------------------------------------------------
    # Source 3: roadmap / opportunities gaps — Phase E
    # Subset of Gap rows with roadmap-oriented gap_type values.
    # ------------------------------------------------------------------
    try:
        q = db.session.query(Gap).filter(
            Gap.gap_type.in_(_ROADMAP_GAP_TYPES),
            Gap.gap_kind != GAP_KIND_PLATEAU_TRANSITION
        )
        if severity:
            q = q.filter(Gap.severity == severity)
        rows = q.limit(limit).all()
        for row in rows:
            results.append({
                "gap_source": "roadmap",
                "gap_id": row.id,
                "title": getattr(row, "title", None) or f"ROAD-GAP-{row.id}",
                "severity": row.severity,
                "status": row.resolution_status,
                "adm_phase": "E",
                "description": row.description,
                "gap_type": row.gap_type,
            })
    except Exception as exc:
        log.warning("gap_register: roadmap source unavailable: %s", exc)

    return results


def gap_summary_by_phase() -> Dict[str, Any]:
    """Return a count summary of gaps grouped by source and severity.

    Returns:
        Dict with keys matching each gap_source; each value is a dict of
        severity → count.  All values come from DB columns; none fabricated.
    """
    summary: Dict[str, Any] = {
        "capability": {},
        "implementation": {},
        "roadmap": {},
        "total": 0,
    }

    try:
        rows = (
            db.session.query(
                CapabilityGapAnalysis.gap_severity,
                db.func.count(CapabilityGapAnalysis.id),
            )
            .group_by(CapabilityGapAnalysis.gap_severity)
            .all()
        )
        for sev, cnt in rows:
            summary["capability"][sev or "unknown"] = cnt
            summary["total"] += cnt
    except Exception as exc:
        log.warning("gap_register: capability summary unavailable: %s", exc)

    try:
        rows = (
            db.session.query(Gap.severity, db.func.count(Gap.id))
            .filter(Gap.gap_kind != GAP_KIND_PLATEAU_TRANSITION)
            .group_by(Gap.severity)
            .all()
        )
        for sev, cnt in rows:
            summary["implementation"][sev or "unknown"] = cnt
            summary["total"] += cnt
    except Exception as exc:
        log.warning("gap_register: implementation summary unavailable: %s", exc)

    try:
        rows = (
            db.session.query(Gap.severity, db.func.count(Gap.id))
            .filter(
                Gap.gap_type.in_(_ROADMAP_GAP_TYPES),
                Gap.gap_kind != GAP_KIND_PLATEAU_TRANSITION
            )
            .group_by(Gap.severity)
            .all()
        )
        for sev, cnt in rows:
            summary["roadmap"][sev or "unknown"] = cnt
    except Exception as exc:
        log.warning("gap_register: roadmap summary unavailable: %s", exc)

    return summary


def create_gap(
    organization_id: int,
    name: str,
    *,
    description: Optional[str] = None,
    gap_type: Optional[str] = None,
    priority: str = "medium",
    severity: str = "medium",
    resolution_status: str = "identified",
    architecture_id: Optional[int] = None,
    current_state_ref: Optional[str] = None,
    target_state_ref: Optional[str] = None,
    auto_generated: bool = False,
    generation_source: Optional[str] = None,
) -> Tuple[Gap, bool]:
    """Create a gap in the one gap register (`gaps`, this module's canonical
    store), refusing a duplicate within the organisation.

    A duplicate is an existing gap in the same organisation with the same
    name and, when given, the same architecture_id -- the scope a re-run of
    gap discovery or a repeated manual "create gap" call must not
    grow the register by re-saving what is already there.

    Flushes but does not commit: the caller controls the transaction
    boundary, so a batch (gap discovery over several findings) commits once.

    Returns (gap, created). created is False when an existing gap was
    returned instead of a new one being inserted.
    """
    if not organization_id:
        raise ValueError("create_gap requires an organization_id")
    name = (name or "").strip()
    if not name:
        raise ValueError("create_gap requires a name")

    query = Gap.query.filter(Gap.organization_id == organization_id, Gap.name == name)
    if architecture_id is not None:
        query = query.filter(Gap.architecture_id == architecture_id)
    else:
        query = query.filter(Gap.architecture_id.is_(None))
    existing = query.first()
    if existing is not None:
        return existing, False

    gap = Gap(
        organization_id=organization_id,
        name=name,
        description=description or "",
        gap_type=gap_type,
        priority=priority,
        severity=severity,
        resolution_status=resolution_status,
        architecture_id=architecture_id,
        current_state_ref=current_state_ref,
        target_state_ref=target_state_ref,
        auto_generated=auto_generated,
        generation_source=generation_source,
    )
    db.session.add(gap)
    db.session.flush()
    return gap, True


def link_gap_to_plateau(
    gap_id: int, plateau_id: int, organization_id: int
) -> Gap:
    """Link a gap to a plateau within the same organisation.

    Both the gap and the plateau must belong to *organization_id*.
    Raises ValueError if either does not, or if the link already exists.

    Flushes but does not commit: the caller controls the transaction boundary.
    """
    gap = Gap.query.filter_by(id=gap_id, organization_id=organization_id).first()
    if gap is None:
        raise ValueError("Gap not found in this organisation")

    plateau = Plateau.query.filter_by(id=plateau_id, organization_id=organization_id).first()
    if plateau is None:
        raise ValueError("Plateau not found in this organisation")

    if plateau in gap.plateaus:
        raise ValueError("Gap is already linked to this plateau")

    gap.plateaus.append(plateau)
    db.session.flush()
    return gap


def unlink_gap_from_plateau(
    gap_id: int, plateau_id: int, organization_id: int
) -> Gap:
    """Remove the link between a gap and a plateau within the same organisation.

    Raises ValueError if either the gap or the plateau does not belong to
    the organisation, or if no link exists.
    """
    gap = Gap.query.filter_by(id=gap_id, organization_id=organization_id).first()
    if gap is None:
        raise ValueError("Gap not found in this organisation")

    plateau = Plateau.query.filter_by(id=plateau_id, organization_id=organization_id).first()
    if plateau is None:
        raise ValueError("Plateau not found in this organisation")

    if plateau not in gap.plateaus:
        raise ValueError("Gap is not linked to this plateau")

    gap.plateaus.remove(plateau)
    db.session.flush()
    return gap


def get_plateau_gaps(plateau_id: int, organization_id: int) -> List[Gap]:
    """Return all gaps linked to a plateau within the organisation.

    Raises ValueError if the plateau does not belong to the organisation.
    """
    plateau = Plateau.query.filter_by(id=plateau_id, organization_id=organization_id).first()
    if plateau is None:
        raise ValueError("Plateau not found in this organisation")
    return list(plateau.gaps)


def get_gaps_not_addressed(organization_id: int) -> List[Gap]:
    """Return gaps that have no work package addressing them.

    A gap is "addressed" when it has at least one entry in the
    gap_work_packages junction table.  This queries the unified work
    package store (WorkPackage model) through the existing many-to-many
    relationship.
    """
    from app.models.implementation_migration import WorkPackage

    subq = (
        db.session.query(gap_work_packages.c.gap_id)
        .join(WorkPackage, gap_work_packages.c.work_package_id == WorkPackage.id)
        .filter(WorkPackage.organization_id == organization_id)
        .subquery()
    )
    return (
        Gap.query
        .filter(
            Gap.organization_id == organization_id,
            Gap.gap_kind != GAP_KIND_PLATEAU_TRANSITION,
            ~Gap.id.in_(db.session.query(subq.c.gap_id)),
        )
        .all()
    )


def count_gaps(organization_id: int) -> int:
    """Return the number of gaps in the one gap register for an organisation.

    This is the single canonical count used by both the roadmap and the
    gap analysis screen, so they always agree.
    """
    return (
        Gap.query
        .filter(
            Gap.organization_id == organization_id,
            Gap.gap_kind != GAP_KIND_PLATEAU_TRANSITION,
        )
        .count()
    )
