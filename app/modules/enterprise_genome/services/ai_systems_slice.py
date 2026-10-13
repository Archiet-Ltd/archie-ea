"""Deterministic AI-Systems genome slice (PILLAR 6).

``build_ai_systems_slice(org_id, session)`` reads the org's AI-system
``ArchiMateElement`` rows and projects them into a stable, hashable slice:

  * **structural provenance** — every projected system carries its
    ``archimate_element_id``; nothing in the slice exists without a real modelled
    element behind it.
  * **spec_hash** — a content hash over the ordered, normalised systems, so an
    unchanged estate rebuilds byte-for-byte identically and any drift is visible.
  * **no fabrication** — an unrecorded field is ``"unknown"`` (see
    ``ai_system_profile``); the builder never fills a plausible default.

Pure and deterministic: no LLM, no clock, no network. The ``generated_at`` field
is intentionally *not* part of the slice payload or the hash — a timestamp would
make every rebuild differ. Callers that want a timestamp add it around the slice.
"""

from __future__ import annotations

import hashlib
import json

from .ai_system_profile import (
    AI_SYSTEM_ELEMENT_LAYER,
    AI_SYSTEM_ELEMENT_TYPE,
    AI_SYSTEM_MARKER,
    profile_from_element,
)

SLICE_KIND = "ai_systems"
SLICE_VERSION = 1


def _iter_ai_system_elements(org_id: int, session):
    """AI-system elements for one org, deterministically ordered.

    Scoped explicitly by ``organization_id`` — this builder is called from
    request context (scoped by the tenant middleware anyway) AND from CLI/tests
    (which are unfiltered), so the predicate is defence that holds in both.
    Filtering the ``ai_system`` marker is done in Python because
    ``custom_properties`` is JSON and portable JSON containment is awkward; the
    element set per org is small.
    """
    from app.models.models import ArchiMateElement

    q = (
        session.query(ArchiMateElement)
        .filter(ArchiMateElement.organization_id == org_id)
        .filter(ArchiMateElement.type == AI_SYSTEM_ELEMENT_TYPE)
        .order_by(ArchiMateElement.id.asc())
    )
    for el in q:
        props = el.custom_properties or {}
        if isinstance(props, dict) and AI_SYSTEM_MARKER in props:
            yield el


def _spec_hash(systems: list[dict]) -> str:
    """Content hash over the ordered, normalised systems.

    Excludes nothing derived (currency, risk flags stay in) so the hash pins the
    whole projection, but excludes any ambient value (timestamps): the input is
    only the systems list, canonically JSON-encoded with sorted keys.
    """
    payload = json.dumps(systems, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_ai_systems_slice(org_id: int, session) -> dict:
    """Build the deterministic AI-systems slice for *org_id*.

    Returns a dict:
        {
          "kind": "ai_systems",
          "version": 1,
          "organization_id": org_id,
          "systems": [ <profile>, ... ],   # ordered by archimate_element_id
          "counts": { total, retired, stale, current, unknown_model, flagged,
                      high_risk, released },
          "spec_hash": "sha256:...",
        }
    """
    systems = [profile_from_element(el) for el in _iter_ai_system_elements(org_id, session)]
    # profiles already come out in element-id order from the query; make the
    # ordering explicit so the hash never depends on query-plan quirks.
    systems.sort(key=lambda s: s["archimate_element_id"])

    counts = {
        "total": len(systems),
        "current": sum(1 for s in systems if s["model_currency"] == "current"),
        "stale": sum(1 for s in systems if s["model_currency"] == "stale"),
        "retired": sum(1 for s in systems if s["model_currency"] == "retired"),
        "unknown_model": sum(1 for s in systems if s["model_currency"] == "unknown"),
        "flagged": sum(1 for s in systems if s["risk_flags"]),
        "high_risk": sum(1 for s in systems if s.get("risk_class") == "high"),
        "released": sum(1 for s in systems if s.get("release_state") == "released"),
    }

    return {
        "kind": SLICE_KIND,
        "version": SLICE_VERSION,
        "organization_id": org_id,
        "systems": systems,
        "counts": counts,
        "spec_hash": _spec_hash(systems),
        "provenance": {
            "source": "archimate_elements",
            "element_type": AI_SYSTEM_ELEMENT_TYPE,
            "element_layer": AI_SYSTEM_ELEMENT_LAYER,
            "marker": AI_SYSTEM_MARKER,
        },
    }
