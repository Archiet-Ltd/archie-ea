"""Deterministic model-drift / health detector for the enterprise genome (ADR 0009).

ADR 0009's thesis: a model is accurate the week it is built and then rots as
reality moves under it. The labour worth automating is *noticing* that the model
no longer matches reality — then proposing a governed correction, never a silent
auto-edit.

This module is the "notice" half. ``detect_model_drift(org_id, session)`` scans
the genome for ONE organization and returns a validated, ``spec_hash``-ed report
of typed drift findings. It is:

  * DETERMINISTIC and zero-LLM — identical input yields byte-identical output
    (same ``spec_hash``), like ``coverage_slice.build_coverage_slice``.
  * PROVENANCE-CARRYING — every finding names the real ``archimate_element_id``(s)
    it is about, so the UI can link straight to the element(s). A finding that
    cannot be traced to a real element is OMITTED, never fabricated (CLAUDE.md:
    never invent data). Where a signal is computed from a business store whose
    row lacks an ArchiMate link, the row is counted under
    ``skipped_no_provenance`` rather than reported without provenance.

It detects five real drift/staleness signals (each documented at its detector):

  a. orphaned_element                        — an element wired to nothing
  b. capability_without_application_support  — a coverage gap
  c. decommissioned_application_still_mapped — reality drift
  d. motivation_without_realization          — an ungrounded driver/goal/requirement
  e. near_duplicate_elements                 — internal inconsistency (fuzzy)

Honest limits (see the module-level ``UNCOMPUTABLE_SIGNALS`` note and the ADR):
``ArchiMateElement`` carries NO created/updated timestamp in this schema, so the
ADR's headline "model age" (time since each element was last confirmed) is NOT
computable here and is deliberately not reported rather than approximated.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

REPORT_VERSION = "1.0.0"

# Finding type keys — stable identifiers the emitter, route and tests key off.
FINDING_ORPHANED = "orphaned_element"
FINDING_CAP_NO_SUPPORT = "capability_without_application_support"
FINDING_DECOMM_MAPPED = "decommissioned_application_still_mapped"
FINDING_MOTIVATION_NO_REALIZATION = "motivation_without_realization"
FINDING_NEAR_DUPLICATE = "near_duplicate_elements"

# The five signals this detector computes, in report order.
SIGNAL_ORDER = [
    FINDING_ORPHANED,
    FINDING_CAP_NO_SUPPORT,
    FINDING_DECOMM_MAPPED,
    FINDING_MOTIVATION_NO_REALIZATION,
    FINDING_NEAR_DUPLICATE,
]

# Signals a best-in-class detector would want but this schema cannot support, so
# they are honestly declared unavailable rather than approximated with a made-up
# number. Surfaced in the report so the omission is explicit, not silent.
UNCOMPUTABLE_SIGNALS = {
    "model_age": (
        "ArchiMateElement has no created_at/updated_at column in this schema, so "
        "time-since-last-confirmed (ADR 0009's 'model age') cannot be measured "
        "deterministically. Omitted rather than fabricated."
    ),
}

# Severity per finding type. Fixed (not computed) so output is deterministic.
_SEVERITY = {
    FINDING_ORPHANED: "medium",
    FINDING_CAP_NO_SUPPORT: "high",
    FINDING_DECOMM_MAPPED: "high",
    FINDING_MOTIVATION_NO_REALIZATION: "medium",
    FINDING_NEAR_DUPLICATE: "low",
}

# Motivation types whose downward realization we check (ADR 0009 names these).
_MOTIVATION_TYPES = {"driver", "goal", "requirement"}

# Application lifecycle values that mean "on the way out" — a mapping claiming
# such an app still actively supports a capability is reality drift.
# (application_portfolio.ApplicationComponent.lifecycle_status vocabulary.)
_RETIRING_LIFECYCLE = {"deprecated", "retired", "decommissioned", "sunset"}

# Fuzzy name-similarity at/above which two same-layer elements are flagged as a
# near-duplicate cluster. Reuses the platform's repaired fuzzy path. 0.6 is
# stricter than grounding's 0.5 add-time warning: a standing cluster should be a
# confident lexical match, not merely a token overlap.
NEAR_DUPLICATE_THRESHOLD = 0.6


def compute_blocking_key(name: str) -> str:
    """Cheap blocking key for near-duplicate candidate grouping.

    Groups elements by the first token of their normalized name so that only
    elements within the same group need to be compared pairwise. This reduces
    the O(n²) comparison space to O(groups × avg_group_size²).

    Exposed as a public function so the matcher can reuse the same key
    for consistent grouping across the genome pipeline.

    Args:
        name: the element name (may be None or empty).

    Returns:
        A lowercase first-token string, or the empty string for a blank name.
        Deterministic: same name always yields the same key.
    """
    if not name or not name.strip():
        return ""
    return (name.strip().split()[0] or "").lower()


def detect_model_drift(org_id: int, session=None) -> Dict[str, Any]:
    """Scan the genome for one org and return a spec-hashed drift report.

    Args:
        org_id: the organization (tenant) to scan. Passed explicitly so the
            detector is correct outside a request context too (CLI/scheduler/
            tests have no ``g.current_org_id`` and are otherwise unfiltered).
        session: optional SQLAlchemy session; defaults to ``db.session``.

    Returns:
        {
          "report_version": "1.0.0",
          "organization_id": int,
          "signals_scanned": [signal keys...],
          "uncomputable_signals": {key: reason, ...},
          "findings": [ finding dict, ... ],   # deterministically ordered
          "summary": {"total": int, "by_type": {...}, "by_severity": {...},
                      "skipped_no_provenance": {...}},
          "spec_hash": "sha256:...",
        }
    """
    if session is None:
        from app.extensions import db

        session = db.session

    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    # --- Load this org's elements and relationships once, deterministically. ---
    elements = (
        session.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == org_id)
        .filter(ArchiMateElement.deleted_at.is_(None))
        .order_by(ArchiMateElement.id.asc())
        .all()
    )
    rels = (
        session.query(ArchiMateRelationship)
        .filter(ArchiMateRelationship.organization_id == org_id)
        .order_by(ArchiMateRelationship.id.asc())
        .all()
    )

    findings: List[Dict[str, Any]] = []
    skipped: Dict[str, int] = {}

    findings.extend(_detect_orphaned_elements(org_id, elements, rels, session))
    caps_gap, caps_skipped = _detect_capabilities_without_support(org_id, session)
    findings.extend(caps_gap)
    if caps_skipped:
        skipped[FINDING_CAP_NO_SUPPORT] = caps_skipped
    decomm, decomm_skipped = _detect_decommissioned_still_mapped(org_id, session)
    findings.extend(decomm)
    if decomm_skipped:
        skipped[FINDING_DECOMM_MAPPED] = decomm_skipped
    findings.extend(_detect_motivation_without_realization(elements, rels))
    findings.extend(_detect_near_duplicates(elements))

    # Deterministic order: by signal position, then by the finding's primary
    # element id, then by a stable within-type key.
    signal_pos = {k: i for i, k in enumerate(SIGNAL_ORDER)}
    findings.sort(key=lambda f: (signal_pos.get(f["type"], 99), f["_sort_key"]))
    for f in findings:
        f.pop("_sort_key", None)

    by_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for f in findings:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    report = {
        "report_version": REPORT_VERSION,
        "organization_id": org_id,
        "signals_scanned": list(SIGNAL_ORDER),
        "uncomputable_signals": dict(UNCOMPUTABLE_SIGNALS),
        "findings": findings,
        "summary": {
            "total": len(findings),
            "by_type": by_type,
            "by_severity": by_severity,
            "skipped_no_provenance": skipped,
        },
    }
    report["spec_hash"] = _spec_hash(report)
    return report


# --------------------------------------------------------------------------- #
# (a) Orphaned elements                                                        #
# --------------------------------------------------------------------------- #
def _detect_orphaned_elements(org_id, elements, rels, session) -> List[Dict[str, Any]]:
    """Elements wired to NOTHING: no relationship (either end) and no mapping.

    Computed as: element id that is neither a ``source_id`` nor a ``target_id`` of
    any relationship in the org, AND is not referenced as the ``archimate_element_id``
    of any business capability or application component (so a bare, unmapped
    catalogue element is not mistaken for a live one). Provenance is the element's
    own id — always resolvable.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    wired = set()
    for r in rels:
        if r.source_id is not None:
            wired.add(r.source_id)
        if r.target_id is not None:
            wired.add(r.target_id)

    mapped_element_ids = set()
    for (eid,) in (
        session.query(BusinessCapability.archimate_element_id)
        .filter(BusinessCapability.organization_id == org_id)
        .filter(BusinessCapability.archimate_element_id.isnot(None))
        .all()
    ):
        mapped_element_ids.add(eid)
    for (eid,) in (
        session.query(ApplicationComponent.archimate_element_id)
        .filter(ApplicationComponent.organization_id == org_id)
        .filter(ApplicationComponent.archimate_element_id.isnot(None))
        .all()
    ):
        mapped_element_ids.add(eid)

    out = []
    for e in elements:
        if e.id in wired or e.id in mapped_element_ids:
            continue
        out.append(
            {
                "type": FINDING_ORPHANED,
                "severity": _SEVERITY[FINDING_ORPHANED],
                "elements": [_elem_ref(e, role="orphan")],
                "why": (
                    f"{e.type or 'Element'} '{e.name}' participates in no ArchiMate "
                    f"relationship and is not linked from any capability or "
                    f"application — it is disconnected from the model."
                ),
                "remediation": {
                    "available": True,
                    "operation": "modify",
                    "target_element_id": e.id,
                    "hint": "Propose flagging the orphan for review/retirement.",
                },
                "_sort_key": e.id,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# (b) Capabilities with zero application support                              #
# --------------------------------------------------------------------------- #
def _detect_capabilities_without_support(org_id, session):
    """Capabilities with NO application-capability mapping (a coverage gap).

    A capability nobody's application supports is a real hole in the estate.
    Provenance is the capability's ``archimate_element_id``; a capability with no
    element link cannot be provenance-stamped, so it is COUNTED under
    ``skipped_no_provenance`` and not reported (never fabricated).
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability

    caps = (
        session.query(BusinessCapability)
        .filter(BusinessCapability.organization_id == org_id)
        .order_by(BusinessCapability.id.asc())
        .all()
    )
    supported_cap_ids = {
        cid
        for (cid,) in session.query(
            ApplicationCapabilityMapping.business_capability_id
        )
        .filter(ApplicationCapabilityMapping.organization_id == org_id)
        .all()
    }

    out = []
    skipped = 0
    for c in caps:
        if c.id in supported_cap_ids:
            continue
        if c.archimate_element_id is None:
            skipped += 1
            continue
        out.append(
            {
                "type": FINDING_CAP_NO_SUPPORT,
                "severity": _SEVERITY[FINDING_CAP_NO_SUPPORT],
                "elements": [
                    {
                        "archimate_element_id": c.archimate_element_id,
                        "name": c.name,
                        "archimate_type": "Capability",
                        "layer": "strategy",
                        "role": "unsupported_capability",
                        "source_row": {"table": "business_capability", "id": c.id},
                    }
                ],
                "why": (
                    f"Capability '{c.name}' has no application-capability mapping — "
                    f"no application in the portfolio is recorded as supporting it."
                ),
                "remediation": {
                    "available": False,
                    "operation": None,
                    "target_element_id": c.archimate_element_id,
                    "hint": (
                        "Closing this gap means mapping an application to the "
                        "capability, not editing the genome element — no single-"
                        "element patch fixes it."
                    ),
                },
                "_sort_key": c.archimate_element_id,
            }
        )
    return out, skipped


# --------------------------------------------------------------------------- #
# (c) Decommissioned application still mapped as active support               #
# --------------------------------------------------------------------------- #
def _detect_decommissioned_still_mapped(org_id, session):
    """Retiring/decommissioned applications still mapped to a capability.

    Reality drift: the estate says the app is on its way out, but the model still
    claims it supports a capability. Provenance is the application's
    ``archimate_element_id`` (plus the capability's where linked). An app with no
    element link is counted under ``skipped_no_provenance``.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    apps = (
        session.query(ApplicationComponent)
        .filter(ApplicationComponent.organization_id == org_id)
        .order_by(ApplicationComponent.id.asc())
        .all()
    )
    retiring = {
        a.id: a
        for a in apps
        if (a.lifecycle_status or "").strip().lower() in _RETIRING_LIFECYCLE
    }
    if not retiring:
        return [], 0

    cap_name_by_id = {
        c.id: c
        for c in session.query(BusinessCapability)
        .filter(BusinessCapability.organization_id == org_id)
        .all()
    }

    mappings = (
        session.query(ApplicationCapabilityMapping)
        .filter(ApplicationCapabilityMapping.organization_id == org_id)
        .filter(
            ApplicationCapabilityMapping.application_component_id.in_(list(retiring))
        )
        .order_by(ApplicationCapabilityMapping.id.asc())
        .all()
    )

    out = []
    skipped = 0
    seen = set()
    for m in mappings:
        app = retiring.get(m.application_component_id)
        if app is None:
            continue
        if app.archimate_element_id is None:
            if m.application_component_id not in seen:
                skipped += 1
                seen.add(m.application_component_id)
            continue
        cap = cap_name_by_id.get(m.business_capability_id)
        elements = [
            {
                "archimate_element_id": app.archimate_element_id,
                "name": app.name,
                "archimate_type": "ApplicationComponent",
                "layer": "application",
                "role": "retiring_application",
                "source_row": {"table": "application_components", "id": app.id},
                "lifecycle_status": app.lifecycle_status,
            }
        ]
        if cap is not None and cap.archimate_element_id is not None:
            elements.append(
                {
                    "archimate_element_id": cap.archimate_element_id,
                    "name": cap.name,
                    "archimate_type": "Capability",
                    "layer": "strategy",
                    "role": "supported_capability",
                    "source_row": {"table": "business_capability", "id": cap.id},
                }
            )
        cap_label = f"'{cap.name}'" if cap is not None else "a capability"
        out.append(
            {
                "type": FINDING_DECOMM_MAPPED,
                "severity": _SEVERITY[FINDING_DECOMM_MAPPED],
                "elements": elements,
                "why": (
                    f"Application '{app.name}' is {app.lifecycle_status} yet is still "
                    f"mapped as supporting {cap_label} — the model claims live support "
                    f"from an application reality is retiring."
                ),
                "remediation": {
                    "available": True,
                    "operation": "modify",
                    "target_element_id": app.archimate_element_id,
                    "hint": (
                        "Propose recording the application's retiring lifecycle on "
                        "its genome element so the model matches reality."
                    ),
                },
                "_sort_key": (app.archimate_element_id, m.id),
            }
        )
    return out, skipped


# --------------------------------------------------------------------------- #
# (d) Motivation elements with no realization downward                        #
# --------------------------------------------------------------------------- #
def _detect_motivation_without_realization(elements, rels):
    """Driver/Goal/Requirement elements that nothing realizes.

    In ArchiMate, motivation elements sit at the top and are realized/served/
    influenced by elements below them — those relationships point AT the
    motivation element (it is the ``target_id``). A motivation element that is the
    target of no relationship has nothing beneath it carrying it into the estate:
    an aspiration with no implementation. Provenance is the element's own id.

    Computed structurally (element is in no relationship's ``target_id`` set); no
    semantic inference beyond that, so the signal is deterministic and honest
    about what it means.
    """
    realized_targets = {r.target_id for r in rels if r.target_id is not None}
    out = []
    for e in elements:
        if (e.type or "").strip().lower() not in _MOTIVATION_TYPES:
            continue
        if e.id in realized_targets:
            continue
        out.append(
            {
                "type": FINDING_MOTIVATION_NO_REALIZATION,
                "severity": _SEVERITY[FINDING_MOTIVATION_NO_REALIZATION],
                "elements": [_elem_ref(e, role="unrealized_motivation")],
                "why": (
                    f"{e.type} '{e.name}' is the target of no relationship — nothing "
                    f"in a lower layer realizes, serves or influences it, so it is an "
                    f"aspiration with no implementation recorded against it."
                ),
                "remediation": {
                    "available": False,
                    "operation": None,
                    "target_element_id": e.id,
                    "hint": (
                        "Grounding this means adding a realizing relationship from a "
                        "core element, not editing this element alone."
                    ),
                },
                "_sort_key": e.id,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# (e) Near-duplicate element clusters                                          #
# --------------------------------------------------------------------------- #
def _detect_near_duplicates(elements):
    """Same-layer elements whose names are fuzzy-duplicate of each other.

    Silent near-duplicate proliferation is how a system of record rots. Reuses the
    platform's repaired fuzzy path (``DuplicateDetectionUtils.is_duplicate`` in
    ``mode='fuzzy'``). Compares only within the same layer AND the same blocking
    key (first token of the normalized name), so an application and a capability
    sharing a name is legitimate, and elements whose names start with different
    words are never compared — the blocking key reduces the O(n²) comparison
    space to O(groups × avg_group_size²). Deterministic: elements are already
    id-ordered, pairs are formed lower-id-first, and each element joins at most
    one cluster (its first match), so clustering is order-stable.
    """
    from app.modules.duplicate_detection.services.duplicate_detection_utils import (
        DuplicateDetectionUtils,
    )

    # Group by (layer, blocking_key) — two-level grouping so only elements in
    # the same layer AND with the same first token are compared pairwise.
    by_block: Dict[tuple, List[Any]] = {}
    for e in elements:
        if not (e.name or "").strip():
            continue
        key = (e.layer, compute_blocking_key(e.name))
        by_block.setdefault(key, []).append(e)

    out = []
    # Sort keys for deterministic output: layer first (None last), then token.
    for (layer, _token), group in sorted(
        by_block.items(), key=lambda kv: (kv[0][0] is None, str(kv[0][0]), kv[0][1])
    ):
        assigned = set()
        for i, a in enumerate(group):
            if a.id in assigned:
                continue
            cluster = [a]
            best_score = 0.0
            for b in group[i + 1 :]:
                if b.id in assigned:
                    continue
                is_near, score = DuplicateDetectionUtils.is_duplicate(
                    a.name, b.name, mode="fuzzy", threshold=NEAR_DUPLICATE_THRESHOLD
                )
                if is_near:
                    cluster.append(b)
                    assigned.add(b.id)
                    best_score = max(best_score, score)
            if len(cluster) > 1:
                assigned.add(a.id)
                names = ", ".join(f"'{c.name}'" for c in cluster)
                out.append(
                    {
                        "type": FINDING_NEAR_DUPLICATE,
                        "severity": _SEVERITY[FINDING_NEAR_DUPLICATE],
                        "elements": [
                            _elem_ref(c, role="near_duplicate") for c in cluster
                        ],
                        "why": (
                            f"{len(cluster)} elements in the {layer or 'unlayered'} "
                            f"layer have near-duplicate names ({names}, up to "
                            f"{int(best_score * 100)}% similar) — likely the same "
                            f"thing modelled more than once."
                        ),
                        "remediation": {
                            "available": False,
                            "operation": None,
                            "target_element_id": cluster[0].id,
                            "hint": (
                                "Resolving a duplicate is a merge decision (which "
                                "element survives), not a single-element edit."
                            ),
                        },
                        "_sort_key": tuple(c.id for c in cluster),
                    }
                )
    return out


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def _elem_ref(e, role: str) -> Dict[str, Any]:
    """Structural provenance ref for an ArchiMateElement (id always present)."""
    return {
        "archimate_element_id": e.id,
        "name": e.name,
        "archimate_type": e.type,
        "layer": e.layer,
        "role": role,
    }


def _spec_hash(report: Dict[str, Any]) -> str:
    """Deterministic sha256 over the report content (excluding the hash itself)."""
    payload = {k: v for k, v in report.items() if k != "spec_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "compute_blocking_key",
    "detect_model_drift",
    "REPORT_VERSION",
    "SIGNAL_ORDER",
    "UNCOMPUTABLE_SIGNALS",
    "FINDING_ORPHANED",
    "FINDING_CAP_NO_SUPPORT",
    "FINDING_DECOMM_MAPPED",
    "FINDING_MOTIVATION_NO_REALIZATION",
    "FINDING_NEAR_DUPLICATE",
]
