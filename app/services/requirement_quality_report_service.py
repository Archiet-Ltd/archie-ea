"""Measure (not fabricate, not fix) how far existing requirements are from the
founder's 20 Nov 2026 ruling that EARS is the mandatory statement form, plus
the agreed ISO/IEC/IEEE 29148 + INCOSE attribute set.

This is report mode only — nothing here writes to a Requirement row or its
mirrored ArchiMate element. It exists to size the backfill backlog before
that backlog becomes a gate (see CLAUDE.md, "Own the decision"). Follows the
measurement shape already used by ARCH-126
(``app/services/traceability_report_service.py``): real computed zeros are
real numbers; only an empty denominator is uncomputable and renders as
``None``.

What is and is not a structured field today (read plainly, not assumed):

- The EARS statement form is NOT stored anywhere. ``Requirement.title`` /
  ``Requirement.description`` are free text; whether that text matches one
  of the five EARS patterns is computed here from the text, every run — it
  is not a column.
- ``Requirement.verification_method`` (inspection / analysis / test /
  demonstration) IS a structured column on the ``requirements`` table. Some
  AI-generation call sites additionally duplicate a copy of it into the
  mirrored ``ArchiMateElement.properties`` JSON blob — this check reads only
  the structured column, which is the one ISO/IEC/IEEE 29148 "verified by"
  attribute actually has a canonical home.
- There is no column literally named ``kind``. The closest structured proxy
  is ``Requirement.type`` (a free string: functional / non_functional /
  constraint / ...), which this check reports against. That is named here so
  a future reader does not go looking for a ``kind`` column that does not
  exist.
- "Traces from a Stakeholder, Driver or Goal" is answered two different ways
  in this codebase, and both are structured: the direct FK columns
  (``Requirement.stakeholder_id`` / ``driver_id`` / ``goal_id``) written by
  the journey flow, or an ``ArchiMateRelationship`` row (type ``influence``
  or ``realization``) connecting the requirement's mirrored
  ``ArchiMateElement`` to a Stakeholder/Driver/Goal element, written by
  ``_create_archimate_relationships``. Per ADR 0008, a requirement satisfying
  either path has a real trace — this check does not require one specific
  path over the other.
- ``status`` is a read-only Python property on ``Requirement`` that proxies
  the mirrored ``ArchiMateElement.status`` column; it is not asked for in
  this report because the task brief does not list it as one of the four
  counted gaps (EARS / verification / trace / kind).
"""

from __future__ import annotations

import re
from typing import TypedDict

# The five EARS patterns named in the brief, matched anywhere in the combined
# title + description text (not anchored to the start), case-insensitive.
_EARS_PATTERNS = [
    re.compile(r"\bwhen\b.*?\bshall\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bwhile\b.*?\bshall\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bif\b.*?\bthen\b.*?\bshall\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bwhere\b.*?\bshall\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bthe\s+system\s+shall\b", re.IGNORECASE | re.DOTALL),
]

_TRACE_ELEMENT_TYPES = ("Stakeholder", "Driver", "Goal")
_TRACE_RELATIONSHIP_TYPES = ("influence", "realization")


class RequirementQualityReport(TypedDict):
    organization_id: int | None
    total: int
    not_ears_count: int
    not_ears_names: list[str]
    missing_verification_count: int
    missing_verification_names: list[str]
    missing_trace_count: int
    missing_trace_names: list[str]
    missing_kind_count: int
    missing_kind_names: list[str]
    non_compliant_count: int
    non_compliant_names: list[str]


def is_ears_compliant(statement_text: str | None) -> bool:
    """True if the text matches one of the five EARS patterns named in the
    founder's 20 Nov 2026 ruling. An empty/None statement is not compliant —
    there is nothing to match."""
    if not statement_text:
        return False
    return any(pattern.search(statement_text) for pattern in _EARS_PATTERNS)


def _display_name(requirement) -> str:
    return requirement.title or requirement.category or f"Requirement #{requirement.id}"


