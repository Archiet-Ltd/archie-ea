"""The ranked list of adopted controls with missing or out-of-date evidence, and the
approval path for evidence a connector brings in.

Reuse
-----
* ``control_evidence`` -- the one evidence rule and the one reading of a control's tests.
* ``IntelligenceQueryService.set_impact`` -- the one blast-radius answer; the list makes a
  single call for the highest-priority applications (``EXPOSURE_MAX_APPLICATIONS``).
* ``ApplicationOwner.display_rows_for_owner_rows`` -- the one owner display converter.
* ``create_approval_record`` and ``AIChatApprovalService.approve_and_execute`` -- the one
  approval queue; connector evidence is a proposal and is written only on approval.
* ``current_org_id`` -- the caller's active organisation.

The list is derived on every read; it is not stored and is not a second gap register.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.extensions import db
from app.middleware.tenant_context import current_org_id
from app.modules.compliance.services.control_evidence import (
    EXCEPTION,
    GAP_STATES,
    INEFFECTIVE,
    control_effectiveness,
    evidence_state,
    latest_tests_by_kind,
    naive_utc,
)
from app.modules.intelligence.services.answer_envelope import CONFIDENCE_DRAWN_ONLY
from app.modules.intelligence.services.query_service import SET_IMPACT_MAX_SEEDS

logger = logging.getLogger(__name__)

ENTITY_CONTROL_EVIDENCE = "control_evidence"
EXPOSURE_MAX_APPLICATIONS = SET_IMPACT_MAX_SEEDS
EVIDENCE_URL_MAX_LENGTH = 500

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_UNRANKED = len(_PRIORITY_RANK)

REASON_TOO_MANY = "not computed for this many applications"
REASON_NO_APPLICATION = "not applicable to an organisation-wide control"
REASON_NO_ELEMENT = "this application has no model element"
REASON_NO_ORGANISATION = "no active organisation"
REASON_UNRESOLVED = "the application's model element was not found"
REASON_PARTIAL = "counted over the first reached elements only"
ORGANISATION_WIDE = "Organisation-wide"
DASH = "—"


def _full_name(user) -> str:
    if user is None:
        return DASH
    name = " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p).strip()
    return name or DASH


def _day(moment: Optional[datetime]) -> Optional[str]:
    return moment.date().isoformat() if moment is not None else None


def _valid_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > EVIDENCE_URL_MAX_LENGTH:
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def valid_evidence_url(value: Any) -> bool:
    """An evidence link is a web link of at most the stored length."""
    return _valid_http_url(value)


def _adopted_row(organization_id: Optional[int], row_id: int):
    """One adopted control row of the organisation, looked up directly by id."""
    from app.models.application_compliance import ApplicationComplianceControl

    if organization_id is None:
        return None
    return db.session.execute(
        db.select(ApplicationComplianceControl)
        .where(ApplicationComplianceControl.id == row_id)
        .where(ApplicationComplianceControl.organization_id == organization_id)
    ).scalar_one_or_none()


def evidence_gaps(organization_id: Optional[int], as_of: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The organisation's adopted controls whose evidence is missing or expired, or whose latest
    tests found them ineffective or an exception, ranked by control priority, then by how many
    elements the application reaches downstream, then by validity date (earliest first).

    Returns an empty list when the organisation is unknown or is not the caller's active one.
    """
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.control_test import ControlTest
    from app.models.user import User

    ambient = current_org_id()
    if organization_id is None or (ambient is not None and ambient != organization_id):
        return []
    as_of = naive_utc(as_of)

    # One read: every adopted control of the organisation with its global control, framework
    # and application. The global rows are reached only through the ids on the caller's rows.
    found = db.session.execute(
        db.select(ApplicationComplianceControl, ComplianceControl, RegulatoryFramework, ApplicationComponent)
        .join(ComplianceControl, ComplianceControl.id == ApplicationComplianceControl.control_id)
        .outerjoin(RegulatoryFramework, RegulatoryFramework.id == ComplianceControl.framework_id)
        .outerjoin(
            ApplicationComponent,
            db.and_(
                ApplicationComponent.id == ApplicationComplianceControl.application_id,
                ApplicationComponent.organization_id == organization_id,
            ),
        )
        .where(ApplicationComplianceControl.organization_id == organization_id)
        .order_by(ApplicationComplianceControl.id)
    ).all()
    if not found:
        return []

    # One read: every test of those rows, reduced to the latest of each kind per row.
    tests_by_row: Dict[int, list] = {}
    for test in db.session.execute(
        db.select(ControlTest)
        .where(ControlTest.organization_id == organization_id)
        .where(ControlTest.application_control_id.in_([row.id for row, *_ in found]))
    ).scalars():
        tests_by_row.setdefault(test.application_control_id, []).append(test)

    candidates = []
    for mapping, control, framework, component in found:
        state = evidence_state(mapping, as_of)
        tests = tests_by_row.get(mapping.id, [])
        effectiveness = control_effectiveness(tests)
        if state in GAP_STATES or effectiveness in (INEFFECTIVE, EXCEPTION):
            candidates.append((mapping, control, framework, component, state, tests, effectiveness))
    if not candidates:
        return []

    def _base_rank(item):
        mapping, control = item[0], item[1]
        return (
            _PRIORITY_RANK.get((control.priority or "").lower(), _UNRANKED),
            mapping.evidence_valid_until is None,
            mapping.evidence_valid_until or datetime.max,
            mapping.id,
        )

    candidates.sort(key=_base_rank)

    # One batched read of owners, one of users.
    app_ids = sorted({item[0].application_id for item in candidates if item[0].application_id is not None})
    owner_rows = []
    if app_ids:
        owner_rows = list(
            db.session.execute(
                db.select(ApplicationOwner)
                .where(ApplicationOwner.organization_id == organization_id)
                .where(ApplicationOwner.application_id.in_(app_ids))
                .order_by(ApplicationOwner.id)
            ).scalars()
        )
    user_ids = {r.user_id for r in owner_rows if r.user_id is not None}
    user_ids |= {item[0].evidence_owner_id for item in candidates if item[0].evidence_owner_id is not None}
    users: Dict[int, Any] = {}
    if user_ids:
        users = {
            u.id: u
            for u in db.session.execute(
                db.select(User).where(User.organization_id == organization_id).where(User.id.in_(sorted(user_ids)))
            ).scalars()
        }
    application_of_owner_row = {r.id: r.application_id for r in owner_rows}
    owner_names: Dict[int, List[str]] = {}
    for display in ApplicationOwner.display_rows_for_owner_rows(owner_rows, organization_id, users=users):
        owner_names.setdefault(application_of_owner_row[display["id"]], []).append(display["user_name"])

    exposure, exposure_reason, partial = _exposure(candidates, organization_id, ambient)

    rows = []
    for mapping, control, framework, component, state, tests, effectiveness in candidates:
        latest = latest_tests_by_kind(tests)
        driver = next((t for t in latest.values() if t.result == effectiveness), None)
        most_recent = max(latest.values(), key=lambda t: (t.tested_at, t.id), default=None)
        result_shown = (driver or most_recent).result if (driver or most_recent) else None
        names = [n for n in owner_names.get(mapping.application_id, []) if n != DASH]
        element_id = component.archimate_element_id if component is not None else None
        key = mapping.application_id
        rows.append({
            "adopted_row_id": mapping.id,
            "control_code": control.control_code,
            "title": control.title,
            "priority": control.priority,
            "framework_code": framework.code if framework is not None else None,
            "application_id": mapping.application_id,
            "application_name": component.name if component is not None else ORGANISATION_WIDE,
            "state": state,
            "valid_until": _day(mapping.evidence_valid_until),
            "owner": ", ".join(dict.fromkeys(names)) if names else DASH,
            "evidence_owner": _full_name(users.get(mapping.evidence_owner_id)),
            "evidence_owner_id": mapping.evidence_owner_id,
            "latest_test_result": result_shown,
            "effectiveness": effectiveness,
            "test_notes": (driver.notes if driver is not None and driver.result in (INEFFECTIVE, EXCEPTION) else None),
            "exposure": exposure.get(key),
            "exposure_reason": (
                None if exposure.get(key) is not None and not partial
                else exposure_reason.get(key) or (REASON_PARTIAL if exposure.get(key) is not None else None)
            ),
            "evidence": mapping.evidence_url.strip() if (mapping.evidence_url or "").strip() else DASH,
            "provenance": {
                "source": mapping.evidence_source,
                "who": _full_name(users.get(mapping.evidence_owner_id)),
                "collected_at": mapping.evidence_collected_at.isoformat() if mapping.evidence_collected_at else None,
            },
            "confidence": "recorded",
            "exposure_confidence": None,
            "exposure_confidence_basis": CONFIDENCE_DRAWN_ONLY,
            "trace": {
                "control_id": mapping.control_id,
                "framework_id": control.framework_id,
                "adopted_row_id": mapping.id,
                "application_id": mapping.application_id,
                "element_id": element_id,
            },
        })

    rows.sort(key=lambda r: (
        _PRIORITY_RANK.get((r["priority"] or "").lower(), _UNRANKED),
        r["exposure"] is None,
        -(r["exposure"] or 0),
        r["valid_until"] is None,
        r["valid_until"] or "",
        r["adopted_row_id"],
    ))
    return rows


