"""Business capabilities captured at onboarding, written to the real capability table.

There is no onboarding copy of this data. An answer becomes (or updates) a
``BusinessCapability`` row for the organisation, in the same table every other
part of the product reads. Creating one through the ORM already does the rest:
a ``before_insert`` listener creates its ArchiMate Capability element, and the
insert/update listeners project it, with its organisation and maturity, into
``unified_capabilities`` (the single maturity authority other views read).

What is offered depends on the company's stage and size; see
``seed_data/onboarding/capability_catalogue.yml``. The catalogue only *offers*.
A capability exists in the model once the founder has answered about it, and an
unanswered one stays "expected at your stage" and is never written.

Rules kept on purpose:
  * Unrated is NULL. A maturity level is only stored when the founder gave one,
    and that is also what stamps ``maturity_assessment_date`` ("this is real").
  * The stage expectation is a comparison. It is never written as the company's
    own ``target_maturity_level``.
  * Onboarding never deletes a capability.
"""
from __future__ import annotations

import datetime
import functools
import re
from pathlib import Path

import yaml

from app import db
from app.models.business_capabilities import BusinessCapability

_DATA_PATH = Path(__file__).resolve().parents[3] / "seed_data" / "onboarding" / "capability_catalogue.yml"

_MAX_OWNER = 100  # BusinessCapability.business_owner is String(100)
_LEVELS = (1, 2, 3, 4, 5)


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    with open(_DATA_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def stages() -> list[str]:
    return list(_load()["stages"])


def size_bands() -> list[dict]:
    return [dict(b) for b in _load()["size_bands"]]


def maturity_levels() -> list[dict]:
    return [dict(m) for m in _load()["maturity_levels"]]


def band_from_text(company_size: str | None) -> str:
    """Best-effort size band from free text like "12 people" or "5,000 employees".

    Only used when the founder did not pick a band. With no number in the text it
    falls back to the smallest band, the safest assumption (fewest questions).
    """
    match = re.search(r"\d[\d,\.]*", company_size or "")
    if not match:
        return "micro"
    try:
        headcount = int(float(match.group(0).replace(",", "")))
    except ValueError:
        return "micro"
    if headcount <= 10:
        return "micro"
    if headcount <= 50:
        return "small"
    if headcount <= 250:
        return "mid"
    return "large"


def _expected(entry: dict, stage: str) -> int:
    per_capability = entry.get("expected") or {}
    return int(per_capability.get(stage) or _load()["default_expected"][stage])


def catalogue_for(stage: str, size_band: str) -> list[dict]:
    """Capabilities offered to a company at *stage* and *size_band*, in order."""
    data = _load()
    stage_order, band_keys = data["stages"], [b["key"] for b in data["size_bands"]]
    if stage not in stage_order:
        stage = stage_order[0]
    if size_band not in band_keys:
        size_band = band_keys[0]
    stage_idx, band_idx = stage_order.index(stage), band_keys.index(size_band)
    offered = []
    for entry in data["capabilities"]:
        if stage_order.index(entry["from_stage"]) <= stage_idx and band_keys.index(entry["from_size"]) <= band_idx:
            offered.append(
                {
                    "key": entry["key"],
                    "label": entry["label"],
                    "hint": entry["hint"],
                    "group": entry["group"],
                    "expected": _expected(entry, stage),
                }
            )
    return offered


def _existing_by_name() -> dict[str, BusinessCapability]:
    # Inside a request the tenant listener scopes this to the caller's
    # organisation; a name match is therefore always within one organisation.
    return {(c.name or "").strip().lower(): c for c in BusinessCapability.query.all()}


def read(stage: str, size_band: str) -> list[dict]:
    """The offered catalogue, each item joined with the organisation's real row."""
    existing = _existing_by_name()
    rows = []
    for item in catalogue_for(stage, size_band):
        cap = existing.get(item["label"].lower())
        rows.append(
            {
                **item,
                "present": cap is not None,
                "owner": (cap.business_owner or "") if cap else "",
                "maturity": cap.current_maturity_level if cap else None,
            }
        )
    return rows


def _clean_owner(value) -> str | None:
    text = (str(value) if value is not None else "").strip()
    return text[:_MAX_OWNER] or None


def _clean_level(value) -> int | None:
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if level in _LEVELS else None


def save(items: list[dict], *, stage: str, size_band: str) -> dict:
    """Create or update capabilities from what the founder answered.

    Each item is ``{"key": ..., "owner": ..., "maturity": ...}``. A key that is
    not offered to this company is ignored. ``owner`` / ``maturity`` are applied
    only when present in the item; ``maturity: null`` clears a rating.
    Returns ``{"created": n, "updated": n}``.
    """
    offered = {c["key"]: c for c in catalogue_for(stage, size_band)}
    existing = _existing_by_name()
    created = updated = 0
    now = datetime.datetime.utcnow()
    for item in items or []:
        offer = offered.get((item or {}).get("key"))
        if not offer:
            continue
        cap = existing.get(offer["label"].lower())
        is_new = cap is None
        if is_new:
            cap = BusinessCapability(
                name=offer["label"],
                description=offer["hint"],
                business_domain=offer["group"],
                category=offer["group"],
                level=1,
            )
            db.session.add(cap)
            existing[offer["label"].lower()] = cap
        if "owner" in item:
            cap.business_owner = _clean_owner(item["owner"])
        if "maturity" in item:
            level = _clean_level(item["maturity"])
            cap.current_maturity_level = level
            if level is not None:
                cap.maturity_assessment_date = now
                if not cap.maturity_assessment_notes:
                    cap.maturity_assessment_notes = "Rated during onboarding"
            else:
                cap.maturity_assessment_date = None
            cap.maturity_gap = (
                cap.target_maturity_level - level
                if level is not None and cap.target_maturity_level
                else None
            )
        if is_new:
            created += 1
        else:
            updated += 1
    db.session.commit()
    return {"created": created, "updated": updated}
