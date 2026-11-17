"""Element profile service — one reader and one writer for specialisation kind and typed properties.

A profile is a framework-specific kind (e.g. SAFe Epic, Feature) stored as a
property on an ArchiMate element, alongside typed properties (e.g. WSJF parts).
The kind is stored under the exporter's specialisation property name so it
survives export and import.

Reuse:
- The element property column (``acm_properties``) is the store, through the
  existing typed property writer.
- The WSJF score function is shared with ``app/services/wsjf_service.py``.
- The exporter's specialisation property name is ``SPECIALIZATION_PROPERTY``
  from ``app/models/archimate_element_types.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import db
from app.models.archimate_element_types import SPECIALIZATION_PROPERTY
from app.services.wsjf_service import compute_wsjf_score

logger = logging.getLogger(__name__)

# The property key under which the framework kind is stored.
KIND_PROPERTY = "entelim:kind"
# The property key prefix for WSJF components.
WSJF_PREFIX = "wsjf_"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_properties(element) -> Dict[str, Any]:
    """Read the acm_properties dict from an element, normalised."""
    raw = getattr(element, "acm_properties", None) or {}
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    return dict(raw)


def _write_property(element, key: str, value: Any, source: str = "user"):
    """Write one property onto an element's acm_properties with provenance."""
    props = _read_properties(element)
    entry = {
        "value": value,
        "source": source,
        "set_by": source,
        "set_at": _now().isoformat(),
    }
    props[key] = entry
    element.acm_properties = props
    return entry


def _read_property(element, key: str) -> Optional[Dict]:
    """Read one property entry (with provenance) or None."""
    props = _read_properties(element)
    raw = props.get(key)
    if raw is None:
        return None
    if isinstance(raw, dict) and "value" in raw:
        return raw
    return {"value": raw, "source": None, "set_by": None, "set_at": None}


def _read_property_value(element, key: str):
    """Read the plain value of one property, or None."""
    entry = _read_property(element, key)
    if entry is None:
        return None
    return entry.get("value")


# ── Profile registry ────────────────────────────────────────────────────────


def _load_frameworks() -> Dict[str, Any]:
    """Load every framework profile from app/services/profiles/.

    Returns a dict keyed by framework_id with the framework's config.
    """
    import importlib
    import pkgutil

    import app.services.profiles as profiles_pkg

    frameworks = {}
    for _finder, name, _ispkg in pkgutil.iter_modules(profiles_pkg.__path__):
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"app.services.profiles.{name}")
            frameworks[mod.FRAMEWORK_ID] = mod
        except Exception as exc:
            logger.warning("Could not load profile %s: %s", name, exc)
    return frameworks


def list_frameworks() -> List[Dict]:
    """List every loaded framework with its id and label."""
    return [
        {"id": fid, "label": getattr(mod, "FRAMEWORK_LABEL", fid)}
        for fid, mod in _load_frameworks().items()
    ]


def list_kinds(framework_id: str = "safe") -> List[Dict]:
    """List every kind in a framework with its allowed types."""
    frameworks = _load_frameworks()
    mod = frameworks.get(framework_id)
    if not mod:
        return []
    return [
        {
            "kind": kind,
            "allowed_types": info["allowed_types"],
            "properties": list(info.get("properties", {}).keys()),
        }
        for kind, info in mod.KINDS.items()
    ]


def allowed_kinds(element_type: str, framework_id: str = "safe") -> List[str]:
    """Return the kind names allowed for an element type in a framework."""
    frameworks = _load_frameworks()
    mod = frameworks.get(framework_id)
    if not mod:
        return []
    return mod.allowed_kinds_for_type(element_type)


def kind_allowed(kind: str, element_type: str, framework_id: str = "safe") -> bool:
    """True if *kind* may be applied to an element of *element_type*."""
    frameworks = _load_frameworks()
    mod = frameworks.get(framework_id)
    if not mod:
        return False
    return mod.kind_allowed_for_type(kind, element_type)


def kind_properties(kind: str, framework_id: str = "safe") -> Dict:
    """Return the typed property definitions for a kind."""
    frameworks = _load_frameworks()
    mod = frameworks.get(framework_id)
    if not mod:
        return {}
    return mod.kind_properties(kind)


# ── Reader ──────────────────────────────────────────────────────────────────