def _has_structured_trace(requirement, traced_element_ids: set[int]) -> bool:
    """True if the requirement traces from a Stakeholder, Driver or Goal via
    either structured path this codebase actually uses (see module docstring):
    the direct FK columns, or an ArchiMateRelationship row reaching one of
    those element types from the requirement's mirrored ArchiMate element."""
    if requirement.stakeholder_id or requirement.driver_id or requirement.goal_id:
        return True
    if requirement.archimate_element_id and requirement.archimate_element_id in traced_element_ids:
        return True
    return False


def _traced_archimate_element_ids(organization_id: int | None) -> set[int]:
    """Element ids that are a relationship-endpoint away from a Stakeholder,
    Driver or Goal element, via one of the two relationship types the
    journey flow actually writes (``influence``, ``realization``)."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    trace_element_query = ArchiMateElement.query.filter(
        ArchiMateElement.type.in_(_TRACE_ELEMENT_TYPES)
    )
    if organization_id is not None:
        trace_element_query = trace_element_query.filter(
            ArchiMateElement.organization_id == organization_id
        )
    trace_element_ids = {row.id for row in trace_element_query.all()}
    if not trace_element_ids:
        return set()

    rel_query = ArchiMateRelationship.query.filter(
        ArchiMateRelationship.type.in_(_TRACE_RELATIONSHIP_TYPES)
    )
    if organization_id is not None:
        rel_query = rel_query.filter(ArchiMateRelationship.organization_id == organization_id)

    traced_ids: set[int] = set()
    for rel in rel_query.all():
        if rel.source_id in trace_element_ids and rel.target_id is not None:
            traced_ids.add(rel.target_id)
        if rel.target_id in trace_element_ids and rel.source_id is not None:
            traced_ids.add(rel.source_id)
    return traced_ids


def compute_requirement_quality_report(organization_id: int | None = None) -> RequirementQualityReport:
    """Classify every Requirement row (optionally scoped to one organisation)
    against the founder's EARS-mandatory ruling plus the agreed ISO/IEC/IEEE
    29148 + INCOSE attributes this codebase can actually express.

    Changes nothing. Run outside a request context (this module's CLI command
    included), so there is no ``g.current_org_id`` — the organisation is put
    in the predicate explicitly, the same convention the backfill commands
    use, rather than trusted to tenant-isolation middleware that is a no-op
    here.
    """
    from app.models.models import Requirement

    query = Requirement.query
    if organization_id is not None:
        query = query.filter(Requirement.organization_id == organization_id)
    requirements = query.all()

    traced_element_ids = _traced_archimate_element_ids(organization_id)

    not_ears_names: list[str] = []
    missing_verification_names: list[str] = []
    missing_trace_names: list[str] = []
    missing_kind_names: list[str] = []
    non_compliant_names: list[str] = []

    for req in requirements:
        name = _display_name(req)
        statement_text = " ".join(part for part in (req.title, req.description) if part)

        ears_ok = is_ears_compliant(statement_text)
        verification_ok = bool(req.verification_method)
        trace_ok = _has_structured_trace(req, traced_element_ids)
        kind_ok = bool(req.type)

        if not ears_ok:
            not_ears_names.append(name)
        if not verification_ok:
            missing_verification_names.append(name)
        if not trace_ok:
            missing_trace_names.append(name)
        if not kind_ok:
            missing_kind_names.append(name)
        if not (ears_ok and verification_ok and trace_ok and kind_ok):
            non_compliant_names.append(name)

    return {
        "organization_id": organization_id,
        "total": len(requirements),
        "not_ears_count": len(not_ears_names),
        "not_ears_names": not_ears_names,
        "missing_verification_count": len(missing_verification_names),
        "missing_verification_names": missing_verification_names,
        "missing_trace_count": len(missing_trace_names),
        "missing_trace_names": missing_trace_names,
        "missing_kind_count": len(missing_kind_names),
        "missing_kind_names": missing_kind_names,
        "non_compliant_count": len(non_compliant_names),
        "non_compliant_names": non_compliant_names,
    }
