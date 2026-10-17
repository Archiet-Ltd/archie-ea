"""The ranked list of adopted controls whose evidence is missing or has lapsed, or whose
tests failed, and the proposal path for evidence a connector sync found.

Reads only the caller's own organisation: every query names ``organization_id``, and
with no organisation the list is empty. Global catalogue rows (``ComplianceControl``,
``RegulatoryFramework``) are reached only through the ids on the organisation's own
mapping rows.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.middleware.tenant_context import current_org_id
from app.modules.compliance.services.control_evidence import (
    EXCEPTION,
    GAP_STATES,
    INEFFECTIVE,
    NOT_REQUIRED,
    NOT_TESTED,
    effectiveness,
    evidence_state,
)

logger = logging.getLogger(__name__)

ENTITY_CONTROL_EVIDENCE = "control_evidence"
EXPOSURE_NOT_COMPUTED_REASON = "not computed for this many applications"
EXPOSURE_NO_ELEMENT_REASON = "no model element for this application"
EXPOSURE_TRUNCATED_REASON = "the combined answer was cut at its row limit, so the count would be understated"
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_ROW_LIMIT = 200


def _person_name(user) -> str:
    name = " ".join(
        part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if part
    ).strip()
    return name or "—"


def _day(value: Optional[datetime]) -> Optional[str]:
    return value.date().isoformat() if value else None


def _exposure(organization_id: int, element_ids: List[int]) -> Tuple[Dict[int, int], bool]:
    """Elements each application element reaches downstream, from ONE ``set_impact`` call.

    Only drawn relationships count (``include_derived=False``), which is what each row's
    ``confidence_basis`` says. When the combined answer was cut at its row limit the
    per-seed counts would be understated, so none is returned and ``truncated`` is True.
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    if not element_ids or current_org_id() != organization_id:
        return {}, False
    answer = IntelligenceQueryService.set_impact(
        element_ids, direction="downstream", with_owner=False, include_derived=False
    )
    if answer.get("truncated"):
        return {}, True
    counts = {eid: 0 for eid in answer.get("seed_element_ids", [])}
    for row in answer.get("rows", []):
        for seed in row.get("seed_ids", []):
            if seed in counts and seed != row["element_id"]:
                counts[seed] += 1
    return counts, False


