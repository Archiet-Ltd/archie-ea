"""Writers for an adopted control's evidence and tests.

Every function takes the caller's organisation and looks the control row up directly with
``organization_id`` in the statement, so a row id that belongs to another organisation
(or to nobody) is refused the same way: :class:`ControlNotFound`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app import db
from app.models.control_test import TEST_KINDS, TEST_RESULTS, ControlTest
from app.modules.compliance.services.control_evidence import effectiveness

_UNSET = object()

IMPLEMENTATION_STATUSES = (
    "planned", "in_progress", "implemented", "verified", "not_applicable", "waived",
)


# The roles that may record control tests, evidence and owners (the write routes require them).
CONTROL_WORK_ROLES = (
    "admin",
    "architect",
    "business_architect",
    "security_architect",
    "compliance",
    "compliance_officer",
)

EVIDENCE_URL_MAX = 500


def may_record_control_work(user) -> bool:
    """Whether ``user`` holds a control-work role: the one rule behind the page's actions, the
    write routes and the approval of connector-found evidence. A read-only account (no general
    permission) holds no write role however it is labelled."""
    from app.models.user import Permission
    from app.utils.role_access import get_user_role, is_admin

    if user is None or not user.can(Permission.GENERAL):
        return False
    role = get_user_role(user)
    return bool(is_admin(user) or role in CONTROL_WORK_ROLES or (role or "").endswith("_architect"))


class ControlNotFound(LookupError):
    """The adopted control row is not in the caller's organisation."""


def _own_control(organization_id: Optional[int], control_row_id: int):
    from app.models.application_compliance import ApplicationComplianceControl

    if organization_id is None:
        raise ControlNotFound("no organisation")
    row = db.session.execute(
        db.select(ApplicationComplianceControl)
        .where(ApplicationComplianceControl.id == control_row_id)
        .where(ApplicationComplianceControl.organization_id == organization_id)
    ).scalar_one_or_none()
    if row is None:
        raise ControlNotFound(str(control_row_id))
    return row


def _own_user_id(organization_id: int, user_id: Optional[int]) -> Optional[int]:
    from app.models.user import User

    if user_id is None:
        return None
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            raise ValueError("The evidence owner must be a person's id") from None
    found = db.session.execute(
        db.select(User.id).where(User.id == user_id).where(User.organization_id == organization_id)
    ).scalar_one_or_none()
    if found is None:
        raise ValueError("That person is not in this organisation")
    return found


def record_control_test(
    organization_id: Optional[int],
    control_row_id: int,
    *,
    test_kind: str,
    result: str,
    tested_by_id: Optional[int],
    notes: Optional[str] = None,
    evidence_url: Optional[str] = None,
    tested_at: Optional[datetime] = None,
) -> ControlTest:
    """Record one design or operating test on an adopted control of ``organization_id``."""
    if test_kind not in TEST_KINDS:
        raise ValueError(f"test_kind must be one of {', '.join(TEST_KINDS)}")
    if result not in TEST_RESULTS:
        raise ValueError(f"result must be one of {', '.join(TEST_RESULTS)}")
    control = _own_control(organization_id, control_row_id)
    test = ControlTest(
        organization_id=organization_id,
        application_control_id=control.id,
        test_kind=test_kind,
        result=result,
        tested_by_id=tested_by_id,
        tested_at=tested_at or datetime.utcnow(),
        notes=(notes or "").strip() or None,
        evidence_url=((evidence_url or "").strip() or None),
    )
    db.session.add(test)
    db.session.flush()
    return test


def list_control_tests(organization_id: Optional[int], control_row_id: int) -> Dict[str, Any]:
    control = _own_control(organization_id, control_row_id)
    tests: List[ControlTest] = list(
        db.session.execute(
            db.select(ControlTest)
            .where(ControlTest.application_control_id == control.id)
            .where(ControlTest.organization_id == organization_id)
            .order_by(ControlTest.tested_at.desc(), ControlTest.id.desc())
        ).scalars()
    )
    return {
        "control_id": control.id,
        "effectiveness": effectiveness(tests),
        "tests": [t.to_dict() for t in tests],
    }


def update_control_evidence(
    organization_id: Optional[int],
    control_row_id: int,
    *,
    implementation_status: Any = _UNSET,
    evidence_url: Any = _UNSET,
    notes: Any = _UNSET,
    evidence_valid_until: Any = _UNSET,
    evidence_collected_at: Any = _UNSET,
    evidence_owner_id: Any = _UNSET,
):
    """Set what the caller named on an adopted control; fields left out are kept.

    A person recording evidence sets ``evidence_source`` to ``person``.
    """
    control = _own_control(organization_id, control_row_id)
    if implementation_status is not _UNSET:
        if implementation_status not in IMPLEMENTATION_STATUSES:
            raise ValueError(f"implementation_status must be one of {', '.join(IMPLEMENTATION_STATUSES)}")
        control.implementation_status = implementation_status
    if notes is not _UNSET:
        control.notes = notes
    if evidence_url is not _UNSET:
        new_url = (evidence_url or "").strip() or None
        if new_url and len(new_url) > EVIDENCE_URL_MAX:
            raise ValueError(f"The evidence link can be at most {EVIDENCE_URL_MAX} characters")
        changed = new_url != control.evidence_url
        control.evidence_url = new_url
        if new_url:
            control.evidence_source = "person"
        if changed:
            # A different link is different evidence: the dates of the old one do not carry
            # over. Collected now unless the caller says when; valid until nothing, unless told.
            if evidence_collected_at is _UNSET:
                control.evidence_collected_at = datetime.utcnow() if new_url else None
            if evidence_valid_until is _UNSET:
                control.evidence_valid_until = None
    if evidence_collected_at is not _UNSET:
        control.evidence_collected_at = evidence_collected_at
    if evidence_valid_until is not _UNSET:
        control.evidence_valid_until = evidence_valid_until
    if evidence_owner_id is not _UNSET:
        control.evidence_owner_id = _own_user_id(organization_id, evidence_owner_id)
    db.session.flush()
    return control


UNSET = _UNSET
