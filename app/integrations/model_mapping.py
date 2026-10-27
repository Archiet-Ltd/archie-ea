"""Model mapping: external system → ArchiMate facts.

Defines the extension point every integration mapping implements. A mapping
declares which ArchiMate facts an external item becomes: elements, relationships,
owners.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class SourceRef:
    """Where an ArchiMate fact came from.

    Attributes:
        system: The external system (github, jira, servicenow, etc.)
        external_id: The item's id in that system
        item_type: The item's type (repo, issue, service, etc.)
        url: Direct link to the item in the external system
        read_at: When the fact was derived
    """
    system: str
    external_id: str
    item_type: str
    url: str
    read_at: datetime


@dataclass
class ElementFact:
    """An ArchiMate element derived from an external item.

    Attributes:
        archimate_type: ArchiMate element type (ApplicationComponent, BusinessActor, etc.)
        name: Element name
        description: Element description (optional)
        properties: Additional properties for the element (optional)
    """
    archimate_type: str
    name: str
    description: Optional[str] = None
    properties: Optional[Dict] = None


@dataclass
class RelationshipFact:
    """An ArchiMate relationship derived from an external item.

    Attributes:
        type: ArchiMate relationship type (composition, serving, etc.)
        source_path: Path to the source element fact (e.g., "element" or "team")
        target_path: Path to the target element fact (e.g., "element" or "dependencies.0")
        description: Relationship description (optional)
        properties: Additional properties for the relationship (optional)
    """
    type: str
    source_path: str
    target_path: str
    description: Optional[str] = None
    properties: Optional[Dict] = None


@dataclass
class OwnerFact:
    """An owner derived from an external item.

    Attributes:
        element_path: Path to the element fact this owns (e.g., "element")
        owner_name: Name of the owner (person or team)
        owner_email: Email of the owner (optional, for person owners)
        ownership_type: Type of ownership (primary, business, technical, etc.)
        is_team: Whether this is a team owner (BusinessActor) rather than a person
    """
    element_path: str
    owner_name: str
    owner_email: Optional[str] = None
    ownership_type: str = "primary"
    is_team: bool = False


class ModelMapping:
    """Base class for integration mappings.

    A mapping declares which ArchiMate facts an external item becomes. Subclasses
    implement `map_item` to return the facts for one item.
    """

    system_name: str = ""  # e.g., "github", "jira"
    display_name: str = ""  # how people read the system, e.g. "GitHub"

    def map_item(
        self,
        item: Dict,
        organization_id: int,
        *,
        context: Optional[Dict] = None,
    ) -> Dict[str, ElementFact | List[RelationshipFact] | List[OwnerFact]]:
        """Map an external item to ArchiMate facts.

        Args:
            item: The external item (from the API)
            organization_id: The organization this mapping is for
            context: Additional context for the mapping (optional)

        Returns:
            A dict with keys:
                - 'source': SourceRef for this item
                - 'element': The primary ElementFact
                - 'relationships': List of RelationshipFact (optional)
                - 'owners': List of OwnerFact (optional)
                - Additional ElementFact keyed by path (e.g., 'team')
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement map_item")


# Registry: system_name -> ModelMapping class
MAPPINGS: Dict[str, type[ModelMapping]] = {}


def register_mapping(cls: type[ModelMapping]) -> type[ModelMapping]:
    """Decorator to register a ModelMapping class."""
    if not cls.system_name:
        raise ValueError(f"{cls.__name__} must set system_name")
    MAPPINGS[cls.system_name] = cls
    return cls
