"""The overlay a scenario branch lays over the base model.

A branch is an ordered list of changes. This module folds them into one small
description of what differs from the base model, which the one impact walk
reads: relationships that no longer exist, relationships and elements that
exist only on the branch, and elements renamed or retyped on it. Nothing is
copied from the base model and nothing is written to it.

Changes apply in the order they were recorded, so a later change to the same
target wins. An element or relationship a branch adds is named by a negative id
(``-<change id>``); a later change can target it by that id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Set


@dataclass
class ScenarioOverlay:
    branch_id: int
    added_elements: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    element_changes: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    removed_elements: Set[int] = field(default_factory=set)
    added_relationships: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    removed_relationships: Set[int] = field(default_factory=set)
    relationship_types: Dict[int, str] = field(default_factory=dict)

    # -- questions the walk asks ------------------------------------------------
    def element_exists(self, element_id: int) -> bool:
        """True for an element added on the branch, False once the branch removed it."""
        return element_id in self.added_elements and element_id not in self.removed_elements

    def drops_relationship(self, rel_id: int, source_id: int, target_id: int) -> bool:
        return (
            rel_id in self.removed_relationships
            or source_id in self.removed_elements
            or target_id in self.removed_elements
        )

    def relationship_type(self, rel_id: int, base_type: Optional[str]) -> Optional[str]:
        return self.relationship_types.get(rel_id, base_type)

    def relationships_from(self, source_ids: Iterable[int]):
        wanted = set(source_ids)
        return [
            (rid, r) for rid, r in sorted(self.added_relationships.items(), reverse=True)
            if r["source_id"] in wanted and not self.drops_relationship(rid, r["source_id"], r["target_id"])
        ]

    def relationships_to(self, target_ids: Iterable[int]):
        wanted = set(target_ids)
        return [
            (rid, r) for rid, r in sorted(self.added_relationships.items(), reverse=True)
            if r["target_id"] in wanted and not self.drops_relationship(rid, r["source_id"], r["target_id"])
        ]

    def describe_element(self, element_id: int, base: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The element as the branch sees it: base fields with the branch's changes on top."""
        if element_id in self.removed_elements:
            return None
        if element_id in self.added_elements:
            added = self.added_elements[element_id]
            return {"id": element_id, "name": added["name"], "type": added["type"], "layer": added["layer"]}
        if base is None:
            return None
        changed = self.element_changes.get(element_id)
        if not changed:
            return base
        return {**base, **changed}


def build_overlay(branch_id: int, changes: Iterable[Any]) -> ScenarioOverlay:
    """Fold a branch's changes, in recorded order, into an overlay."""
    overlay = ScenarioOverlay(branch_id=branch_id)
    for change in sorted(changes, key=lambda c: c.id):
        payload = dict(change.payload or {})
        if change.target_kind == "element":
            _apply_element(overlay, change, payload)
        else:
            _apply_relationship(overlay, change, payload)
    return overlay


def _apply_element(overlay: ScenarioOverlay, change: Any, payload: Dict[str, Any]) -> None:
    if change.change_kind == "add":
        overlay.added_elements[change.added_id] = {
            "name": payload["name"], "type": payload["type"], "layer": payload["layer"],
        }
    elif change.change_kind == "change":
        target = change.target_id
        if target in overlay.added_elements:
            overlay.added_elements[target].update(payload)
        else:
            overlay.element_changes.setdefault(target, {}).update(payload)
    elif change.change_kind == "remove":
        target = change.target_id
        if target in overlay.added_elements:
            del overlay.added_elements[target]
            for rid in [
                rid for rid, r in overlay.added_relationships.items() if target in (r["source_id"], r["target_id"])
            ]:
                del overlay.added_relationships[rid]
        else:
            overlay.removed_elements.add(target)


def _apply_relationship(overlay: ScenarioOverlay, change: Any, payload: Dict[str, Any]) -> None:
    if change.change_kind == "add":
        overlay.added_relationships[change.added_id] = {
            "source_id": payload["source_id"], "target_id": payload["target_id"], "type": payload["type"],
        }
    elif change.change_kind == "change":
        target = change.target_id
        if target in overlay.added_relationships:
            overlay.added_relationships[target]["type"] = payload["type"]
        else:
            overlay.relationship_types[target] = payload["type"]
    elif change.change_kind == "remove":
        target = change.target_id
        if target in overlay.added_relationships:
            del overlay.added_relationships[target]
        else:
            overlay.removed_relationships.add(target)
