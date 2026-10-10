"""
ArchiMate 3.2 Metamodel Validator

Validates ArchiMate elements and relationships against the ArchiMate 3.2 specification.
Provides both rule-based validation and AI-powered quality assessment.
"""

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import joinedload

from app.models import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
from app.models.constants import ArchiMateLayer, ArchiMateRelationshipType
from app.models.business_layer import BusinessService
from app.models.process_data import BusinessProcess
from app.models.relationship_tables import (
    DataObjectStorage,
    InterfaceConsumer,
    ServiceDependency,
    ServiceRealization,
)

RULES_PATH = Path(__file__).with_name("rules").joinpath("archimate_rules.json")


class ArchiMateValidator:
    """Validates ArchiMate models for compliance with ArchiMate 3.2 metamodel."""

    ACCESS_TYPES = ["ReadWrite", "Read", "Write", "Access"]

    def __init__(self):
        self._raw_rules = self._load_rules()
        self.layers: Dict[str, List[str]] = self._raw_rules.get("layers", {})
        self.layer_aliases: Dict[str, str] = {
            alias.lower(): target
            for alias, target in self._raw_rules.get("layer_aliases", {}).items()
        }
        self.relationship_types: List[str] = self._raw_rules.get("relationship_types", [])
        self.relationship_aliases: Dict[str, str] = {
            alias.lower(): target
            for alias, target in self._raw_rules.get("relationship_aliases", {}).items()
        }
        self.cardinality_rules: Dict[str, Dict[str, Any]] = self._raw_rules.get(
            "cardinality_rules", {}
        )

        self.element_type_index: Dict[str, str] = {}
        self.relationship_type_index: Dict[str, str] = {}
        self._build_indexes()

        self.relationship_rules: Dict[
            Tuple[str, str], List[str]
        ] = self._normalize_relationship_map(self._raw_rules.get("relationship_rules", {}))
        self.cardinality_rules = self._normalize_cardinality_rules(self.cardinality_rules)
        self._build_bidirectional_rules()

        canonical_relationship_types = {
            self._canonical_relationship_type(rel_type)
            for rel_type in self.relationship_types
            if rel_type
        }
        self.relationship_types = sorted({rel for rel in canonical_relationship_types if rel})
        self.relationship_type_index = {rel.lower(): rel for rel in self.relationship_types}

    def _load_rules(self) -> Dict[str, Any]:
        """Load rule definitions from disk with a safe fallback."""
        if RULES_PATH.exists():
            try:
                with RULES_PATH.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                logging.error("Failed to load ArchiMate rules from %s: %s", RULES_PATH, exc)

        logging.warning("Using fallback ArchiMate rule set; consider restoring %s", RULES_PATH)
        return {
            "layers": {
                "business": ["BusinessProcess", "BusinessService"],
                "application": ["ApplicationComponent", "ApplicationService", "DataObject"],
                "technology": ["Node", "TechnologyService", "Artifact"],
            },
            "relationship_types": [
                "Composition",
                "Aggregation",
                "Assignment",
                "Realization",
                "Serving",
                "Access",
                "Association",
            ],
            "relationship_rules": {
                "BusinessProcess:BusinessService": ["Realization"],
                "ApplicationComponent:ApplicationService": ["Realization"],
                "ApplicationComponent:DataObject": ["Access"],
                "Node:TechnologyService": ["Realization"],
            },
            "cardinality_rules": {},
        }

    def _build_indexes(self) -> None:
        """Create lookup indexes for layers and relationship types."""
        for layer, types in self.layers.items():
            for element_type in types:
                self.element_type_index[element_type.lower()] = element_type

        for rel_type in self.relationship_types:
            self.relationship_type_index[rel_type.lower()] = rel_type

        # Ensure Association exists even if omitted from rule file (common default)
        self.relationship_type_index.setdefault("association", "Association")

    def _normalize_relationship_map(
        self, raw_rules: Dict[str, List[str]]
    ) -> Dict[Tuple[str, str], List[str]]:
        normalized: Dict[Tuple[str, str], List[str]] = {}
        for key, values in raw_rules.items():
            if ":" not in key:
                logging.debug("Skipping malformed relationship rule key: %s", key)
                continue
            raw_source, raw_target = key.split(":", 1)
            source = self._canonical_element_type(raw_source)
            target = self._canonical_element_type(raw_target)
            allowed = [self._canonical_relationship_type(value) for value in values]
            normalized[(source, target)] = sorted({rel for rel in allowed if rel})
        return normalized

    def _normalize_cardinality_rules(
        self, rules: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        normalized: Dict[str, Dict[str, Any]] = {}
        for element_type, payload in rules.items():
            canonical = self._canonical_element_type(element_type)
            normalized[canonical] = {}
            for direction, requirement in payload.items():
                if not isinstance(requirement, dict):
                    continue
                types = [
                    self._canonical_relationship_type(rel) for rel in requirement.get("types", [])
                ]
                normalized[canonical][direction] = {
                    "min": requirement.get("min", 0),
                    "types": [rel for rel in types if rel],
                    "message": requirement.get("message", ""),
                    "severity": requirement.get("severity", "warning"),
                }
        return normalized

    def _normalize_layer(self, layer: Optional[str]) -> Optional[str]:
        normalized = ArchiMateLayer.normalize(layer)
        if not normalized:
            return None
        key = self.layer_aliases.get(normalized, normalized)
        return key if key in self.layers else key

    def _canonical_element_type(self, element_type: Optional[str]) -> Optional[str]:
        if not element_type:
            return None
        key = element_type.strip()
        canonical = self.element_type_index.get(key.lower())
        return canonical or key

    def _canonical_relationship_type(self, relationship_type: Optional[str]) -> Optional[str]:
        normalized = ArchiMateRelationshipType.normalize(relationship_type, pascal_case=True)
        if not normalized:
            return None
        key = normalized.strip()
        alias_target = self.relationship_aliases.get(key.lower())
        if alias_target:
            key = alias_target
        canonical = self.relationship_type_index.get(key.lower())
        return canonical or key

    @staticmethod
    def _chunked(values: List[int], chunk_size: int):
        for idx in range(0, len(values), chunk_size):
            yield values[idx : idx + chunk_size]

    def _apply_cardinality_requirement(
        self,
        element: ArchiMateElement,
        direction: str,
        requirement: Dict[str, Any],
        counts: Counter,
        results: Dict,
    ) -> None:
        min_required = requirement.get("min", 0)
        monitored_types = requirement.get("types", [])
        severity = requirement.get("severity", "warning").lower()
        message = requirement.get("message", "").strip()

        actual = sum(counts.get(rel_type, 0) for rel_type in monitored_types)
        if actual >= min_required:
            return

        detail = (
            f"Cardinality {direction} check failed for {element.name} ({element.type}): "
            f"requires >= {min_required} relationships of types {monitored_types} but found {actual}."
        )
        if message:
            detail = f"{detail} {message}"

        if severity == "error":
            results["is_valid"] = False
            results.setdefault("element_errors", []).append(
                {
                    "element_id": element.id,
                    "element_name": element.name,
                    "element_type": element.type,
                    "layer": element.layer,
                    "error": detail,
                    "severity": "error",
                }
            )
        else:
            tag = severity.upper() if severity in {"warning", "info"} else "WARNING"
            results["warnings"].append(f"[{tag}] {detail}")

    def _build_bidirectional_rules(self):
        """Build bidirectional relationship rules (most ArchiMate relationships work both ways)."""
        # Create reverse mappings for bidirectional relationships
        bidirectional_types = {"Association", "Flow", "Serving", "Triggering"}
        additional_rules: Dict[Tuple[str, str], List[str]] = {}

        for (source, target), rel_types in list(self.relationship_rules.items()):
            for rel_type in rel_types:
                if rel_type in bidirectional_types:
                    reverse_key = (target, source)
                    if reverse_key not in self.relationship_rules:
                        additional_rules.setdefault(reverse_key, [])
                        if rel_type not in additional_rules[reverse_key]:
                            additional_rules[reverse_key].append(rel_type)

        for key, values in additional_rules.items():
            if key in self.relationship_rules:
                merged = set(self.relationship_rules[key]) | set(values)
                self.relationship_rules[key] = sorted(merged)
            else:
                self.relationship_rules[key] = values

    def validate_element_type(self, element: ArchiMateElement) -> Tuple[bool, Optional[str]]:
        """
        Validate that element type belongs to its declared layer.

        Args:
            element: ArchiMateElement to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not element.layer or not element.type:
            return False, "Element must have both layer and type defined"

        normalized_layer = self._normalize_layer(element.layer)
        if not normalized_layer or normalized_layer not in self.layers:
            return (
                False,
                f"Invalid layer '{element.layer}'. Valid layers: {list(self.layers.keys())}",
            )

        canonical_type = self._canonical_element_type(element.type)
        valid_types = self.layers[normalized_layer]
        if canonical_type not in valid_types:
            return False, (
                f"Element type '{element.type}' is not valid for layer '{element.layer}'. "
                f"Valid types: {valid_types}"
            )

        return True, None

    def validate_relationship(
        self, relationship: ArchiMateRelationship
    ) -> Tuple[bool, Optional[str], List[str]]:
        """
        Validate ArchiMate relationship against metamodel rules.

        Args:
            relationship: ArchiMateRelationship to validate

        Returns:
            Tuple of (is_valid, error_message, allowed_types)
            - is_valid: Whether the relationship is valid
            - error_message: Error description if invalid
            - allowed_types: List of allowed relationship types for this source-target pair
        """
        source = relationship.source
        target = relationship.target

        if not source or not target:
            return False, "Relationship must have both source and target elements", []

        # Validate element types first
        source_valid, source_error = self.validate_element_type(source)
        if not source_valid:
            return False, f"Source element invalid: {source_error}", []

        target_valid, target_error = self.validate_element_type(target)
        if not target_valid:
            return False, f"Target element invalid: {target_error}", []

        canonical_source_type = self._canonical_element_type(source.type)
        canonical_target_type = self._canonical_element_type(target.type)

        # Check relationship type
        canonical_relationship_type = self._canonical_relationship_type(relationship.type)
        if canonical_relationship_type not in self.relationship_types:
            return (
                False,
                (
                    f"Invalid relationship type '{relationship.type}'. Valid types: {self.relationship_types}"
                ),
                [],
            )

        # Check if this source-target combination is allowed
        key = (canonical_source_type, canonical_target_type)
        allowed_types = self.relationship_rules.get(key, [])

        # If no specific rule exists, check for generic Association (allowed between most elements)
        if not allowed_types and canonical_relationship_type == "Association":
            allowed_types = ["Association"]

        if not allowed_types:
            return (
                False,
                f"No relationships allowed between {source.type} and {target.type} in ArchiMate 3.2",
                [],
            )

        if canonical_relationship_type not in allowed_types:
            return (
                False,
                (
                    f"Relationship type '{relationship.type}' not allowed between {source.type} and {target.type}. "
                    f"Allowed: {allowed_types}"
                ),
                allowed_types,
            )

        return True, None, allowed_types

    def get_allowed_relationship_types(self, source_type: str, target_type: str) -> List[str]:
        """
        Get list of allowed relationship types between two element types.

        Args:
            source_type: Source element type
            target_type: Target element type

        Returns:
            List of allowed relationship type strings
        """
        canonical_source = self._canonical_element_type(source_type)
        canonical_target = self._canonical_element_type(target_type)
        key = (canonical_source, canonical_target)
        allowed = list(self.relationship_rules.get(key, []))

        # Association is generally allowed between any elements
        if "Association" not in allowed:
            allowed.append("Association")

        return sorted(set(allowed))

    def get_valid_types_for_layer(self, layer: str) -> List[str]:
        """
        Get all valid element types for a given layer.

        Args:
            layer: Layer name (case-insensitive)

        Returns:
            List of valid element type strings
        """
        normalized_layer = self._normalize_layer(layer)
        if not normalized_layer:
            return []
        return self.layers.get(normalized_layer, [])

    def validate_model(self, model: ArchitectureModel) -> Dict[str, any]:
        """
        Validate entire ArchiMate model for compliance.

        Args:
            model: ArchitectureModel to validate

        Returns:
            Dictionary with validation results:
            {
                'is_valid': bool,
                'element_errors': List[Dict],
                'relationship_errors': List[Dict],
                'warnings': List[str],
                'summary': Dict
            }
        """
        results = {
            "is_valid": True,
            "element_errors": [],
            "relationship_errors": [],
            "warnings": [],
            "summary": {
                "total_elements": 0,
                "valid_elements": 0,
                "total_relationships": 0,
                "valid_relationships": 0,
            },
        }

        # Validate elements
        elements = list(model.elements)
        results["summary"]["total_elements"] = len(elements)

        element_by_id = {element.id: element for element in elements}

        for element in elements:
            is_valid, error = self.validate_element_type(element)
            if not is_valid:
                results["is_valid"] = False
                results["element_errors"].append(
                    {
                        "element_id": element.id,
                        "element_name": element.name,
                        "element_type": element.type,
                        "layer": element.layer,
                        "error": error,
                    }
                )
            else:
                results["summary"]["valid_elements"] += 1

        # Validate relationships
        relationships = list(model.relationships)
        results["summary"]["total_relationships"] = len(relationships)

        relationship_summaries: List[
            Tuple[Optional[int], Optional[int], Optional[int], Optional[str]]
        ] = []
        for rel in relationships:
            is_valid, error, allowed_types = self.validate_relationship(rel)
            if not is_valid:
                results["is_valid"] = False
                results["relationship_errors"].append(
                    {
                        "relationship_id": rel.id,
                        "source": rel.source.name if rel.source else "Unknown",
                        "target": rel.target.name if rel.target else "Unknown",
                        "type": rel.type,
                        "error": error,
                        "allowed_types": allowed_types,
                    }
                )
            else:
                results["summary"]["valid_relationships"] += 1
            canonical_type = self._canonical_relationship_type(rel.type) or rel.type
            relationship_summaries.append((rel.id, rel.source_id, rel.target_id, canonical_type))

        # Add warnings for potential issues
        self._validate_relationship_integrity(model, relationship_summaries, element_by_id, results)
        self._validate_cardinality(model, relationships, element_by_id, results)
        self._validate_traceability(model, relationships, element_by_id, results)
        self._validate_relationship_tables(model, element_by_id, results)
        self._detect_duplicate_elements(elements, results)
        self._add_quality_warnings(model, results, relationships)

        return results

    def _validate_relationship_integrity(
        self,
        model: ArchitectureModel,
        relationship_summaries: List[Tuple[Optional[int], Optional[int], Optional[int], str]],
        element_by_id: Dict[int, ArchiMateElement],
        results: Dict,
    ) -> None:
        """Detect duplicate, dangling, or malformed relationships."""
        seen: Dict[Tuple[int, int, str], int] = {}

        for rel_id, source_id, target_id, rel_type in relationship_summaries:
            if not source_id or not target_id:
                results["is_valid"] = False
                results["relationship_errors"].append(
                    {
                        "relationship_id": rel_id,
                        "source": source_id,
                        "target": target_id,
                        "type": rel_type,
                        "error": "Relationship has null source/target reference",
                        "allowed_types": [],
                    }
                )
                continue

            if source_id == target_id and rel_type not in {"Composition", "Aggregation"}:
                results["is_valid"] = False
                results["relationship_errors"].append(
                    {
                        "relationship_id": rel_id,
                        "source": element_by_id.get(source_id).name
                        if source_id in element_by_id
                        else source_id,
                        "target": element_by_id.get(target_id).name
                        if target_id in element_by_id
                        else target_id,
                        "type": rel_type,
                        "error": "Self-referential relationship detected",
                        "allowed_types": [],
                    }
                )

            key = (source_id, target_id, rel_type)
            if key in seen:
                if seen[key] == 1:
                    source_label = (
                        element_by_id.get(source_id).name
                        if source_id in element_by_id
                        else source_id
                    )
                    target_label = (
                        element_by_id.get(target_id).name
                        if target_id in element_by_id
                        else target_id
                    )
                    results["warnings"].append(
                        f"Duplicate relationship detected between elements {source_label} → {target_label} ({rel_type})."
                    )
                seen[key] += 1
            else:
                seen[key] = 1

        # Verify all relationship endpoints belong to the architecture
        element_ids = set(element_by_id.keys())
        for rel_id, source_id, target_id, rel_type in relationship_summaries:
            if source_id and source_id not in element_ids:
                results["is_valid"] = False
                results["relationship_errors"].append(
                    {
                        "relationship_id": rel_id,
                        "source": source_id,
                        "target": target_id,
                        "type": rel_type,
                        "error": "Relationship source does not belong to architecture model",
                        "allowed_types": [],
                    }
                )
            if target_id and target_id not in element_ids:
                results["is_valid"] = False
                results["relationship_errors"].append(
                    {
                        "relationship_id": rel_id,
                        "source": source_id,
                        "target": target_id,
                        "type": rel_type,
                        "error": "Relationship target does not belong to architecture model",
                        "allowed_types": [],
                    }
                )

    def _validate_cardinality(
        self,
        model: ArchitectureModel,
        relationships: List[ArchiMateRelationship],
        element_by_id: Dict[int, ArchiMateElement],
        results: Dict,
    ) -> None:
        """Apply basic cardinality heuristics to detect dangling core elements."""
        outbound: Dict[int, Counter] = defaultdict(Counter)
        inbound: Dict[int, Counter] = defaultdict(Counter)

        for rel in relationships:
            canonical_type = self._canonical_relationship_type(rel.type)
            if rel.source_id and canonical_type:
                outbound[rel.source_id][canonical_type] += 1
            if rel.target_id and canonical_type:
                inbound[rel.target_id][canonical_type] += 1

        for element in element_by_id.values():
            rules = self.cardinality_rules.get(self._canonical_element_type(element.type))
            if not rules:
                continue

            outbound_rule = rules.get("outbound")
            if outbound_rule:
                self._apply_cardinality_requirement(
                    element,
                    direction="outbound",
                    requirement=outbound_rule,
                    counts=outbound.get(element.id, Counter()),
                    results=results,
                )

            inbound_rule = rules.get("inbound")
            if inbound_rule:
                self._apply_cardinality_requirement(
                    element,
                    direction="inbound",
                    requirement=inbound_rule,
                    counts=inbound.get(element.id, Counter()),
                    results=results,
                )

    def _validate_traceability(
        self,
        model: ArchitectureModel,
        relationships: List[ArchiMateRelationship],
        element_by_id: Dict[int, ArchiMateElement],
        results: Dict,
    ) -> None:
        """Ensure requirements, code artifacts, and tests align with ArchiMate structure."""
        if not hasattr(model, "requirements"):
            return

        outbound_map: Dict[int, List[ArchiMateRelationship]] = defaultdict(list)
        inbound_map: Dict[int, List[ArchiMateRelationship]] = defaultdict(list)
        for rel in relationships:
            if rel.source_id:
                outbound_map[rel.source_id].append(rel)
            if rel.target_id:
                inbound_map[rel.target_id].append(rel)

        for requirement in getattr(model, "requirements", []) or []:
            element_id = requirement.archimate_element_id or requirement.source_element_id
            if not element_id:
                results["warnings"].append(
                    f"Requirement '{requirement.display_title}' is not linked to an ArchiMate element."
                )
                continue

            if element_id not in element_by_id:
                results["warnings"].append(
                    f"Requirement '{requirement.display_title}' references element {element_id} outside this architecture."
                )
                continue

            outbound = outbound_map.get(element_id, [])
            inbound = inbound_map.get(element_id, [])
            if not outbound and not inbound:
                results["warnings"].append(
                    f"Requirement '{requirement.display_title}' is orphaned – no realizing relationships detected."
                )

            artifacts = list(requirement.implemented_by or [])
            if not artifacts:
                results["warnings"].append(
                    f"Requirement '{requirement.display_title}' has no linked code artifacts."
                )
            tests = list(requirement.test_cases or [])
            if not tests:
                results["warnings"].append(
                    f"Requirement '{requirement.display_title}' has no associated test cases."
                )

        for artifact in getattr(model, "code_artifacts", []) or []:
            if artifact.source_element_id and artifact.source_element_id not in element_by_id:
                results["warnings"].append(
                    f"Code artifact '{artifact.name}' references element {artifact.source_element_id} outside this architecture."
                )

    def _validate_relationship_tables(
        self, model: ArchitectureModel, element_by_id: Dict[int, ArchiMateElement], results: Dict
    ) -> None:
        """Check junction tables that map to ArchiMate elements for consistency."""
        architecture_id = model.id
        element_ids = set(element_by_id.keys())

        if not element_ids:
            return

        element_id_list = list(element_ids)

        # Avoid overwhelming the database for extremely large models
        if len(element_id_list) > 5000:
            results["warnings"].append(
                "Skipped deep relationship table validation due to large model (>5000 elements)."
            )
            return

        batch_size = 500

        def fetch_data_object_records(batch: List[int]):
            return (
                DataObjectStorage.query.options(joinedload(DataObjectStorage.application_component))
                .filter(DataObjectStorage.application_component_id.in_(batch))
                .all()
            )

        data_object_records: List[DataObjectStorage] = []
        for batch in self._chunked(element_id_list, batch_size):
            data_object_records.extend(fetch_data_object_records(batch))
        for record in data_object_records:
            component = record.application_component
            if component and component.type != "ApplicationComponent":
                results["warnings"].append(
                    f"DataObjectStorage entry {record.id} references element '{component.name}' not typed as ApplicationComponent."
                )

        def fetch_interface_consumers(batch: List[int]):
            return (
                InterfaceConsumer.query.options(joinedload(InterfaceConsumer.consumer_application))
                .filter(InterfaceConsumer.consumer_application_id.in_(batch))
                .all()
            )

        interface_consumers: List[InterfaceConsumer] = []
        for batch in self._chunked(element_id_list, batch_size):
            interface_consumers.extend(fetch_interface_consumers(batch))
        for consumer in interface_consumers:
            element = consumer.consumer_application
            if element and element.layer != "application":
                results["warnings"].append(
                    f"InterfaceConsumer {consumer.id} links to '{element.name}' outside application layer."
                )

        service_dependencies = (
            ServiceDependency.query.join(ServiceDependency.dependent_service)
            .join(BusinessService.archimate_element)
            .options(
                joinedload(ServiceDependency.dependent_service).joinedload(
                    BusinessService.archimate_element
                ),
                joinedload(ServiceDependency.provider_service).joinedload(
                    BusinessService.archimate_element
                ),
            )
            .filter(ArchiMateElement.architecture_id == architecture_id)
            .all()
        )
        for dependency in service_dependencies:
            dep_element = getattr(dependency.dependent_service, "archimate_element", None)
            prov_element = getattr(dependency.provider_service, "archimate_element", None)
            if dep_element and dep_element.architecture_id == architecture_id:
                if prov_element and prov_element.architecture_id != architecture_id:
                    results["warnings"].append(
                        f"ServiceDependency {dependency.id} crosses architecture boundaries ({dep_element.name} → {getattr(prov_element, 'name', 'Unknown')})."
                    )

        service_realizations = (
            ServiceRealization.query.join(ServiceRealization.process)
            .join(BusinessProcess.archimate_element)
            .options(
                joinedload(ServiceRealization.process).joinedload(
                    BusinessProcess.archimate_element
                ),
                joinedload(ServiceRealization.service).joinedload(
                    BusinessService.archimate_element
                ),
            )
            .filter(ArchiMateElement.architecture_id == architecture_id)
            .all()
        )
        for realization in service_realizations:
            process_element = getattr(realization.process, "archimate_element", None)
            service_element = getattr(realization.service, "archimate_element", None)
            if process_element and process_element.architecture_id == architecture_id:
                if not service_element or service_element.architecture_id != architecture_id:
                    results["warnings"].append(
                        f"ServiceRealization {realization.id} points to service outside this architecture."
                    )

    def _detect_duplicate_elements(self, elements: List[ArchiMateElement], results: Dict) -> None:
        """Identify duplicate names/types that often signal modeling issues."""
        counter = Counter(
            (element.name.strip().lower(), element.type) for element in elements if element.name
        )
        for (name, element_type), count in counter.items():
            if count > 1:
                results["warnings"].append(
                    f"Duplicate element detected: '{name}' ({element_type}) appears {count} times."
                )

    def _add_quality_warnings(
        self, model: ArchitectureModel, results: Dict, relationships: List[ArchiMateRelationship]
    ) -> None:
        """Add quality warnings (not errors, but potential issues)."""
        elements = list(model.elements)

        # Check for orphaned elements (no relationships)
        adjacency = defaultdict(int)
        reverse_adjacency = defaultdict(int)
        for rel in relationships:
            if rel.source_id:
                adjacency[rel.source_id] += 1
            if rel.target_id:
                reverse_adjacency[rel.target_id] += 1

        for element in elements:
            if adjacency.get(element.id, 0) == 0 and reverse_adjacency.get(element.id, 0) == 0:
                results["warnings"].append(
                    f"Orphaned element: '{element.name}' ({element.type}) has no relationships"
                )

        # Check for missing descriptions
        missing_desc = [e for e in elements if not e.description or len(e.description.strip()) < 10]
        if (
            elements and len(missing_desc) > len(elements) * 0.3
        ):  # More than 30% missing descriptions
            results["warnings"].append(
                f"{len(missing_desc)} elements ({len(missing_desc)/len(elements)*100:.1f}%) are missing detailed descriptions"
            )

        # Check for layer violations (e.g., business process directly using node)
        for rel in relationships:
            source = getattr(rel, "source", None)
            target = getattr(rel, "target", None)
            if source and target:
                source_layer = source.layer
                target_layer = target.layer

                # Business should not directly depend on Technology (should go through Application)
                if (
                    source_layer == "business"
                    and target_layer == "technology"
                    and rel.type != "Association"
                ):
                    results["warnings"].append(
                        f"Layer violation: {source.name} (business) directly depends on "
                        f"{target.name} (technology). Consider using application layer."
                    )

    def suggest_relationships(
        self, source: ArchiMateElement, existing_targets: List[ArchiMateElement]
    ) -> List[Dict]:
        """
        Suggest valid relationships from source to existing target elements.

        Args:
            source: Source ArchiMateElement
            existing_targets: List of potential target elements

        Returns:
            List of dictionaries with suggestions:
            [{
                'target': ArchiMateElement,
                'allowed_types': List[str],
                'recommendation': str
            }]
        """
        suggestions = []

        for target in existing_targets:
            if source.id == target.id:
                continue  # Skip self-relationships

            allowed_types = self.get_allowed_relationship_types(source.type, target.type)

            if allowed_types:
                # Generate recommendation based on common patterns
                recommendation = self._generate_relationship_recommendation(
                    source.type, target.type, allowed_types
                )

                suggestions.append(
                    {
                        "target": target,
                        "allowed_types": allowed_types,
                        "recommendation": recommendation,
                    }
                )

        return suggestions

    def _generate_relationship_recommendation(
        self, source_type: str, target_type: str, allowed_types: List[str]
    ) -> str:
        """Generate human-readable recommendation for relationship."""
        if "Realization" in allowed_types:
            return f"Consider 'Realization' if {source_type} implements/realizes {target_type}"
        elif "Serving" in allowed_types:
            return f"Use 'Serving' if {source_type} provides services to {target_type}"
        elif "Assignment" in allowed_types:
            return f"Use 'Assignment' if {source_type} is assigned to perform {target_type}"
        elif "Composition" in allowed_types:
            return f"Use 'Composition' if {target_type} is an integral part of {source_type}"
        elif "Flow" in allowed_types:
            return (
                f"Use 'Flow' to show information/control flow from {source_type} to {target_type}"
            )
        else:
            return f"Valid relationships: {', '.join(allowed_types)}"
