"""Scenario branches: create, read, rename, delete and record changes.

Every function takes the organisation explicitly and filters by it as well as
by the tenant listener, so a branch of another organisation behaves as absent
(``ScenarioNotFound``) from every entry point. A change's organisation is taken
from its branch, never from the request. Nothing here writes to the base
element or relationship tables.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from app import db
from app.models.scenario import CHANGE_KINDS, TARGET_KINDS, ScenarioBranch, ScenarioChange
from app.modules.genome.patch.schema import ARCHIMATE_TYPE_LAYER
from app.modules.intelligence.services.scenario_overlay import ScenarioOverlay, build_overlay

MAX_NAME_LENGTH = 200
MAX_CHANGES_PER_BRANCH = 500


class ScenarioError(Exception):
    """A refusal the route turns into a plain message at ``status_code``."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ScenarioNotFound(ScenarioError):
    def __init__(self, message: str = "Scenario not found"):
        super().__init__(message, 404)


def _clean_name(raw: Optional[str]) -> str:
    name = (raw or "").strip()
    if not name:
        raise ScenarioError("Give the scenario a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise ScenarioError(f"A scenario name is at most {MAX_NAME_LENGTH} characters.")
    return name


# -- branches ----------------------------------------------------------------
def list_branches(org_id: int) -> List[ScenarioBranch]:
    return (
        ScenarioBranch.query.filter(ScenarioBranch.organization_id == org_id)
        .order_by(ScenarioBranch.name)
        .all()
    )


def get_branch(org_id: int, branch_id: int) -> ScenarioBranch:
    """The organisation's branch, or ``ScenarioNotFound`` (also for another organisation's id)."""
    # A query, not .get(): .get() answers from the identity map without SQL.
    branch = ScenarioBranch.query.filter(
        ScenarioBranch.id == branch_id, ScenarioBranch.organization_id == org_id
    ).first()
    if branch is None:
        raise ScenarioNotFound()
    return branch


def create_branch(org_id: int, name: str, description: Optional[str], user_id: Optional[int]) -> ScenarioBranch:
    name = _clean_name(name)
    branch = ScenarioBranch(
        organization_id=org_id, name=name, description=(description or "").strip() or None, created_by_id=user_id
    )
    db.session.add(branch)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise ScenarioError("You already have a scenario with that name.", 409)
    return branch


def rename_branch(org_id: int, branch_id: int, name: Optional[str], description: Optional[str]) -> ScenarioBranch:
    branch = get_branch(org_id, branch_id)
    if name is not None:
        branch.name = _clean_name(name)
    if description is not None:
        branch.description = description.strip() or None
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise ScenarioError("You already have a scenario with that name.", 409)
    return branch


def delete_branch(org_id: int, branch_id: int) -> None:
    branch = get_branch(org_id, branch_id)
    db.session.delete(branch)
    db.session.flush()


# -- changes -----------------------------------------------------------------
def overlay_for(org_id: Optional[int], branch_id: int) -> Optional[ScenarioOverlay]:
    """The branch's overlay, or None when the branch is not this organisation's."""
    if org_id is None:
        return None
    try:
        branch = get_branch(org_id, int(branch_id))
    except ScenarioNotFound:
        return None
    return build_overlay(branch.id, branch.changes)


def _real_element_exists(org_id: int, element_id: int) -> bool:
    from app.models import ArchiMateElement

    return (
        db.session.execute(
            db.select(ArchiMateElement.id).where(
                ArchiMateElement.id == element_id, ArchiMateElement.organization_id == org_id
            )
        ).first()
        is not None
    )


def _real_relationship_exists(org_id: int, relationship_id: int) -> bool:
    from app.models import ArchiMateRelationship

    return (
        db.session.execute(
            db.select(ArchiMateRelationship.id).where(
                ArchiMateRelationship.id == relationship_id, ArchiMateRelationship.organization_id == org_id
            )
        ).first()
        is not None
    )


def _valid_relationship_types() -> set:
    from app.models.archimate_element_types import VALID_RELATIONSHIP_TYPES

    return {t.lower() for t in VALID_RELATIONSHIP_TYPES}


def _as_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ScenarioError(f"{what} must be a number.")
    return value


def _known_element(org_id: int, overlay: ScenarioOverlay, element_id: int, what: str) -> None:
    """The element exists on this branch: a base element not removed here, or one added here."""
    if element_id < 0:
        if not overlay.element_exists(element_id):
            raise ScenarioError(f"{what} is not an element on this scenario.")
        return
    if element_id in overlay.removed_elements or not _real_element_exists(org_id, element_id):
        raise ScenarioError(f"{what} is not an element in your model.")


def _validate(org_id: int, overlay: ScenarioOverlay, kind: str, target_kind: str, target_id, payload) -> Dict[str, Any]:
    payload = dict(payload or {})
    if kind not in CHANGE_KINDS:
        raise ScenarioError("A change is an add, a change or a remove.")
    if target_kind not in TARGET_KINDS:
        raise ScenarioError("A change is about an element or a relationship.")

    if kind == "add":
        if target_id is not None:
            raise ScenarioError("An addition has no target; it is named when it is added.")
        if target_kind == "element":
            name = _clean_name(payload.get("name"))
            element_type = payload.get("type")
            if element_type not in ARCHIMATE_TYPE_LAYER:
                raise ScenarioError("That is not an element type.")
            return {"name": name, "type": element_type, "layer": ARCHIMATE_TYPE_LAYER[element_type]}
        source = _as_int(payload.get("source_id"), "The source")
        target = _as_int(payload.get("target_id"), "The target")
        _known_element(org_id, overlay, source, "The source")
        _known_element(org_id, overlay, target, "The target")
        rel_type = str(payload.get("type") or "").lower()
        if rel_type not in _valid_relationship_types():
            raise ScenarioError("That is not a relationship type.")
        return {"source_id": source, "target_id": target, "type": rel_type}

    target = _as_int(target_id, "The target")
    if target_kind == "element":
        _known_element(org_id, overlay, target, "The target")
        if kind == "remove":
            return {}
        out: Dict[str, Any] = {}
        if "name" in payload:
            out["name"] = _clean_name(payload["name"])
        if "type" in payload:
            if payload["type"] not in ARCHIMATE_TYPE_LAYER:
                raise ScenarioError("That is not an element type.")
            out["type"] = payload["type"]
            out["layer"] = ARCHIMATE_TYPE_LAYER[payload["type"]]
        if not out:
            raise ScenarioError("Say what changes: a name or a type.")
        return out

    # relationship change / remove
    if target < 0:
        if target not in overlay.added_relationships:
            raise ScenarioError("The target is not a relationship on this scenario.")
    elif target in overlay.removed_relationships or not _real_relationship_exists(org_id, target):
        raise ScenarioError("The target is not a relationship in your model.")
    if kind == "remove":
        return {}
    rel_type = str(payload.get("type") or "").lower()
    if rel_type not in _valid_relationship_types():
        raise ScenarioError("That is not a relationship type.")
    return {"type": rel_type}


def add_change(
    org_id: int,
    branch_id: int,
    change_kind: str,
    target_kind: str,
    target_id,
    payload,
    user_id: Optional[int],
) -> ScenarioChange:
    branch = get_branch(org_id, branch_id)
    if len(branch.changes) >= MAX_CHANGES_PER_BRANCH:
        raise ScenarioError(f"A scenario holds at most {MAX_CHANGES_PER_BRANCH} changes.")
    overlay = build_overlay(branch.id, branch.changes)
    clean = _validate(org_id, overlay, change_kind, target_kind, target_id, payload)
    change = ScenarioChange(
        organization_id=branch.organization_id,  # from the branch, never from the request
        branch_id=branch.id,
        change_kind=change_kind,
        target_kind=target_kind,
        target_id=None if change_kind == "add" else _as_int(target_id, "The target"),
        payload=clean,
        created_by_id=user_id,
    )
    db.session.add(change)
    db.session.flush()
    return change


def remove_change(org_id: int, branch_id: int, change_id: int) -> None:
    """Take back the last change recorded on the branch (earlier ones may be built upon)."""
    branch = get_branch(org_id, branch_id)
    change = next((c for c in branch.changes if c.id == change_id), None)
    if change is None:
        raise ScenarioNotFound("Change not found")
    if branch.changes and branch.changes[-1].id != change.id:
        raise ScenarioError("Only the most recent change can be taken back.", 409)
    db.session.delete(change)
    db.session.flush()