def evidence_gaps(organization_id: Optional[int], as_of: Optional[datetime] = None) -> Dict[str, Any]:
    """Controls with missing or expired evidence, or an ineffective or exception test result.

    Ranked by control priority (critical first), then exposure (most downstream elements
    first), then validity date (earliest first). Exposure is computed for the 25 highest
    priority applications; the rest show a dash and the reason.
    """
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.control_test import ControlTest
    from app.models.user import User
    from app.modules.intelligence.services.query_service import SET_IMPACT_MAX_SEEDS

    moment = as_of or datetime.utcnow()
    empty = {"rows": [], "total": 0, "as_of": moment.isoformat(), "exposure_limit": SET_IMPACT_MAX_SEEDS,
             "untested": [], "untested_total": 0}
    if organization_id is None:
        return empty

    mapped = db.session.execute(
        db.select(ApplicationComplianceControl, ComplianceControl, RegulatoryFramework, ApplicationComponent)
        .outerjoin(ComplianceControl, ComplianceControl.id == ApplicationComplianceControl.control_id)
        .outerjoin(RegulatoryFramework, RegulatoryFramework.id == ComplianceControl.framework_id)
        .outerjoin(ApplicationComponent, ApplicationComponent.id == ApplicationComplianceControl.application_id)
        .where(ApplicationComplianceControl.organization_id == organization_id)
        .order_by(ApplicationComplianceControl.id)
    ).all()
    if not mapped:
        return empty

    tests_by_control: Dict[int, List[Any]] = {}
    for test in db.session.execute(
        db.select(ControlTest).where(ControlTest.organization_id == organization_id)
    ).scalars():
        tests_by_control.setdefault(test.application_control_id, []).append(test)

    candidates = []
    untested = []
    for mapping, control, framework, component in mapped:
        # An application mapping whose application is gone from this organisation is skipped.
        if mapping.application_id is not None and (
            component is None or component.organization_id != organization_id
        ):
            continue
        state = evidence_state(mapping, moment)
        tests = tests_by_control.get(mapping.id, [])
        effect = effectiveness(tests)
        if state in GAP_STATES or effect in (INEFFECTIVE, EXCEPTION):
            candidates.append((mapping, control, framework, component, state, effect, tests))
        elif effect == NOT_TESTED and state != NOT_REQUIRED:
            # Not a gap, but the officer still has to be able to record its first tests.
            untested.append({
                "id": mapping.id,
                "control_code": control.control_code if control else None,
                "control_title": control.title if control else None,
                "framework_code": framework.code if framework else None,
                "application_name": component.name if component is not None else "Organisation-wide",
            })

    # Exposure for the highest-priority applications only (one set_impact call).
    def _priority(item) -> int:
        return _PRIORITY_RANK.get((item[1].priority if item[1] else None) or "medium", 2)

    by_priority = sorted(candidates, key=lambda c: (_priority(c), c[0].id))
    element_for_app: Dict[int, int] = {}
    for item in by_priority:
        component = item[3]
        element_id = getattr(component, "archimate_element_id", None) if component else None
        if element_id is None or component.id in element_for_app:
            continue
        if len(element_for_app) >= SET_IMPACT_MAX_SEEDS:
            break
        element_for_app[component.id] = element_id
    exposure_by_element, exposure_truncated = _exposure(organization_id, list(element_for_app.values()))

    owner_rows = (
        ApplicationOwner.get_display_rows_for_applications(
            sorted({c[3].id for c in candidates if c[3] is not None}), organization_id
        )
        if candidates
        else {}
    )
    owner_user_ids = {c[0].evidence_owner_id for c in candidates if c[0].evidence_owner_id}
    users = {}
    if owner_user_ids:
        users = {
            u.id: u
            for u in db.session.execute(
                db.select(User).where(User.organization_id == organization_id).where(User.id.in_(owner_user_ids))
            ).scalars()
        }

    rows = []
    for mapping, control, framework, component, state, effect, tests in candidates:
        element_id = getattr(component, "archimate_element_id", None) if component else None
        exposure: Optional[int] = None
        exposure_reason: Optional[str] = None
        if component is None:
            exposure_reason = None  # an organisation-wide control reaches no single application
        elif element_id is None:
            exposure_reason = EXPOSURE_NO_ELEMENT_REASON
        elif component.id in element_for_app and element_for_app[component.id] in exposure_by_element:
            exposure = exposure_by_element[element_for_app[component.id]]
        elif exposure_truncated and component.id in element_for_app:
            exposure_reason = EXPOSURE_TRUNCATED_REASON
        else:
            exposure_reason = EXPOSURE_NOT_COMPUTED_REASON
        latest_test = max(tests, key=lambda t: (t.tested_at or datetime.min, t.id)) if tests else None
        evidence_owner = users.get(mapping.evidence_owner_id)
        app_owners = owner_rows.get(component.id, []) if component is not None else []
        rows.append({
            "id": mapping.id,
            "control_code": control.control_code if control else None,
            "control_title": control.title if control else None,
            "priority": (control.priority if control else None) or "medium",
            "framework_code": framework.code if framework else None,
            "application_id": component.id if component is not None else None,
            "application_name": component.name if component is not None else "Organisation-wide",
            "state": state,
            "effectiveness": effect,
            "latest_test_result": latest_test.result if latest_test else None,
            "latest_test_notes": latest_test.notes if latest_test else None,
            "valid_until": _day(mapping.evidence_valid_until),
            "exposure": exposure,
            "exposure_reason": exposure_reason,
            "application_owners": [o["user_name"] for o in app_owners],
            "evidence_owner_id": mapping.evidence_owner_id,
            "evidence_owner": _person_name(evidence_owner) if evidence_owner else "—",
            # The four properties each finding row carries.
            "evidence": mapping.evidence_url or "—",
            "provenance": {
                "source": mapping.evidence_source,
                "who": _person_name(evidence_owner) if evidence_owner else "—",
                "collected_at": _day(mapping.evidence_collected_at),
            },
            "confidence": None,
            "confidence_basis": "drawn_facts_only",
            "trace": {
                "control_id": mapping.control_id,
                "framework_id": framework.id if framework else None,
                "adopted_row_id": mapping.id,
                "application_id": component.id if component is not None else None,
                "element_id": element_id,
            },
        })

    rows.sort(
        key=lambda r: (
            _PRIORITY_RANK.get(r["priority"], 2),
            -(r["exposure"] if r["exposure"] is not None else -1),
            r["valid_until"] or "9999-12-31",
            r["id"],
        )
    )
    total = len(rows)
    return {
        **empty,
        "rows": rows[:_ROW_LIMIT],
        "total": total,
        "untested": untested[:_ROW_LIMIT],
        "untested_total": len(untested),
    }


