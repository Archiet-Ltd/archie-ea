"""A person confirms or corrects the profile read from their website, and only then is it recorded.

The crawler stores each fact as a pending proposal (``service.py``). Here a person decides each one:
an accepted fact is written to the organisation's company profile and, where it describes something
the model holds, to the ArchiMate model through the backbone writer (products as business services,
technologies as system software, key people as business actors, compliance mentions as requirements
against the compliance catalogue, integration partners as external application components). Every
record keeps where it came from. A fact the person changed is stored as corrected, with the person as
its source; a fact left alone keeps the page it was read from.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm.attributes import flag_modified

from app import db

from .service import OP_PROFILE_FACT, PROFILE_FIELDS, PROPOSAL_SOURCE, pending_website_proposals

PROFILE_KEY = "company_profile"

SIZE_BANDS = (
    ("1-10", "1 to 10"), ("11-50", "11 to 50"), ("51-200", "51 to 200"),
    ("201-1000", "201 to 1,000"), ("1000+", "Over 1,000"),
)
GOALS = (
    ("understand", "Understand our estate"), ("change", "Plan a change"), ("cost", "Cut cost"),
    ("audit", "Prepare for an audit"), ("strategy", "Pick a strategy"),
)
_SIZE_KEYS = {key for key, _label in SIZE_BANDS}
_GOAL_KEYS = {key for key, _label in GOALS}

# Rows the person decides on, in profile order. The completeness figure describes the reading, not
# the company, so it is shown as a line and never offered as a fact.
_NOT_A_FACT = {"completeness_score"}
_LISTS = {"key_person", "social_profile", "technology", "product", "value_proposition", "competitive_advantage",
          "compliance", "integration_partner", "case_study"}

# field -> (ArchiMate type, layer) for the facts the model holds
_ELEMENTS = {
    "product": ("BusinessService", "Business"),
    "technology": ("SystemSoftware", "Technology"),
    "key_person": ("BusinessActor", "Business"),
    "integration_partner": ("ApplicationComponent", "Application"),
}


def asked(org_settings: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """The answers given up front, or None for what was not answered."""
    got = ((org_settings or {}).get("company_context") or {})
    return {"size_band": got.get("size_band"), "role": got.get("role"), "goal": got.get("goal")}


def display(value: Any) -> str:
    """A fact's value as one line a person can read and edit."""
    if isinstance(value, dict):
        if "min" in value or "max" in value:
            low, high = value.get("min"), value.get("max")
            return "%s to %s people" % (low, high) if low is not None and high is not None else "%s people" % (low or high)
        if "title" in value:
            return "%s, %s" % (value.get("name"), value["title"]) if value.get("title") else str(value.get("name"))
        if "platform" in value:
            return "%s: %s" % (value["platform"], value.get("url"))
        if "type" in value:
            return "%s (%s)" % (value.get("name"), value["type"]) if value.get("type") else str(value.get("name"))
        return ", ".join(str(v) for v in value.values() if v not in (None, ""))
    return str(value)


def proposals(org_id: int):
    """The facts waiting for a decision, each with a line to show and edit, and the completeness row (or None)."""
    rows = [dict(r) for r in pending_website_proposals(org_id) if r.get("field") in PROFILE_FIELDS]
    completeness = next((r for r in rows if r["field"] in _NOT_A_FACT), None)
    facts = [r for r in rows if r["field"] not in _NOT_A_FACT]
    for row in facts:
        row["text"] = display(row["value"])
        row["percent"] = int(round(float(row.get("confidence") or 0) * 100))
    return facts, completeness


def _apply_correction(original: Any, text: str) -> Any:
    """The stored value after a person edited the line shown for it."""
    if text == display(original):
        return original
    if isinstance(original, dict):
        if "title" in original:
            name, _sep, title = text.partition(",")
            return {"name": name.strip(), "title": title.strip() or None}
        if "platform" in original:
            platform, _sep, url = text.partition(":")
            return {"platform": platform.strip(), "url": url.strip()} if url.strip() else {**original, "url": text}
        if "type" in original:
            name, _sep, rest = text.partition("(")
            return {"name": name.strip(), "type": rest.rstrip(") ").strip() or original.get("type")}
        return {**original, "name": text}
    if isinstance(original, int) and not isinstance(original, bool) and text.isdigit():
        return int(text)
    return text


