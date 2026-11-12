"""What an application read from GitHub still needs, and the step that fills each.

The connector reads a repository and creates the application with what the repository states. It
cannot read who owns it, which capability it serves, what kind of system it is or where it stands in
its life. This module names those four gaps for an imported application, links each to the existing
step that fills it, and offers a value only where the repository gives one, with the file and
repository it came from. Nothing is offered without such a source.

Owner, capability, type and status are judged by the same rules the application page uses elsewhere:
the owner gap is the "Owner" item of ``compute_completeness`` (an owner is a record in the owner
store; text alone is not one), a capability is a row in the capability mapping store.
Type and status are the two things the import fills with a starting word (the general type
"application" and the status "planning"); while either is still that word, nobody has chosen it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from flask import url_for

from app import db

# What the import writes before anyone has chosen: the general type and the starting status.
IMPORT_TYPE = "application"
IMPORT_STATUS = "planning"

FIELDS = ("owner", "capability", "type", "status")


def _fact(element, key):
    entry = (getattr(element, "acm_properties", None) or {}).get(key)
    return entry.get("value") if isinstance(entry, dict) else None


def _where(element) -> Optional[str]:
    """The repository and file the element was read from, in words ("acme/shop, CODEOWNERS")."""
    try:
        ref = json.loads(element.source_ref or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(ref, dict) or not ref.get("repository"):
        return None
    commit = f" at commit {ref['commit'][:7]}" if ref.get("commit") else ""
    file = f", {ref['file']}" if ref.get("file") else ""
    return f"{ref['repository']}{file}{commit}"


def _owner_hint(element, org_id) -> Dict[str, Any]:
    """The code owners GitHub names. One person of this organisation whose e-mail is named is a
    suggestion; a team or a handle is only said, since it names nobody who can be assigned here."""
    from app.connectors.github import _person_in_org

    owners = [o for o in (_fact(element, "github_code_owners") or []) if isinstance(o, str)]
    where = _where(element)
    if not owners or not where:
        return {}
    person = _person_in_org(org_id, owners)
    if person is not None:
        name = " ".join(p for p in (person.first_name, person.last_name) if p) or person.email
        hint = {"suggestion": name, "evidence": f"Named as code owner in {where}"}
        teams = [o for o in owners if o.startswith("@")]
        if teams:
            hint["note"] = f"GitHub also names {', '.join(teams)}."
        return hint
    named = ", ".join(owners)
    return {"note": f"GitHub names {named} as code owner in {where}. Pick the person here who is that owner."}


def _capability_hint(element, org_id) -> Dict[str, Any]:
    """The capability whose name is the group the repository's topics put it in, if this organisation has one."""
    from app.models.business_capabilities import BusinessCapability

    group = _fact(element, "github_group")
    where = _where(element)
    if not group or not where:
        return {}
    match = db.session.execute(
        db.select(BusinessCapability.name)
        .where(BusinessCapability.organization_id == org_id)
        .where(db.func.lower(BusinessCapability.name) == str(group).lower())
        .order_by(BusinessCapability.id)
        .limit(1)
    ).scalar()
    if match is None:
        return {}
    return {"suggestion": match, "evidence": f"The repository's topics put it in the group {group} ({where})"}


def application_gaps(org_id, applications: Iterable[Any], skip_ids: Iterable[int] = ()) -> Dict[int, Dict[str, Any]]:
    """For each imported application, in one pass for the page: ``{id: {"where": str, "gaps": [...]}}``.

    A gap is ``{"key", "label", "detail", "href" or "capability", "suggestion", "evidence", "note"}``.
    Applications that did not come from a connector, and repositories nobody has promoted
    (``skip_ids``), are absent. Everything is read by organisation.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.archimate_core import ArchiMateElement
    from app.models.application_owner import ApplicationOwner
    from app.services.application_fact_sheet import compute_completeness
    from app.services.element_ownership_service import ACCOUNTABLE_LEGACY_OWNER_COLUMNS, OWNER_TEXT_BLANKS

    skip = set(skip_ids)
    rows = [a for a in applications if a.archimate_element_id and a.id not in skip and a.organization_id == org_id]
    if not rows:
        return {}
    elements = {
        e.id: e
        for e in db.session.execute(
            db.select(ArchiMateElement).where(
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.id.in_([a.archimate_element_id for a in rows]),
                ArchiMateElement.source_kind == "connector",
            )
        ).scalars()
    }
    rows = [a for a in rows if a.archimate_element_id in elements]
    ids = [a.id for a in rows]
    if not ids:
        return {}
    owner_counts = dict(db.session.execute(
        db.select(ApplicationOwner.application_id, db.func.count(ApplicationOwner.id))
        .where(ApplicationOwner.organization_id == org_id, ApplicationOwner.application_id.in_(ids))
        .group_by(ApplicationOwner.application_id)).all())
    mapped = set(db.session.execute(
        db.select(ApplicationCapabilityMapping.application_component_id).where(
            ApplicationCapabilityMapping.organization_id == org_id,
            ApplicationCapabilityMapping.application_component_id.in_(ids),
            ApplicationCapabilityMapping.business_capability_id.isnot(None))).scalars())

    out: Dict[int, Dict[str, Any]] = {}
    for a in rows:
        element = elements[a.archimate_element_id]
        edit = url_for("unified_applications.application_edit", id=a.id)
        detail = url_for("unified_applications.application_detail", id=a.id)
        gaps: List[Dict[str, Any]] = []
        if "Owner" in compute_completeness(a, owner_count=owner_counts.get(a.id, 0))["missing"]:
            text = next((getattr(a, c).strip(OWNER_TEXT_BLANKS) for c in ACCOUNTABLE_LEGACY_OWNER_COLUMNS
                         if (getattr(a, c, None) or "").strip(OWNER_TEXT_BLANKS)), None)
            gap = {"key": "owner", "label": "Owner", "detail": "No owner yet",
                   "href": detail + "#ownership", **_owner_hint(element, org_id)}
            if text:
                gap.update(action="Record as owner",
                           detail="Named as " + text + " in text only; no owner is recorded")
            gaps.append(gap)
        if a.id not in mapped:
            gaps.append({"key": "capability", "label": "Capability", "detail": "No capability mapped yet",
                         "href": None, **_capability_hint(element, org_id)})
        if (a.component_type or "").strip().lower() in ("", IMPORT_TYPE):
            gaps.append({"key": "type", "label": "Type",
                         "detail": "Still the general type Application", "href": edit + "#application_type"})
        if (a.lifecycle_status or "").strip().lower() in ("", IMPORT_STATUS):
            gaps.append({"key": "status", "label": "Status",
                         "detail": "Still the starting status from the import", "href": edit + "#lifecycle_status"})
        if gaps:
            out[a.id] = {"where": _where(element), "gaps": gaps}
    return out
