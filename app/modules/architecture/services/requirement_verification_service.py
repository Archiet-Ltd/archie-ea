"""Which requirements a test verifies, and what that test last did.

A test is an Artifact. "Verifies" is a specialisation of realization from that Artifact to a
Requirement (ADR-0014): the stored relationship is a standard realization carrying the
``entelim:specialization`` property, so an exchange file opens in any ArchiMate tool and Entelim
reads the specialisation back. The last result is three properties on the Artifact
(``entelim:last_result``, ``entelim:commit``, ``entelim:run_at``), kept in ``custom_properties``,
the one property store the exchange export and import already carry.

A requirement with no verifying test is unverified. With any verifying test whose last result is
failed it is failing. Otherwise it is verified, and a test that has no result yet counts as not
yet run, not as passed.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_element_types import SPECIALIZATION_PROPERTY
from app.services.archimate_backbone import create_backbone_element

VERIFIES = "verifies"
RESULT_KEY = "entelim:last_result"
COMMIT_KEY = "entelim:commit"
RUN_AT_KEY = "entelim:run_at"
RESULTS = ("passed", "failed")

UNVERIFIED = "unverified"
FAILING = "failing"
VERIFIED = "verified"
NOT_RUN = "not_run"


class VerificationError(ValueError):
    """The request cannot be recorded; the message is for the person who made it."""


def _own(model, row_id: int, org_id: int, kind: str):
    row = db.session.execute(
        db.select(model).where(model.id == row_id, model.organization_id == org_id)
    ).scalar_one_or_none()
    if row is None:
        raise VerificationError(f"That {kind} is not in your organisation.")
    return row


def _is_verifies(rel) -> bool:
    props = rel.custom_properties or {}
    return rel.type == "realization" and props.get(SPECIALIZATION_PROPERTY) == VERIFIES


def _result_of(test) -> Dict[str, Any]:
    props = test.custom_properties or {}
    result = props.get(RESULT_KEY)
    return {
        "result": result if result in RESULTS else None,
        "commit": props.get(COMMIT_KEY) or None,
        "run_at": props.get(RUN_AT_KEY) or None,
    }


def _links(org_id: int, requirement_ids: Optional[List[int]] = None):
    stmt = db.select(ArchiMateRelationship).where(
        ArchiMateRelationship.organization_id == org_id,
        ArchiMateRelationship.type == "realization",
    )
    if requirement_ids is not None:
        stmt = stmt.where(ArchiMateRelationship.target_id.in_(requirement_ids))
    return [r for r in db.session.execute(stmt).scalars() if _is_verifies(r)]


def _tests_by_requirement(org_id: int, requirement_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    if not requirement_ids:
        return {}
    links = _links(org_id, requirement_ids)
    test_ids = {link.source_id for link in links}
    tests = {
        t.id: t
        for t in db.session.execute(
            db.select(ArchiMateElement).where(
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.type == "Artifact",
                ArchiMateElement.id.in_(test_ids or {0}),
            )
        ).scalars()
    }
    found: Dict[int, List[Dict[str, Any]]] = {rid: [] for rid in requirement_ids}
    for link in links:
        test = tests.get(link.source_id)
        if test is not None:
            found[link.target_id].append({"id": test.id, "name": test.name, **_result_of(test)})
    for rows in found.values():
        rows.sort(key=lambda t: ((t["name"] or "").lower(), t["id"]))
    return found


def state_of(tests: List[Dict[str, Any]]) -> str:
    if not tests:
        return UNVERIFIED
    if any(t["result"] == "failed" for t in tests):
        return FAILING
    if any(t["result"] == "passed" for t in tests):
        return VERIFIED
    return NOT_RUN


def requirement_view(requirement_id: int, org_id: int) -> Dict[str, Any]:
    """The tests that verify one requirement, each with its last result, and the requirement's state."""
    tests = _tests_by_requirement(org_id, [requirement_id]).get(requirement_id, [])
    return {"tests": tests, "state": state_of(tests)}