def _name_of(user) -> str:
    name = getattr(user, "full_name", None)
    name = name() if callable(name) else name
    return (name or getattr(user, "email", None) or "A member").strip()


def _provenance(row: Dict[str, Any], corrected: bool, user) -> Dict[str, Any]:
    person = _name_of(user)
    return {
        "source": "person" if corrected else "website",
        "source_url": row.get("source_url"),
        "fetched_at": row.get("fetched_at"),
        "confidence": 1.0 if corrected else row.get("confidence"),
        "corrected": corrected,
        "confirmed_by": user.id,
        "confirmed_by_name": person,
        "confirmed_at": datetime.utcnow().isoformat(),
    }


def _stamp(element, row: Dict[str, Any], corrected: bool, user) -> None:
    person = _name_of(user)
    now = datetime.utcnow()
    if corrected:
        element.source_kind = "manual"
        element.source_ref = str(user.id)
        element.source_label = person
    else:
        element.source_kind = "import"
        element.source_ref = json.dumps({
            "system": "website", "url": row.get("source_url"), "fetched_at": row.get("fetched_at"),
            "confidence": row.get("confidence"),
        }, sort_keys=True)
        element.source_label = "Company website %s" % row.get("source_url")
    element.source_recorded_at = now


def _find_element(org_id: int, element_type: str, name: str):
    from app.models.archimate_core import ArchiMateElement

    return ArchiMateElement.query.filter_by(organization_id=org_id, type=element_type, name=name[:100]).first()


def _element(org_id: int, field: str, value: Any, row: Dict[str, Any], corrected: bool, user):
    """Write (or find) the model element a fact describes. Returns it, or None for a fact that stays in the profile."""
    from app.services.archimate_backbone import create_backbone_element

    if field not in _ELEMENTS and field != "compliance":
        return None
    prov = {"origin": "company_website", "source_url": row.get("source_url"), "fetched_at": row.get("fetched_at"),
            "confidence": 1.0 if corrected else row.get("confidence"), "corrected_by": user.id if corrected else None}
    if field == "compliance":
        return _requirement(org_id, str(value), row, corrected, user, prov)
    etype, layer = _ELEMENTS[field]
    name = str(value.get("name") if isinstance(value, dict) else value).strip()
    if not name:
        return None
    existing = _find_element(org_id, etype, name)
    if existing is not None:
        return existing
    description, props = None, dict(prov)
    if field == "key_person":
        description = value.get("title") if isinstance(value, dict) else None
        props["role"] = description
    elif field == "technology":
        props["technology_type"] = value.get("type") if isinstance(value, dict) else None
        props["vendor"] = name
        description = "%s, read from the company website" % (props["technology_type"] or "Technology")
    elif field == "integration_partner":
        props["external"] = True
        description = "External integration partner named on the company website"
    elif field == "product":
        description = "Product or service named on the company website"
    element = create_backbone_element(
        element_type=etype, layer=layer, name=name, description=description, organization_id=org_id, provenance=props)
    _stamp(element, row, corrected, user)
    return element


def _requirement(org_id: int, text: str, row: Dict[str, Any], corrected: bool, user, prov: Dict[str, Any]):
    """A compliance mention becomes a Requirement element and a compliance requirement tied to the catalogue entry it names."""
    from app.models.compliance_models import ComplianceRequirement, RegulatoryFramework
    from app.services.archimate_backbone import create_backbone_element

    title = "Comply with %s" % text.strip()
    element = _find_element(org_id, "Requirement", title)
    if element is not None:
        return element
    wanted = "".join(ch for ch in text.lower() if ch.isalnum())
    framework = None
    for candidate in RegulatoryFramework.query.all():
        names = ["".join(ch for ch in (candidate.code or "").lower() if ch.isalnum()),
                 "".join(ch for ch in (candidate.name or "").lower() if ch.isalnum())]
        if wanted and any(n and (n == wanted or n in wanted or wanted in n) for n in names):
            framework = candidate
            break
    element = create_backbone_element(
        element_type="Requirement", layer="Motivation", name=title,
        description="Stated on the company website: %s" % text.strip(), organization_id=org_id,
        provenance={**prov, "framework": framework.code if framework else None})
    _stamp(element, row, corrected, user)
    db.session.add(ComplianceRequirement(
        organization_id=org_id, archimate_element_id=element.id, title=title,
        description="Stated on the company website: %s" % text.strip(), requirement_type="regulatory",
        framework_id=framework.id if framework else None))
    return element