def propose_control_evidence(
    org_id: int,
    application_control_id: int,
    *,
    evidence_url: str,
    collected_at: datetime,
    valid_until: Optional[datetime],
    source_system: str,
    summary: str,
    proposed_by_user_id: Optional[int] = None,
):
    """Raise a pending ``control_evidence`` proposal for what a connector sync found.

    Nothing is written to the control: another user accepts the proposal in the approval
    queue and only then does :func:`accept_control_evidence` write the evidence columns.
    Refuses (``ValueError``) a control row that is not in ``org_id``.
    """
    from app.models.application_compliance import ApplicationComplianceControl
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    if org_id is None:
        raise ValueError("Evidence proposals need an organisation")
    mapping = db.session.execute(
        db.select(ApplicationComplianceControl)
        .where(ApplicationComplianceControl.id == application_control_id)
        .where(ApplicationComplianceControl.organization_id == org_id)
    ).scalar_one_or_none()
    if mapping is None:
        raise ValueError(f"Control {application_control_id} is not in this organisation")
    payload = {
        "application_control_id": mapping.id,
        "evidence_url": evidence_url,
        "collected_at": collected_at.isoformat() if collected_at else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "source_system": source_system,
    }
    return create_approval_record(
        organization_id=org_id,
        operation_type="update",
        entity_type=ENTITY_CONTROL_EVIDENCE,
        entity_id=mapping.id,
        summary=summary,
        operation_payload=payload,
        user_id=proposed_by_user_id,
    )


def accept_control_evidence(approval_id: int, organization_id: int, accepted_by: Optional[int] = None) -> bool:
    """Write the evidence a ``control_evidence`` proposal carries onto its control row.

    Acts only on a proposal of ``organization_id`` that the approval queue has claimed as
    approved and is dispatching right now; the approver, the audit trail and the refusal of
    self-approval are the queue's. A pending, refused or already executed proposal writes
    nothing and returns ``False``. Re-checks that the control row belongs to the proposal's
    organisation before writing.
    """
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.application_compliance import ApplicationComplianceControl

    approval = (
        AIChatCRUDApproval.query.filter_by(
            id=approval_id, organization_id=organization_id, entity_type=ENTITY_CONTROL_EVIDENCE
        )
        .filter(AIChatCRUDApproval.status == ApprovalStatus.APPROVED)
        .first()
    )
    if approval is None or approval.executed_at is not None:
        logger.warning("accept_control_evidence: approval %s not found or not open", approval_id)
        return False
    try:
        payload = json.loads(approval.operation_payload)
        control_row_id = int(payload["application_control_id"])
        collected_at = datetime.fromisoformat(payload["collected_at"]) if payload.get("collected_at") else None
        valid_until = datetime.fromisoformat(payload["valid_until"]) if payload.get("valid_until") else None
        evidence_url = (payload.get("evidence_url") or "").strip()
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        logger.error("accept_control_evidence: invalid payload on approval %s", approval_id)
        return False
    if not evidence_url:
        return False

    mapping = db.session.execute(
        db.select(ApplicationComplianceControl)
        .where(ApplicationComplianceControl.id == control_row_id)
        .where(ApplicationComplianceControl.organization_id == organization_id)
    ).scalar_one_or_none()
    if mapping is None:
        logger.error("accept_control_evidence: control %s is not in organisation %s", control_row_id, organization_id)
        return False

    logger.info("accept_control_evidence: approval %s accepted by user %s", approval_id, accepted_by)
    mapping.evidence_url = evidence_url[:500]
    mapping.evidence_collected_at = collected_at
    mapping.evidence_valid_until = valid_until
    mapping.evidence_source = "connector"
    db.session.add(mapping)
    db.session.flush()
    return True