def candidate_tests(requirement_id: int, org_id: int) -> List[Dict[str, Any]]:
    """The organisation's tests (Artifacts) not yet linked to this requirement."""
    linked = {t["id"] for t in _tests_by_requirement(org_id, [requirement_id]).get(requirement_id, [])}
    rows = db.session.execute(
        db.select(ArchiMateElement.id, ArchiMateElement.name)
        .where(ArchiMateElement.organization_id == org_id, ArchiMateElement.type == "Artifact")
        .order_by(ArchiMateElement.name, ArchiMateElement.id)
    ).all()
    return [{"id": r.id, "name": r.name} for r in rows if r.id not in linked]


def coverage(org_id: int) -> Dict[str, Any]:
    """The organisation's requirements split into unverified and last failed.

    ``total`` is every requirement counted; ``rows`` are the two lists the page shows.
    """
    requirements = db.session.execute(
        db.select(ArchiMateElement.id, ArchiMateElement.name)
        .where(ArchiMateElement.organization_id == org_id, ArchiMateElement.type == "Requirement")
        .order_by(ArchiMateElement.name, ArchiMateElement.id)
    ).all()
    by_requirement = _tests_by_requirement(org_id, [r.id for r in requirements])
    unverified: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for req in requirements:
        tests = by_requirement.get(req.id, [])
        state = state_of(tests)
        if state == UNVERIFIED:
            unverified.append({"id": req.id, "name": req.name})
        elif state == FAILING:
            failing = [t for t in tests if t["result"] == "failed"]
            failed.append({"id": req.id, "name": req.name, "tests": failing})
    return {"total": len(requirements), "unverified": unverified, "failed": failed}


def link_test(requirement_id: int, org_id: int, test_id: Optional[int] = None, new_test_name: str = "") -> ArchiMateElement:
    """Link a test to a requirement with a verifies relationship; create the test when named new."""
    requirement = _own(ArchiMateElement, requirement_id, org_id, "requirement")
    if requirement.type != "Requirement":
        raise VerificationError("Only a requirement can be verified by a test.")
    name = (new_test_name or "").strip()
    if test_id:
        test = _own(ArchiMateElement, test_id, org_id, "test")
        if test.type != "Artifact":
            raise VerificationError("Only an artifact can be recorded as a test.")
    elif name:
        test = create_backbone_element(
            element_type="Artifact", layer="technology", name=name, organization_id=org_id
        )
    else:
        raise VerificationError("Pick a test, or give the new test a name.")

    if any(t["id"] == test.id for t in _tests_by_requirement(org_id, [requirement.id])[requirement.id]):
        raise VerificationError(f"{test.name} already verifies this requirement.")

    from app.modules.architecture.services.archimate_relationship_service import ArchiMateRelationshipService

    relationship = ArchiMateRelationshipService.create_relationship(
        test, requirement, "realization", None, organization_id=org_id, rollback=False
    )
    if relationship is None:
        raise VerificationError("That test cannot be linked to this requirement.")
    relationship.custom_properties = {SPECIALIZATION_PROPERTY: VERIFIES}
    db.session.commit()
    return test


def record_result(test_id: int, org_id: int, result: str, commit: str = "", run_at: Optional[datetime] = None) -> Dict[str, Any]:
    """Record a test's last result, with the commit it ran against and when."""
    test = _own(ArchiMateElement, test_id, org_id, "test")
    if test.type != "Artifact":
        raise VerificationError("Only an artifact can be recorded as a test.")
    if result not in RESULTS:
        raise VerificationError("The result is either passed or failed.")
    props = dict(test.custom_properties or {})
    props[RESULT_KEY] = result
    props[COMMIT_KEY] = (commit or "").strip()[:64]
    props[RUN_AT_KEY] = (run_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    test.custom_properties = props
    db.session.commit()
    return _result_of(test)