def _exposure(candidates, organization_id: int, ambient: Optional[int]):
    """Elements reached downstream per application id, from one ``set_impact`` call over the
    applications of the highest-priority candidates (drawn relationships only)."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    counts: Dict[Optional[int], Optional[int]] = {}
    reasons: Dict[Optional[int], str] = {}
    element_of: Dict[int, int] = {}
    chosen: List[int] = []  # application ids, in priority order, up to the ceiling
    for item in candidates:
        mapping, component = item[0], item[3]
        app_id = mapping.application_id
        if app_id is None:
            reasons[None] = REASON_NO_APPLICATION
            continue
        if app_id in element_of or app_id in reasons:
            continue
        if component is None or component.archimate_element_id is None:
            reasons[app_id] = REASON_NO_ELEMENT
            continue
        if len(chosen) >= EXPOSURE_MAX_APPLICATIONS:
            reasons[app_id] = REASON_TOO_MANY
            continue
        chosen.append(app_id)
        element_of[app_id] = component.archimate_element_id
    if not chosen:
        return counts, reasons, False
    if ambient != organization_id:
        for app_id in chosen:
            reasons[app_id] = REASON_NO_ORGANISATION
        return counts, reasons, False

    result = IntelligenceQueryService.set_impact(
        sorted(set(element_of.values())), direction="downstream", include_derived=False, with_owner=False
    )
    reached: Dict[int, int] = {}
    for row in result["rows"]:
        for seed in row["seed_ids"]:
            reached[seed] = reached.get(seed, 0) + 1
    unresolved = set(result["unresolved_seed_ids"])
    for app_id in chosen:
        element_id = element_of[app_id]
        if element_id in unresolved:
            reasons[app_id] = REASON_UNRESOLVED
        else:
            counts[app_id] = reached.get(element_id, 0)
    return counts, reasons, bool(result.get("truncated"))


# --------------------------------------------------------------------- #
# Connector-sourced evidence: a proposal first, a write only on approval #
# --------------------------------------------------------------------- #


def propose_control_evidence(
    org_id: int,
    application_control_id: int,
    *,
    evidence_url: str,
    collected_at: Optional[datetime],
    valid_until: Optional[datetime],
    source_system: str,
    summary: str,
    proposed_by_user_id: Optional[int] = None,
):
    """Queue evidence a connector found for an adopted control as an approval proposal.

    Nothing is written to the control row. Refuses (``ValueError``) when the adopted row is not
    in ``org_id`` or the link is not a web link. The caller commits.
    """
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    row = _adopted_row(org_id, application_control_id)
    if row is None:
        raise ValueError("Adopted control not found in this organisation")
    if not _valid_http_url(evidence_url):
        raise ValueError("Evidence must be a web link (http or https)")

    code = row.control.control_code if row.control is not None else str(row.control_id)
    payload = {
        "evidence_url": evidence_url.strip(),
        "collected_at": collected_at.isoformat() if collected_at else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "source_system": source_system,
        "summary": summary,
    }
    return create_approval_record(
        organization_id=org_id,
        operation_type="update",
        entity_type=ENTITY_CONTROL_EVIDENCE,
        entity_id=row.id,
        summary=f"Record evidence from {source_system} for control {code}: {summary}"[:500],
        operation_payload=payload,
        user_id=proposed_by_user_id,
    )


def _parse_moment(value: Any) -> Optional[datetime]:
    if not value:
        return None
    return naive_utc(datetime.fromisoformat(value))


def accept_control_evidence(approval_id: int, organization_id: int, accepted_by: Optional[int]) -> Dict[str, Any]:
    """Write an approved piece of connector evidence onto its adopted control row.

    Called by the approval queue's executor after ``approve_and_execute`` has claimed the
    approval (recording the approver and refusing self-approval). Acts only on an approved,
    not yet executed approval of ``organization_id`` whose recorded approver is ``accepted_by``;
    a pending, refused, expired or executed approval writes nothing. The control row is looked
    up in the approval's own organisation; a proposal that names another organisation's row is
    refused. Does not commit.
    """
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

    import json

    def refuse(reason: str) -> Dict[str, Any]:
        logger.warning("accept_control_evidence: approval %s refused: %s", approval_id, reason)
        return {"success": False, "error": reason}

    if organization_id is None:
        return refuse("no organisation")
    approval = db.session.execute(
        db.select(AIChatCRUDApproval)
        .where(AIChatCRUDApproval.id == approval_id)
        .where(AIChatCRUDApproval.organization_id == organization_id)
        .where(AIChatCRUDApproval.entity_type == ENTITY_CONTROL_EVIDENCE)
    ).scalar_one_or_none()
    if approval is None:
        return refuse("approval not found")
    # Only the approval queue's executor reaches this with an approval it has claimed
    # (status approved, not yet executed); a pending, refused, expired or executed approval
    # writes nothing, so there is no way to accept evidence around the queue.
    if approval.status != ApprovalStatus.APPROVED or approval.executed_at is not None:
        return refuse(f"approval is {approval.status.value}, not claimed for execution")
    if approval.approved_by_id is None or approval.approved_by_id != accepted_by:
        return refuse("the recorded approver does not match")

    row = _adopted_row(approval.organization_id, approval.entity_id)
    if row is None:
        return refuse("adopted control not found in this organisation")
    try:
        payload = json.loads(approval.operation_payload)
        url = payload["evidence_url"]
        collected_at = _parse_moment(payload.get("collected_at"))
        valid_until = _parse_moment(payload.get("valid_until"))
    except (ValueError, TypeError, KeyError):
        return refuse("invalid proposal")
    if not _valid_http_url(url):
        return refuse("invalid evidence link")

    row.evidence_url = url.strip()
    row.evidence_collected_at = collected_at
    row.evidence_valid_until = valid_until
    row.evidence_source = "connector"
    db.session.flush()
    return {"success": True, "adopted_row_id": row.id}