def read_profile(element) -> Dict:
    """Read the full profile (kind, framework, WSJF parts, WSJF score) from an element.

    Returns a dict with keys: kind, framework_id, framework_label, properties,
    wsjf_score, and every WSJF component with its provenance.
    """
    props = _read_properties(element)
    kind = None
    framework_id = None
    framework_label = None

    # Read the kind from the stored property.
    kind_entry = props.get(KIND_PROPERTY)
    if kind_entry and isinstance(kind_entry, dict):
        kind = kind_entry.get("value")
        framework_id = kind_entry.get("framework_id")
    elif kind_entry:
        kind = kind_entry

    # Resolve framework label.
    if framework_id:
        for fw in list_frameworks():
            if fw["id"] == framework_id:
                framework_label = fw["label"]
                break

    # Read WSJF components.
    business_value = _read_property_value(element, WSJF_PREFIX + "business_value")
    time_criticality = _read_property_value(element, WSJF_PREFIX + "time_criticality")
    risk_reduction = _read_property_value(element, WSJF_PREFIX + "risk_reduction")
    job_size = _read_property_value(element, WSJF_PREFIX + "job_size")

    # Compute WSJF score.
    wsjf_score = compute_wsjf_score(
        business_value=business_value or 1,
        time_criticality=time_criticality or 1,
        risk_reduction=risk_reduction or 1,
        job_size=job_size or 1,
    )

    return {
        "kind": kind,
        "framework_id": framework_id,
        "framework_label": framework_label,
        "wsjf_score": wsjf_score,
        "properties": {
            "business_value": _read_property(element, WSJF_PREFIX + "business_value"),
            "time_criticality": _read_property(element, WSJF_PREFIX + "time_criticality"),
            "risk_reduction": _read_property(element, WSJF_PREFIX + "risk_reduction"),
            "job_size": _read_property(element, WSJF_PREFIX + "job_size"),
        },
    }


# ── Writer ──────────────────────────────────────────────────────────────────


def set_kind(element, kind: str, framework_id: str = "safe", source: str = "user") -> Dict:
    """Set the framework kind on an element.

    Validates that the kind is allowed for the element's type.
    Returns the written entry.
    """
    element_type = getattr(element, "type", None)
    if not element_type:
        raise ValueError("Element has no type.")

    if kind is not None and not kind_allowed(kind, element_type, framework_id):
        raise ValueError(
            f"Kind '{kind}' is not allowed for element type '{element_type}' "
            f"in framework '{framework_id}'."
        )

    props = _read_properties(element)
    if kind is None:
        # Remove the kind.
        props.pop(KIND_PROPERTY, None)
        # Also remove the specialisation property if it was set to this kind.
        props.pop(SPECIALIZATION_PROPERTY, None)
    else:
        entry = {
            "value": kind,
            "framework_id": framework_id,
            "source": source,
            "set_by": source,
            "set_at": _now().isoformat(),
        }
        props[KIND_PROPERTY] = entry
        # Also write the specialisation property so export/import preserves it.
        props[SPECIALIZATION_PROPERTY] = kind

    element.acm_properties = props
    db.session.flush()
    return _read_property(element, KIND_PROPERTY) or {}


def set_wsjf_component(
    element,
    component: str,
    value: int,
    source: str = "user",
) -> Dict:
    """Set one WSJF component (business_value, time_criticality, risk_reduction, job_size).

    Valid component names are the four WSJF parts. Returns the written entry.
    """
    valid = {"business_value", "time_criticality", "risk_reduction", "job_size"}
    if component not in valid:
        raise ValueError(f"WSJF component must be one of {valid}, got '{component}'.")

    value = max(int(value), 1)
    key = WSJF_PREFIX + component
    return _write_property(element, key, value, source=source)


def set_wsjf_components(
    element,
    business_value: int = 1,
    time_criticality: int = 1,
    risk_reduction: int = 1,
    job_size: int = 1,
    source: str = "user",
) -> Dict:
    """Set all four WSJF components at once and return the profile."""
    set_wsjf_component(element, "business_value", business_value, source=source)
    set_wsjf_component(element, "time_criticality", time_criticality, source=source)
    set_wsjf_component(element, "risk_reduction", risk_reduction, source=source)
    set_wsjf_component(element, "job_size", job_size, source=source)
    db.session.flush()
    return read_profile(element)


def get_kind_property(element, key: str):
    """Read the plain value of a kind-specific property."""
    return _read_property_value(element, key)


def set_kind_property(element, key: str, value: any, source: str = "user") -> Dict:
    """Write a kind-specific property."""
    return _write_property(element, key, value, source=source)