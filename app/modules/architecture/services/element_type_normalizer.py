"""
Element Type Normalizer

Normalizes ArchiMate element types from LLM responses to match MODEL_REGISTRY keys.
Handles variations in naming (spaces, case, abbreviations) and maps to correct types.
"""

import logging
from typing import Dict, Optional
from app.models.archimate_element_types import StandardTypeTable

logger = logging.getLogger(__name__)


class ElementTypeNormalizer:
    """Normalizes element type names to match MODEL_REGISTRY keys."""

    # Mapping of common variations to canonical types
    TYPE_MAPPINGS = {
        # Application Layer variations
        "application component": "ApplicationComponent",
        "applicationcomponent": "ApplicationComponent",
        "app component": "ApplicationComponent",
        "application": "ApplicationComponent",
        "app": "ApplicationComponent",
        "application interface": "ApplicationInterface",
        "applicationinterface": "ApplicationInterface",
        "app interface": "ApplicationInterface",
        "api": "ApplicationInterface",
        "application service": "ApplicationService",
        "applicationservice": "ApplicationService",
        "app service": "ApplicationService",
        "application function": "ApplicationFunction",
        "applicationfunction": "ApplicationFunction",
        "app function": "ApplicationFunction",
        "application process": "ApplicationProcess",
        "applicationprocess": "ApplicationProcess",
        "app process": "ApplicationProcess",
        "application interaction": "ApplicationInteraction",
        "applicationinteraction": "ApplicationInteraction",
        "application event": "ApplicationEvent",
        "applicationevent": "ApplicationEvent",
        "app event": "ApplicationEvent",
        "application collaboration": "ApplicationCollaboration",
        "applicationcollaboration": "ApplicationCollaboration",
        "data object": "DataObject",
        "dataobject": "DataObject",
        "data": "DataObject",
        "database": "DataObject",
        # Business Layer variations
        "business actor": "BusinessActor",
        "businessactor": "BusinessActor",
        "actor": "BusinessActor",
        "business role": "BusinessRole",
        "businessrole": "BusinessRole",
        "role": "BusinessRole",
        "business process": "BusinessProcess",
        "businessprocess": "BusinessProcess",
        "process": "BusinessProcess",
        "business function": "BusinessFunction",
        "businessfunction": "BusinessFunction",
        "function": "BusinessFunction",
        "business service": "BusinessService",
        "businessservice": "BusinessService",
        "service": "BusinessService",
        "business object": "BusinessObject",
        "businessobject": "BusinessObject",
        "object": "BusinessObject",
        "business event": "BusinessEvent",
        "businessevent": "BusinessEvent",
        "event": "BusinessEvent",
        "business collaboration": "BusinessCollaboration",
        "businesscollaboration": "BusinessCollaboration",
        "collaboration": "BusinessCollaboration",
        "business interface": "BusinessInterface",
        "businessinterface": "BusinessInterface",
        "business interaction": "BusinessInteraction",
        "businessinteraction": "BusinessInteraction",
        "product": "Product",
        "contract": "Contract",
        # Technology Layer variations
        "node": "Node",
        "device": "Device",
        "system software": "SystemSoftware",
        "systemsoftware": "SystemSoftware",
        "software": "SystemSoftware",
        "technology service": "TechnologyService",
        "technologyservice": "TechnologyService",
        "tech service": "TechnologyService",
        "technology interface": "TechnologyInterface",
        "technologyinterface": "TechnologyInterface",
        "tech interface": "TechnologyInterface",
        "path": "Path",
        "communication network": "CommunicationNetwork",
        "communicationnetwork": "CommunicationNetwork",
        "network": "CommunicationNetwork",
        # Strategy Layer variations
        "resource": "Resource",
        "capability": "Capability",
        "value stream": "ValueStream",
        "valuestream": "ValueStream",
        "course of action": "CourseOfAction",
        "courseofaction": "CourseOfAction",
        # Motivation Layer variations
        "stakeholder": "Stakeholder",
        "driver": "Driver",
        "assessment": "Assessment",
        "goal": "Goal",
        "outcome": "Outcome",
        "principle": "Principle",
        "requirement": "Requirement",
        "constraint": "Constraint",
        "meaning": "Meaning",
        "value": "Value",
        # Implementation Layer variations
        "work package": "WorkPackage",
        "workpackage": "WorkPackage",
        "deliverable": "Deliverable",
        "plateau": "Plateau",
        "gap": "Gap",
    }

    # Layer inference from element type
    LAYER_MAP = StandardTypeTable({
        "ApplicationComponent": "application",
        "ApplicationInterface": "application",
        "ApplicationService": "application",
        "ApplicationFunction": "application",
        "ApplicationProcess": "application",
        "ApplicationInteraction": "application",
        "ApplicationEvent": "application",
        "ApplicationCollaboration": "application",
        "DataObject": "application",
        "BusinessActor": "business",
        "BusinessRole": "business",
        "BusinessProcess": "business",
        "BusinessFunction": "business",
        "BusinessService": "business",
        "BusinessObject": "business",
        "BusinessEvent": "business",
        "BusinessCollaboration": "business",
        "BusinessInterface": "business",
        "BusinessInteraction": "business",
        "Product": "business",
        "Contract": "business",
        "Representation": "business",
        "Node": "technology",
        "Device": "technology",
        "SystemSoftware": "technology",
        "TechnologyCollaboration": "technology",
        "TechnologyInterface": "technology",
        "Path": "technology",
        "CommunicationNetwork": "technology",
        "TechnologyFunction": "technology",
        "TechnologyProcess": "technology",
        "TechnologyInteraction": "technology",
        "TechnologyEvent": "technology",
        "TechnologyService": "technology",
        "Artifact": "technology",
        "Equipment": "physical",
        "Facility": "physical",
        "DistributionNetwork": "physical",
        "Material": "physical",
        "Resource": "strategy",
        "Capability": "strategy",
        "ValueStream": "strategy",
        "CourseOfAction": "strategy",
        "Stakeholder": "motivation",
        "Driver": "motivation",
        "Assessment": "motivation",
        "Goal": "motivation",
        "Outcome": "motivation",
        "Principle": "motivation",
        "Requirement": "motivation",
        "Constraint": "motivation",
        "Meaning": "motivation",
        "Value": "motivation",
        "WorkPackage": "implementation",
        "Deliverable": "implementation",
        "ImplementationEvent": "implementation",
        "Plateau": "implementation",
        "Gap": "implementation",
    })

    @classmethod
    def normalize_type(cls, element_type: str) -> Optional[str]:
        """
        Normalize element type to match MODEL_REGISTRY key.

        Args:
            element_type: Raw element type from LLM response

        Returns:
            Normalized type name or None if not found
        """
        if not element_type:
            return None

        # Convert to lowercase and strip whitespace
        normalized = element_type.strip().lower()

        # Direct lookup in mappings
        if normalized in cls.TYPE_MAPPINGS:
            return cls.TYPE_MAPPINGS[normalized]

        # Try removing spaces and checking again
        no_spaces = normalized.replace(" ", "").replace("-", "").replace("_", "")
        if no_spaces in cls.TYPE_MAPPINGS:
            return cls.TYPE_MAPPINGS[no_spaces]

        # Try exact match (case-insensitive) with canonical types
        for canonical_type in cls.LAYER_MAP.keys():
            if canonical_type.lower() == normalized or canonical_type.lower() == no_spaces:
                return canonical_type

        # Try partial matching (e.g., "Application" matches "ApplicationComponent")
        for canonical_type in cls.LAYER_MAP.keys():
            if normalized in canonical_type.lower() or canonical_type.lower() in normalized:
                # Prefer exact matches over partial
                if len(normalized) >= len(canonical_type) * 0.7:  # At least 70% match
                    logger.debug(f"Partial match: '{element_type}' -> '{canonical_type}'")
                    return canonical_type

        logger.warning(f"Could not normalize element type: '{element_type}'")
        return None

    @classmethod
    def infer_layer(cls, element_type: str) -> Optional[str]:
        """
        Infer layer from element type.

        Args:
            element_type: Normalized element type

        Returns:
            Layer name or None if not found
        """
        if not element_type:
            return None

        # First normalize the type
        normalized_type = cls.normalize_type(element_type)
        if not normalized_type:
            return None

        return cls.LAYER_MAP.get(normalized_type)

    @classmethod
    def normalize_element(cls, element: Dict) -> Dict:
        """
        Normalize an element dictionary, fixing type and layer.

        Args:
            element: Element dictionary from LLM response

        Returns:
            Normalized element dictionary
        """
        if not isinstance(element, dict):
            return element

        normalized = element.copy()

        # Normalize type
        original_type = element.get("type", "")
        if original_type:
            normalized_type = cls.normalize_type(original_type)
            if normalized_type:
                normalized["type"] = normalized_type

                # Always set layer from element type — ArchiMate 3.2 layer is
                # deterministic per type. LLMs frequently assign wrong layers.
                correct_layer = cls.LAYER_MAP.get(normalized_type)
                if correct_layer:
                    if normalized.get("layer") and normalized["layer"] != correct_layer:
                        logger.info(
                            f"Correcting layer for '{element.get('name', '?')}' "
                            f"({normalized_type}): '{normalized['layer']}' -> '{correct_layer}'"
                        )
                    normalized["layer"] = correct_layer
            else:
                # Type couldn't be normalized - remove type to signal it should be skipped
                logger.warning(
                    f"Element '{element.get('name', 'Unknown')}' has unrecognized type: '{original_type}'"
                )
                normalized["type"] = None  # Signal that this element should be skipped
        else:
            # No type provided - remove it
            normalized["type"] = None

        return normalized

    @classmethod
    def normalize_elements(cls, elements: list) -> list:
        """
        Normalize a list of elements.

        Args:
            elements: List of element dictionaries

        Returns:
            List of normalized element dictionaries
        """
        if not isinstance(elements, list):
            return []

        normalized = []
        skipped = []

        for elem in elements:
            if not isinstance(elem, dict):
                continue

            normalized_elem = cls.normalize_element(elem)

            # Only include if type was successfully normalized
            if normalized_elem.get("type"):
                normalized.append(normalized_elem)
            else:
                skipped.append(elem.get("name", "Unknown"))

        if skipped:
            logger.warning(
                f"Skipped {len(skipped)} elements with unrecognized types: {skipped[:5]}"
            )

        return normalized