def save_answers(org, size_band: Optional[str], role: Optional[str], goal: Optional[str]) -> None:
    """The three questions asked up front. An empty answer leaves the earlier one."""
    size_band, goal = (size_band or "").strip() or None, (goal or "").strip() or None
    role = (role or "").strip()[:120] or None
    if size_band is not None and size_band not in _SIZE_KEYS:
        raise ValueError("Choose a company size from the list.")
    if goal is not None and goal not in _GOAL_KEYS:
        raise ValueError("Choose a first goal from the list.")
    settings = dict(org.settings or {})
    context = dict(settings.get("company_context") or {})
    for key, value in (("size_band", size_band), ("role", role), ("goal", goal)):
        if value is not None:
            context[key] = value
    settings["company_context"] = context
    org.settings = settings
    flag_modified(org, "settings")


def confirm(org, user, decisions: List[Dict[str, Any]]) -> Dict[str, int]:
    """Record the facts the person accepted (as shown, or as they corrected them) and close the rest.

    ``decisions`` is a list of ``{"id": proposal id, "accept": bool, "value": edited line}``. A proposal
    that is not pending in this organisation is ignored. Nothing is written for a fact not accepted."""
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    by_id = {int(d["id"]): d for d in decisions if str(d.get("id", "")).isdigit()}
    rows = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == org.id, AIChatCRUDApproval.source_table == PROPOSAL_SOURCE,
        AIChatCRUDApproval.operation_type == OP_PROFILE_FACT, AIChatCRUDApproval.status == ApprovalStatus.PENDING,
        AIChatCRUDApproval.id.in_(list(by_id) or [0]),
    ).all()
    settings = dict(org.settings or {})
    profile = dict(settings.get(PROFILE_KEY) or {})
    kept = corrected_count = closed = 0
    named = None
    now = datetime.utcnow()
    for approval in rows:
        decision = by_id[approval.id]
        payload = json.loads(approval.operation_payload or "{}")
        field = payload.get("field")
        if field not in PROFILE_FIELDS or field in _NOT_A_FACT:
            continue
        if not decision.get("accept"):
            approval.status = ApprovalStatus.REJECTED
            approval.rejected_reason = "Left out by the person confirming the profile."
            closed += 1
            continue
        text = str(decision.get("value") if decision.get("value") is not None else display(payload["value"])).strip()
        if not text:
            approval.status = ApprovalStatus.REJECTED
            approval.rejected_reason = "Cleared by the person confirming the profile."
            closed += 1
            continue
        value = _apply_correction(payload["value"], text)
        corrected = value != payload["value"]
        entry = {"value": value, **_provenance(payload, corrected, user)}
        if field in _LISTS:
            held = [e for e in profile.get(field) or [] if e.get("value") != value]
            profile[field] = held + [entry]
        else:
            profile[field] = entry
        if field == "company_name" and str(value).strip():
            named = str(value).strip()[:200]
        element = _element(org.id, field, value, payload, corrected, user)
        approval.status = ApprovalStatus.APPROVED
        approval.approved_at = approval.executed_at = now
        approval.approved_by_id = user.id
        approval.execution_result = json.dumps(
            {"success": True, "field": field, "corrected": corrected, "element_id": element.id if element else None})
        kept += 1
        corrected_count += 1 if corrected else 0
    settings[PROFILE_KEY] = profile
    org.settings = settings
    flag_modified(org, "settings")
    if named:
        org.name = named  # the confirmed company name is the organisation's name wherever it is shown
        from app.modules.organization.public_signals import tidy

        tidy(org.id, org)  # nothing stored under the stand-in name survives the confirmation
    db.session.commit()
    return {"confirmed": kept, "corrected": corrected_count, "left_out": closed}


def profile_entries(org) -> List[Dict[str, Any]]:
    """What was confirmed, in profile order, for Settings: label, line, and where each came from."""
    held = (org.settings or {}).get(PROFILE_KEY) or {}
    out = []
    for field, label in PROFILE_FIELDS.items():
        raw = held.get(field)
        for entry in (raw if isinstance(raw, list) else [raw] if raw else []):
            out.append({"label": label, "text": display(entry.get("value")), "source": entry.get("source"),
                        "source_url": entry.get("source_url"), "confirmed_by_name": entry.get("confirmed_by_name")})
    return out
