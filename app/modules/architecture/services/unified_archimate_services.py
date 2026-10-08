"""
-> app.modules.architecture.services.archimate_service

Unified ArchiMate Services Module

Consolidates all ArchiMate-related services into a single, maintainable module.
This includes validation, metrics calculation, viewpoint building, and element cloning.

Services consolidated:
- ArchiMateValidator: Model validation against ArchiMate 3.2 metamodel
- ArchiMateMetricsService: Architecture health and quality metrics
- ArchiMateViewpointBuilder: Viewpoint generation for applications
- ArchiMateElementCloner: Element cloning for vendor deployment

Original files:
- app/services/archimate/archimate_validator.py (745 lines)
- app/services/archimate/archimate_metrics_service.py (550 lines)
- app/services/archimate_viewpoint_builder.py (732 lines)
- app/services/archimate_element_cloner.py (237 lines)

This consolidation maintains 100% backward compatibility while reducing code fragmentation.
"""

"""
Unified ArchiMate Services Module - Consolidated Imports
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Module-level logger — `logger` was referenced by 8 call sites in this file without
# ever being bound, raising NameError when those paths ran.
logger = logging.getLogger(__name__)

from sqlalchemy import or_, text
from sqlalchemy.orm import joinedload

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
from app.models.constants import ArchiMateLayer, ArchiMateRelationshipType
from app.models.application_layer import (  # dead-code-ok
    ApplicationComponent,
    ApplicationInterface,
)
from app.models.business_capabilities import BusinessCapability
from app.models.business_layer import BusinessService
from app.models.process_data import BusinessProcess
from app.models.relationship_tables import (
    DataObjectStorage,
    InterfaceConsumer,
    ServiceDependency,
    ServiceRealization,
)
from app.models.vendor.vendor_organization import VendorProduct, application_vendor_products

# Path to ArchiMate rules file (adjusted for unified location)
RULES_PATH = Path(__file__).parent / "archimate" / "rules" / "archimate_rules.json"


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


class ArchiMateMetricsService:
    """
    Service for calculating architecture health and quality metrics.

    Metrics include:
    - Structure metrics (size, complexity, connectivity)
    - Quality metrics (completeness, consistency, documentation)
    - Technical debt indicators
    - Layer distribution
    - Relationship quality
    """

    def __init__(self):
        """Initialize metrics service."""
        pass

    def calculate_all_metrics(self, model: ArchitectureModel) -> Dict:
        """
        Calculate comprehensive metrics for an architecture model.

        Args:
            model: ArchitectureModel to analyze

        Returns:
            Dictionary with all calculated metrics
        """
        elements = list(model.archimate_elements.all())
        relationships = list(model.archimate_relationships.all())

        if not elements:
            return {"error": "Model has no elements", "overall_score": 0}

        return {
            "model_id": model.id,
            "model_name": model.name,
            "structure_metrics": self.calculate_structure_metrics(elements, relationships),
            "quality_metrics": self.calculate_quality_metrics(elements, relationships),
            "layer_metrics": self.calculate_layer_metrics(elements),
            "relationship_metrics": self.calculate_relationship_metrics(relationships, elements),
            "complexity_metrics": self.calculate_complexity_metrics(elements, relationships),
            "completeness_score": self.calculate_completeness_score(elements, relationships),
            "technical_debt_score": self.calculate_technical_debt(elements, relationships),
            "overall_health_score": 0,  # Will be calculated from other scores
        }

    def calculate_structure_metrics(
        self, elements: List[ArchiMateElement], relationships: List[ArchiMateRelationship]
    ) -> Dict:
        """
        Calculate structural metrics about the model.

        Returns:
            Dictionary with structural metrics
        """
        total_elements = len(elements)
        total_relationships = len(relationships)

        # Count orphaned elements (no relationships)
        element_ids = {e.id for e in elements}
        connected_elements = set()

        for rel in relationships:
            if rel.source_element_id in element_ids:
                connected_elements.add(rel.source_element_id)
            if rel.target_element_id in element_ids:
                connected_elements.add(rel.target_element_id)

        orphaned_count = total_elements - len(connected_elements)

        # Calculate average connections per element
        avg_connections = (total_relationships * 2) / total_elements if total_elements > 0 else 0

        # Find most connected elements
        element_connections = defaultdict(int)
        for rel in relationships:
            if rel.source_element_id:
                element_connections[rel.source_element_id] += 1
            if rel.target_element_id:
                element_connections[rel.target_element_id] += 1

        max_connections = max(element_connections.values()) if element_connections else 0

        return {
            "total_elements": total_elements,
            "total_relationships": total_relationships,
            "orphaned_elements": orphaned_count,
            "orphaned_percentage": (orphaned_count / total_elements * 100)
            if total_elements > 0
            else 0,
            "connected_elements": len(connected_elements),
            "connectivity_ratio": len(connected_elements) / total_elements
            if total_elements > 0
            else 0,
            "average_connections_per_element": round(avg_connections, 2),
            "max_connections": max_connections,
            "relationship_to_element_ratio": round(total_relationships / total_elements, 2)
            if total_elements > 0
            else 0,
        }

    def calculate_quality_metrics(
        self, elements: List[ArchiMateElement], relationships: List[ArchiMateRelationship]
    ) -> Dict:
        """
        Calculate quality metrics for documentation and completeness.

        Returns:
            Dictionary with quality metrics
        """
        total_elements = len(elements)

        # Documentation quality
        elements_with_description = sum(
            1 for e in elements if e.description and len(e.description.strip()) >= 20
        )
        description_quality_score = (
            (elements_with_description / total_elements * 100) if total_elements > 0 else 0
        )

        # Name quality (not generic names like "System", "Process")
        generic_names = {
            "system",
            "process",
            "service",
            "component",
            "application",
            "database",
            "server",
        }
        elements_with_specific_names = sum(
            1
            for e in elements
            if e.name and e.name.lower().strip() not in generic_names and len(e.name.split()) >= 2
        )
        name_quality_score = (
            (elements_with_specific_names / total_elements * 100) if total_elements > 0 else 0
        )

        # Relationship documentation
        rels_with_description = sum(
            1 for r in relationships if r.description and len(r.description.strip()) >= 10
        )
        relationship_doc_score = (
            (rels_with_description / len(relationships) * 100) if relationships else 0
        )

        # Properties usage
        elements_with_properties = sum(
            1 for e in elements if e.properties and len(e.properties) > 0
        )
        properties_usage_score = (
            (elements_with_properties / total_elements * 100) if total_elements > 0 else 0
        )

        # Overall quality score
        overall_quality = (
            description_quality_score * 0.4
            + name_quality_score * 0.3
            + relationship_doc_score * 0.2
            + properties_usage_score * 0.1
        )

        return {
            "description_quality_score": round(description_quality_score, 1),
            "name_quality_score": round(name_quality_score, 1),
            "relationship_documentation_score": round(relationship_doc_score, 1),
            "properties_usage_score": round(properties_usage_score, 1),
            "overall_quality_score": round(overall_quality, 1),
            "elements_with_good_descriptions": elements_with_description,
            "elements_with_specific_names": elements_with_specific_names,
            "relationships_documented": rels_with_description,
        }

    def calculate_layer_metrics(self, elements: List[ArchiMateElement]) -> Dict:
        """
        Calculate metrics about layer distribution and coverage.

        Returns:
            Dictionary with layer metrics
        """
        total_elements = len(elements)

        # Count elements by layer
        layer_counts = defaultdict(int)
        for elem in elements:
            layer_counts[elem.layer] += 1

        layer_distribution = {
            layer: {"count": count, "percentage": round(count / total_elements * 100, 1)}
            for layer, count in layer_counts.items()
        }

        # Count element types
        type_counts = defaultdict(int)
        for elem in elements:
            type_counts[elem.type] += 1

        # Layer coverage (how many layers are used)
        standard_layers = {
            "motivation",
            "strategy",
            "business",
            "application",
            "technology",
            "physical",
            "implementation",
        }
        layers_used = set(layer_counts.keys())
        layer_coverage_score = len(layers_used) / len(standard_layers) * 100

        # Balance score (how evenly distributed across layers)
        if layer_counts:
            avg_per_layer = total_elements / len(layer_counts)
            variance = sum((count - avg_per_layer) ** 2 for count in layer_counts.values()) / len(
                layer_counts
            )
            balance_score = (
                max(0, 100 - (variance / avg_per_layer * 10)) if avg_per_layer > 0 else 0
            )
        else:
            balance_score = 0

        return {
            "layer_distribution": layer_distribution,
            "layers_used": list(layers_used),
            "layer_count": len(layers_used),
            "layer_coverage_score": round(layer_coverage_score, 1),
            "layer_balance_score": round(balance_score, 1),
            "element_type_diversity": len(type_counts),
            "most_used_types": sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:5],
        }

    def calculate_relationship_metrics(
        self, relationships: List[ArchiMateRelationship], elements: List[ArchiMateElement]
    ) -> Dict:
        """
        Calculate metrics about relationships and their quality.

        Returns:
            Dictionary with relationship metrics
        """
        if not relationships:
            return {"total_relationships": 0, "relationship_quality_score": 0}

        total_relationships = len(relationships)

        # Count by type
        type_counts = defaultdict(int)
        for rel in relationships:
            type_counts[rel.type] += 1

        # Cross-layer relationships
        cross_layer_rels = sum(
            1
            for rel in relationships
            if rel.source_element
            and rel.target_element
            and rel.source_element.layer != rel.target_element.layer
        )
        cross_layer_ratio = (
            (cross_layer_rels / total_relationships * 100) if total_relationships > 0 else 0
        )

        # Bidirectional relationships (both A->B and B->A exist)
        rel_pairs = set()
        bidirectional_count = 0
        for rel in relationships:
            if rel.source_element_id and rel.target_element_id:
                pair = (
                    min(rel.source_element_id, rel.target_element_id),
                    max(rel.source_element_id, rel.target_element_id),
                )
                if pair in rel_pairs:
                    bidirectional_count += 1
                rel_pairs.add(pair)

        # Relationship type diversity
        type_diversity_score = (
            len(type_counts) / 11 * 100
        )  # 11 standard relationship types in ArchiMate

        return {
            "total_relationships": total_relationships,
            "relationship_type_distribution": dict(type_counts),
            "relationship_type_diversity": len(type_counts),
            "type_diversity_score": round(type_diversity_score, 1),
            "cross_layer_relationships": cross_layer_rels,
            "cross_layer_percentage": round(cross_layer_ratio, 1),
            "bidirectional_relationships": bidirectional_count,
            "most_used_relationship_types": sorted(
                type_counts.items(), key=lambda x: x[1], reverse=True
            )[:3],
        }

    def calculate_complexity_metrics(
        self, elements: List[ArchiMateElement], relationships: List[ArchiMateRelationship]
    ) -> Dict:
        """
        Calculate complexity metrics for the architecture.

        Returns:
            Dictionary with complexity metrics
        """
        total_elements = len(elements)
        total_relationships = len(relationships)

        # Cyclomatic complexity (simplified)
        # V = E - N + 2P (where E=edges, N=nodes, P=connected components)
        # For our purposes, we'll use a simplified version
        cyclomatic = total_relationships - total_elements + 2 if total_elements > 0 else 0

        # Calculate depth of element hierarchies
        parent_counts = defaultdict(int)
        max_depth = 0

        def calculate_depth(element_id, elements_dict, visited=None):
            if visited is None:
                visited = set()
            if element_id in visited:
                return 0
            visited.add(element_id)

            elem = elements_dict.get(element_id)
            if not elem or not elem.parent_id:
                return 1

            return 1 + calculate_depth(elem.parent_id, elements_dict, visited)

        elements_dict = {e.id: e for e in elements}
        for elem in elements:
            if elem.parent_id:
                parent_counts[elem.parent_id] += 1
            depth = calculate_depth(elem.id, elements_dict)
            max_depth = max(max_depth, depth)

        # Complexity score (0 - 100, higher = more complex)
        # Based on: size, connectivity, depth, cyclomatic complexity
        size_complexity = min(total_elements / 100 * 100, 100)  # 100+ elements = max
        connectivity_complexity = (
            min(total_relationships / total_elements * 10, 100) if total_elements > 0 else 0
        )
        depth_complexity = min(max_depth * 20, 100)

        overall_complexity = (
            size_complexity * 0.3 + connectivity_complexity * 0.4 + depth_complexity * 0.3
        )

        return {
            "cyclomatic_complexity": cyclomatic,
            "max_hierarchy_depth": max_depth,
            "elements_with_children": len(parent_counts),
            "max_children": max(parent_counts.values()) if parent_counts else 0,
            "size_complexity_score": round(size_complexity, 1),
            "connectivity_complexity_score": round(connectivity_complexity, 1),
            "depth_complexity_score": round(depth_complexity, 1),
            "overall_complexity_score": round(overall_complexity, 1),
            "complexity_rating": self._get_complexity_rating(overall_complexity),
        }

    def _get_complexity_rating(self, score: float) -> str:
        """Get human-readable complexity rating."""
        if score < 30:
            return "Low"
        elif score < 60:
            return "Medium"
        elif score < 80:
            return "High"
        else:
            return "Very High"

    def calculate_completeness_score(
        self, elements: List[ArchiMateElement], relationships: List[ArchiMateRelationship]
    ) -> Dict:
        """
        Calculate how complete the architecture model is.

        Returns:
            Dictionary with completeness metrics
        """
        scores = []

        # Layer completeness (should have multiple layers)
        layers = set(e.layer for e in elements)
        layer_score = min(len(layers) / 4 * 100, 100)  # 4+ layers = complete
        scores.append(layer_score)

        # Connectivity completeness (elements should have relationships)
        if elements:
            connected_ratio = len(
                set(
                    [r.source_element_id for r in relationships if r.source_element_id]
                    + [r.target_element_id for r in relationships if r.target_element_id]
                )
            ) / len(elements)
            connectivity_score = connected_ratio * 100
            scores.append(connectivity_score)
        else:
            connectivity_score = 0

        # Documentation completeness
        documented_elements = sum(1 for e in elements if e.description and len(e.description) >= 20)
        doc_score = (documented_elements / len(elements) * 100) if elements else 0
        scores.append(doc_score)

        # Motivation layer presence (goals, requirements, stakeholders)
        motivation_elements = [e for e in elements if e.layer == "motivation"]
        has_stakeholders = any(e.type == "Stakeholder" for e in motivation_elements)
        has_goals = any(e.type == "Goal" for e in motivation_elements)
        has_requirements = any(e.type == "Requirement" for e in motivation_elements)
        motivation_score = sum([has_stakeholders, has_goals, has_requirements]) / 3 * 100
        scores.append(motivation_score)

        # Cross-layer linkage (motivation should link to realization)
        cross_layer_rels = [
            r
            for r in relationships
            if r.source_element
            and r.target_element
            and r.source_element.layer != r.target_element.layer
        ]
        cross_layer_score = min(len(cross_layer_rels) / 5 * 100, 100)  # 5+ cross-layer = complete
        scores.append(cross_layer_score)

        overall_completeness = sum(scores) / len(scores) if scores else 0

        return {
            "layer_completeness": round(layer_score, 1),
            "connectivity_completeness": round(connectivity_score, 1),
            "documentation_completeness": round(doc_score, 1),
            "motivation_completeness": round(motivation_score, 1),
            "cross_layer_linkage_completeness": round(cross_layer_score, 1),
            "overall_completeness_score": round(overall_completeness, 1),
            "completeness_rating": self._get_completeness_rating(overall_completeness),
        }

    def _get_completeness_rating(self, score: float) -> str:
        """Get human-readable completeness rating."""
        if score < 40:
            return "Incomplete"
        elif score < 70:
            return "Partial"
        elif score < 90:
            return "Good"
        else:
            return "Complete"

    def calculate_technical_debt(
        self, elements: List[ArchiMateElement], relationships: List[ArchiMateRelationship]
    ) -> Dict:
        """
        Calculate technical debt indicators.

        Returns:
            Dictionary with technical debt metrics
        """
        debt_indicators = []

        # Missing descriptions
        missing_descriptions = sum(
            1 for e in elements if not e.description or len(e.description.strip()) < 20
        )
        if missing_descriptions > len(elements) * 0.3:
            debt_indicators.append(
                {
                    "type": "documentation_debt",
                    "severity": "medium",
                    "description": f"{missing_descriptions} elements lack adequate descriptions",
                    "impact_score": 30,
                }
            )

        # Orphaned elements
        connected_ids = set()
        for rel in relationships:
            if rel.source_element_id:
                connected_ids.add(rel.source_element_id)
            if rel.target_element_id:
                connected_ids.add(rel.target_element_id)

        orphaned = len(elements) - len(connected_ids)
        if orphaned > 0:
            debt_indicators.append(
                {
                    "type": "architectural_debt",
                    "severity": "high" if orphaned > 5 else "medium",
                    "description": f"{orphaned} orphaned elements with no relationships",
                    "impact_score": min(orphaned * 10, 50),
                }
            )

        # Generic naming
        generic_names = {"system", "process", "service", "component", "application"}
        generic_named = sum(
            1 for e in elements if e.name and e.name.lower().strip() in generic_names
        )
        if generic_named > 0:
            debt_indicators.append(
                {
                    "type": "naming_debt",
                    "severity": "low",
                    "description": f"{generic_named} elements have generic names",
                    "impact_score": generic_named * 5,
                }
            )

        # Missing motivation layer
        has_motivation = any(e.layer == "motivation" for e in elements)
        if not has_motivation:
            debt_indicators.append(
                {
                    "type": "strategic_debt",
                    "severity": "high",
                    "description": "No motivation layer elements (goals, requirements, stakeholders)",
                    "impact_score": 40,
                }
            )

        # Calculate total debt score
        total_impact = sum(ind["impact_score"] for ind in debt_indicators)
        debt_score = min(total_impact, 100)

        return {
            "debt_indicators": debt_indicators,
            "total_debt_score": round(debt_score, 1),
            "debt_rating": self._get_debt_rating(debt_score),
            "indicator_count": len(debt_indicators),
            "high_severity_count": sum(1 for ind in debt_indicators if ind["severity"] == "high"),
            "medium_severity_count": sum(
                1 for ind in debt_indicators if ind["severity"] == "medium"
            ),
            "low_severity_count": sum(1 for ind in debt_indicators if ind["severity"] == "low"),
        }

    def _get_debt_rating(self, score: float) -> str:
        """Get human-readable debt rating."""
        if score < 20:
            return "Low"
        elif score < 50:
            return "Medium"
        elif score < 80:
            return "High"
        else:
            return "Critical"

    def calculate_overall_health_score(self, metrics: Dict) -> float:
        """
        Calculate overall architecture health score from individual metrics.

        Args:
            metrics: Dictionary with all calculated metrics

        Returns:
            Overall health score (0 - 100)
        """
        # Weight different aspects
        quality_weight = 0.25
        completeness_weight = 0.25
        complexity_weight = 0.20  # Lower complexity = better
        debt_weight = 0.30  # Lower debt = better

        quality_score = metrics.get("quality_metrics", {}).get("overall_quality_score", 50)
        completeness_score = metrics.get("completeness_score", {}).get(
            "overall_completeness_score", 50
        )
        complexity_score = 100 - metrics.get("complexity_metrics", {}).get(
            "overall_complexity_score", 50
        )  # Invert
        debt_score = 100 - metrics.get("technical_debt_score", {}).get(
            "total_debt_score", 50
        )  # Invert

        overall = (
            quality_score * quality_weight
            + completeness_score * completeness_weight
            + complexity_score * complexity_weight
            + debt_score * debt_weight
        )

        return round(overall, 1)


class ArchiMateViewpointBuilder:
    """Service for building ArchiMate 3.2 viewpoints from application data."""

    # ArchiMate 3.2 metamodel: allowed relationship types per layer
    ALLOWED_RELATIONSHIPS = {
        "application": [
            "serving",
            "access",
            "assignment",
            "realization",
            "composition",
            "aggregation",
            "flow",
            "triggering",
        ],
        "business": [
            "serving",
            "access",
            "assignment",
            "realization",
            "composition",
            "aggregation",
            "flow",
            "triggering",
        ],
        "technology": [
            "serving",
            "access",
            "assignment",
            "realization",
            "composition",
            "aggregation",
        ],
        "motivation": ["realization", "influence", "association"],
        "strategy": ["realization", "influence", "association", "aggregation"],
    }

    def __init__(self, application_id: int):
        """
        Initialize viewpoint builder for a specific application.

        Args:
            application_id: ID of the ApplicationComponent
        """
        self.application_id = application_id
        self.app = ApplicationComponent.query.get(application_id)
        if not self.app:
            raise ValueError(f"Application {application_id} not found")

        self.archimate_element = None
        if self.app.archimate_element_id:
            self.archimate_element = db.session.get(ArchiMateElement, self.app.archimate_element_id)

    def build_cooperation_viewpoint(self, depth: int = 2) -> Dict:
        """
        Build Application Cooperation Viewpoint (ArchiMate 3.2).

        Shows:
        - Application components and their relationships
        - Application interfaces (APIs, services)
        - Application events (pub/sub, messaging)
        - Data flows between applications

        Args:
            depth: Relationship traversal depth (1 - 3)

        Returns:
            Dict with nodes, edges, and metadata for diagram generation
        """
        if not self.archimate_element:
            return self._empty_viewpoint(
                "Application Cooperation",
                "Link this application to an ArchiMate element to view cooperation diagram",
            )

        nodes = []
        edges = []
        visited_elements = set()

        # Add central application node
        central_node = self._create_node(
            self.archimate_element, is_central=True, metadata={"app_id": self.application_id}
        )
        nodes.append(central_node)
        visited_elements.add(self.archimate_element.id)

        # Traverse relationships to find cooperating applications
        self._traverse_cooperation_relationships(
            self.archimate_element, nodes, edges, visited_elements, current_depth=0, max_depth=depth
        )

        # Add interfaces as connection points
        interfaces = ApplicationInterface.query.filter(
            or_(
                ApplicationInterface.provider_application_id == self.archimate_element.id,
                ApplicationInterface.archimate_element_id == self.archimate_element.id,
            )
        ).all()

        for interface in interfaces:
            if interface.archimate_element_id:
                interface_element = db.session.get(ArchiMateElement, interface.archimate_element_id)
                if interface_element and interface_element.id not in visited_elements:
                    nodes.append(self._create_node(interface_element, node_type="interface"))
                    visited_elements.add(interface_element.id)

                    # Link interface to application
                    edges.append(
                        {
                            "source": self.archimate_element.id,
                            "target": interface_element.id,
                            "type": "composition",
                            "label": "exposes",
                        }
                    )

        return {
            "viewpoint_type": "application_cooperation",
            "title": f"Application Cooperation: {self.app.name}",
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "central_app_id": self.application_id,
                "node_count": len(nodes),
                "relationship_count": len(edges),
                "depth": depth,
            },
        }

    def build_usage_viewpoint(self, depth: int = 2) -> Dict:
        """
        Build Application Usage Viewpoint (ArchiMate 3.2).

        Shows:
        - Business processes using this application
        - Business services realized by application services
        - Business capabilities supported
        - Application services provided

        Args:
            depth: Relationship traversal depth (1 - 3)

        Returns:
            Dict with nodes, edges, and metadata for diagram generation
        """
        if not self.archimate_element:
            return self._empty_viewpoint(
                "Application Usage",
                "Link this application to an ArchiMate element to view usage diagram",
            )

        nodes = []
        edges = []
        visited_elements = set()

        # Add central application node
        nodes.append(self._create_node(self.archimate_element, is_central=True))
        visited_elements.add(self.archimate_element.id)

        # Find business capabilities supported by this application
        from app.models.application_portfolio import ApplicationCapabilityMapping

        capability_mappings = (
            # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
            db.session.query(ApplicationCapabilityMapping, BusinessCapability)
            .join(
                BusinessCapability,
                ApplicationCapabilityMapping.business_capability_id == BusinessCapability.id,
            )
            .filter(ApplicationCapabilityMapping.application_component_id == self.application_id)
            .all()
        )

        for mapping, capability in capability_mappings:
            if capability.archimate_element_id:
                cap_element = db.session.get(ArchiMateElement, capability.archimate_element_id)
                if cap_element and cap_element.id not in visited_elements:
                    nodes.append(self._create_node(cap_element, node_type="capability"))
                    visited_elements.add(cap_element.id)

                    # Application realizes capability
                    edges.append(
                        {
                            "source": self.archimate_element.id,
                            "target": cap_element.id,
                            "type": "realization",
                            "label": f"{mapping.support_level or 'supports'}",
                        }
                    )

        # Find business layer elements that use this application
        relationships = (
            ArchiMateRelationship.query.options(
                joinedload(ArchiMateRelationship.source), joinedload(ArchiMateRelationship.target)
            )
            .filter(
                or_(
                    ArchiMateRelationship.source_id == self.archimate_element.id,
                    ArchiMateRelationship.target_id == self.archimate_element.id,
                )
            )
            .all()
        )

        for rel in relationships:
            # Find business layer elements
            if (
                rel.source
                and rel.source.layer == "business"
                and rel.source.id not in visited_elements
            ):
                nodes.append(self._create_node(rel.source, node_type="business"))
                visited_elements.add(rel.source.id)
                edges.append(
                    {
                        "source": rel.source.id,
                        "target": rel.target.id,
                        "type": rel.type,
                        "label": rel.type.replace("_", " "),
                    }
                )
            elif (
                rel.target
                and rel.target.layer == "business"
                and rel.target.id not in visited_elements
            ):
                nodes.append(self._create_node(rel.target, node_type="business"))
                visited_elements.add(rel.target.id)
                edges.append(
                    {
                        "source": rel.source.id,
                        "target": rel.target.id,
                        "type": rel.type,
                        "label": rel.type.replace("_", " "),
                    }
                )

        return {
            "viewpoint_type": "application_usage",
            "title": f"Application Usage: {self.app.name}",
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "central_app_id": self.application_id,
                "capabilities_count": len(capability_mappings),
                "node_count": len(nodes),
                "relationship_count": len(edges),
            },
        }

    def build_implementation_viewpoint(self) -> Dict:
        """
        Build Implementation & Migration Viewpoint (ArchiMate 3.2).

        Shows:
        - Vendor products implementing this application
        - Technology services and infrastructure
        - Deployment nodes
        - Migration paths (if replacement planned)

        Returns:
            Dict with nodes, edges, and metadata for diagram generation
        """
        if not self.archimate_element:
            return self._empty_viewpoint(
                "Implementation & Migration",
                "Link this application to an ArchiMate element to view implementation diagram",
            )

        nodes = []
        edges = []
        visited_elements = set()

        # Add central application node
        nodes.append(self._create_node(self.archimate_element, is_central=True))
        visited_elements.add(self.archimate_element.id)

        # Find vendor products
        vendor_links = (
            db.session.query(VendorProduct)
            .join(
                application_vendor_products,
                VendorProduct.id == application_vendor_products.c.vendor_product_id,
            )
            .filter(application_vendor_products.c.archimate_element_id == self.archimate_element.id)
            .all()
        )

        for product in vendor_links:
            if product.archimate_product_element_id:
                product_element = db.session.get(
                    ArchiMateElement, product.archimate_product_element_id
                )
                if product_element and product_element.id not in visited_elements:
                    nodes.append(self._create_node(product_element, node_type="product"))
                    visited_elements.add(product_element.id)

                    # Product realizes application
                    edges.append(
                        {
                            "source": product_element.id,
                            "target": self.archimate_element.id,
                            "type": "realization",
                            "label": "implements",
                        }
                    )

        # Find technology layer elements
        tech_relationships = (
            ArchiMateRelationship.query.options(
                joinedload(ArchiMateRelationship.source), joinedload(ArchiMateRelationship.target)
            )
            .filter(
                or_(
                    ArchiMateRelationship.source_id == self.archimate_element.id,
                    ArchiMateRelationship.target_id == self.archimate_element.id,
                )
            )
            .all()
        )

        for rel in tech_relationships:
            if (
                rel.source
                and rel.source.layer == "technology"
                and rel.source.id not in visited_elements
            ):
                nodes.append(self._create_node(rel.source, node_type="technology"))
                visited_elements.add(rel.source.id)
                edges.append(
                    {
                        "source": rel.source.id,
                        "target": rel.target.id,
                        "type": rel.type,
                        "label": rel.type.replace("_", " "),
                    }
                )
            elif (
                rel.target
                and rel.target.layer == "technology"
                and rel.target.id not in visited_elements
            ):
                nodes.append(self._create_node(rel.target, node_type="technology"))
                visited_elements.add(rel.target.id)
                edges.append(
                    {
                        "source": rel.source.id,
                        "target": rel.target.id,
                        "type": rel.type,
                        "label": rel.type.replace("_", " "),
                    }
                )

        # Check for replacement application (migration path)
        if self.app.replacement_application:
            # This would require looking up the replacement app
            # For now, just add metadata
            pass

        return {
            "viewpoint_type": "implementation_migration",
            "title": f"Implementation & Migration: {self.app.name}",
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "central_app_id": self.application_id,
                "vendor_products_count": len(vendor_links),
                "node_count": len(nodes),
                "relationship_count": len(edges),
                "has_replacement": bool(self.app.replacement_application),
            },
        }

    def build_motivation_viewpoint(self) -> Dict:
        """
        Build Motivation & Compliance Viewpoint (ArchiMate 3.2).

        Shows:
        - Goals realized by this application
        - Requirements implemented
        - Drivers influencing the application
        - Constraints limiting the application
        - Stakeholders interested in the application

        Returns:
            Dict with nodes, edges, and metadata for diagram generation
        """
        if not self.archimate_element:
            return self._empty_viewpoint(
                "Motivation & Compliance",
                "Link this application to an ArchiMate element to view motivation diagram",
            )

        nodes = []
        edges = []
        visited_elements = set()

        # Add central application node
        nodes.append(self._create_node(self.archimate_element, is_central=True))
        visited_elements.add(self.archimate_element.id)

        # Find requirements linked to this application
        from app.models.models import Requirement
        from app.models.relationship_tables import ApplicationRequirementMapping

        requirements = (
            db.session.query(Requirement)
            .join(
                ApplicationRequirementMapping,
                Requirement.id == ApplicationRequirementMapping.requirement_id,
            )
            .filter(ApplicationRequirementMapping.application_component_id == self.application_id)
            .all()
        )

        for req in requirements:
            if req.archimate_element_id:
                req_element = db.session.get(ArchiMateElement, req.archimate_element_id)
                if req_element and req_element.id not in visited_elements:
                    nodes.append(self._create_node(req_element, node_type="requirement"))
                    visited_elements.add(req_element.id)

                    # Application realizes requirement
                    edges.append(
                        {
                            "source": self.archimate_element.id,
                            "target": req_element.id,
                            "type": "realization",
                            "label": "implements",
                        }
                    )

                    # Find goals, drivers, stakeholders linked to requirement
                    if req.goal_id:
                        goal_element = db.session.get(ArchiMateElement, req.goal_id)
                        if goal_element and goal_element.id not in visited_elements:
                            nodes.append(self._create_node(goal_element, node_type="goal"))
                            visited_elements.add(goal_element.id)
                            edges.append(
                                {
                                    "source": req_element.id,
                                    "target": goal_element.id,
                                    "type": "realization",
                                    "label": "realizes",
                                }
                            )

                    if req.driver_id:
                        driver_element = db.session.get(ArchiMateElement, req.driver_id)
                        if driver_element and driver_element.id not in visited_elements:
                            nodes.append(self._create_node(driver_element, node_type="driver"))
                            visited_elements.add(driver_element.id)
                            edges.append(
                                {
                                    "source": driver_element.id,
                                    "target": req_element.id,
                                    "type": "influence",
                                    "label": "drives",
                                }
                            )

        return {
            "viewpoint_type": "motivation_compliance",
            "title": f"Motivation & Compliance: {self.app.name}",
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                "central_app_id": self.application_id,
                "requirements_count": len(requirements),
                "node_count": len(nodes),
                "relationship_count": len(edges),
            },
        }

    def calculate_impact_score(self, change_type: str = "modification") -> Dict:
        """
        Calculate impact score for changes to this application.

        Args:
            change_type: Type of change (modification, retirement, replacement)

        Returns:
            Dict with impact score (0 - 100) and breakdown by category
        """
        if not self.archimate_element:
            return {"total_score": 0, "breakdown": {}, "risk_level": "unknown"}

        impact_breakdown = {
            "downstream_dependencies": 0,
            "upstream_dependencies": 0,
            "business_criticality": 0,
            "integration_complexity": 0,
            "vendor_lock_in": 0,
        }

        # Count downstream dependencies (what consumes this app)
        downstream = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id == self.archimate_element.id
        ).count()
        impact_breakdown["downstream_dependencies"] = min(downstream * 5, 30)

        # Count upstream dependencies (what this app consumes)
        upstream = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.target_id == self.archimate_element.id
        ).count()
        impact_breakdown["upstream_dependencies"] = min(upstream * 3, 20)

        # Business criticality
        criticality_scores = {
            "mission_critical": 30,
            "business_critical": 20,
            "important": 10,
            "supporting": 5,
        }
        impact_breakdown["business_criticality"] = criticality_scores.get(
            self.app.business_criticality, 0
        )

        # Integration complexity (number of interfaces)
        interface_count = ApplicationInterface.query.filter(
            ApplicationInterface.provider_application_id == self.archimate_element.id
        ).count()
        impact_breakdown["integration_complexity"] = min(interface_count * 4, 15)

        # Vendor lock-in (proprietary vendor products)
        vendor_count = (
            db.session.query(VendorProduct)
            .join(
                application_vendor_products,
                VendorProduct.id == application_vendor_products.c.vendor_product_id,
            )
            .filter(application_vendor_products.c.archimate_element_id == self.archimate_element.id)
            .count()
        )
        impact_breakdown["vendor_lock_in"] = min(vendor_count * 3, 15)

        total_score = sum(impact_breakdown.values())

        # Determine risk level
        if total_score >= 70:
            risk_level = "critical"
        elif total_score >= 50:
            risk_level = "high"
        elif total_score >= 30:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "total_score": total_score,
            "breakdown": impact_breakdown,
            "risk_level": risk_level,
            "change_type": change_type,
            "recommendations": self._get_impact_recommendations(total_score, change_type),
        }

    # Private helper methods

    def _traverse_cooperation_relationships(
        self,
        element: ArchiMateElement,
        nodes: List,
        edges: List,
        visited: Set,
        current_depth: int,
        max_depth: int,
    ):
        """Recursively traverse application cooperation relationships."""
        if current_depth >= max_depth:
            return

        relationships = (
            ArchiMateRelationship.query.options(
                joinedload(ArchiMateRelationship.source), joinedload(ArchiMateRelationship.target)
            )
            .filter(
                or_(
                    ArchiMateRelationship.source_id == element.id,
                    ArchiMateRelationship.target_id == element.id,
                )
            )
            .all()
        )

        for rel in relationships:
            # Only include application layer elements
            related_element = None
            is_outbound = rel.source_id == element.id

            if is_outbound and rel.target and rel.target.layer == "application":
                related_element = rel.target
            elif not is_outbound and rel.source and rel.source.layer == "application":
                related_element = rel.source

            if related_element and related_element.id not in visited:
                nodes.append(self._create_node(related_element))
                visited.add(related_element.id)

                edges.append(
                    {
                        "source": rel.source.id,
                        "target": rel.target.id,
                        "type": rel.type,
                        "label": rel.type.replace("_", " "),
                        "direction": "outbound" if is_outbound else "inbound",
                    }
                )

                # Recurse
                self._traverse_cooperation_relationships(
                    related_element, nodes, edges, visited, current_depth + 1, max_depth
                )

    def _create_node(
        self,
        element: ArchiMateElement,
        is_central: bool = False,
        node_type: str = None,
        metadata: Dict = None,
    ) -> Dict:
        """Create a node dictionary for diagram rendering."""
        return {
            "id": element.id,
            "label": element.name,
            "type": node_type or element.type,
            "layer": element.layer,
            "is_central": is_central,
            "description": element.description,
            "metadata": metadata or {},
        }

    def _empty_viewpoint(self, viewpoint_name: str, message: str) -> Dict:
        """Return empty viewpoint structure with message."""
        return {
            "viewpoint_type": viewpoint_name.lower().replace(" ", "_"),
            "title": viewpoint_name,
            "nodes": [],
            "edges": [],
            "metadata": {"message": message, "empty": True},
        }

    def _get_impact_recommendations(self, score: int, change_type: str) -> List[str]:
        """Generate recommendations based on impact score."""
        recommendations = []

        if score >= 70:
            recommendations.append(
                "⚠️ Critical impact - Requires executive approval and detailed migration plan"
            )
            recommendations.append(
                "Conduct comprehensive impact analysis across all dependent systems"
            )
            recommendations.append("Plan for extended testing and rollback procedures")
        elif score >= 50:
            recommendations.append("⚠️ High impact - Requires architecture review board approval")
            recommendations.append("Identify and notify all stakeholders of dependent systems")
            recommendations.append("Create detailed integration testing plan")
        elif score >= 30:
            recommendations.append("Medium impact - Standard change management process applies")
            recommendations.append("Review integration points and update documentation")
        else:
            recommendations.append("Low impact - Proceed with standard deployment process")

        if change_type == "retirement":
            recommendations.append("Ensure all dependent applications have migration paths")

        return recommendations


def validate_relationship(
    source_element: ArchiMateElement, target_element: ArchiMateElement, relationship_type: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate if a relationship is allowed per ArchiMate 3.2 metamodel.

    Args:
        source_element: Source ArchiMate element
        target_element: Target ArchiMate element
        relationship_type: Proposed relationship type

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Basic validation
    if not source_element or not target_element:
        return False, "Source and target elements are required"

    if not relationship_type:
        return False, "Relationship type is required"

    # Check if relationship type is valid for source layer
    source_layer = source_element.layer or "unknown"
    allowed_types = ArchiMateViewpointBuilder.ALLOWED_RELATIONSHIPS.get(source_layer, [])

    if relationship_type not in allowed_types:
        return False, f"Relationship '{relationship_type}' not allowed for {source_layer} layer"

    # Additional metamodel rules can be added here
    # For example: composition relationships must be within same layer
    if relationship_type == "composition" and source_element.layer != target_element.layer:
        return False, "Composition relationships must be within the same layer"

    return True, None


class ArchiMateElementCloner:
    """Service for cloning vendor product ArchiMate elements to applications."""

    def __init__(self, vendor_product_id, application_component_id):
        """
        Initialize cloner for specific vendor product and application.

        Args:
            vendor_product_id: ID of the vendor product to clone from
            application_component_id: ID of the application component to clone to
        """
        self.vendor_product_id = vendor_product_id
        self.application_component_id = application_component_id
        self.cloned_elements = {}
        self.cloned_relationships = []

    def clone_all_elements(self):
        """
        Clone all ArchiMate elements from vendor product to application.

        Returns:
            dict: Cloning results with element counts and IDs
        """
        logger.info(
            f"Starting ArchiMate element cloning for vendor product {self.vendor_product_id} to application {self.application_component_id}"
        )

        try:
            # Get vendor product
            vendor_product = VendorProduct.query.get(self.vendor_product_id)
            if not vendor_product:
                raise ValueError(f"Vendor product {self.vendor_product_id} not found")

            # Get template elements linked to this vendor product
            template_elements = self._get_template_elements()
            logger.info(f"Found {len(template_elements)} template elements to clone")

            # Clone elements in dependency order (parents first)
            cloned_count = 0
            for element in template_elements:
                cloned_element = self._clone_element(element)
                if cloned_element:
                    self.cloned_elements[element.id] = cloned_element
                    cloned_count += 1

            # Clone relationships between cloned elements
            self._clone_relationships()

            # Update junction table to link cloned elements to application
            self._update_junction_table()

            # Commit all changes
            db.session.commit()

            logger.info(
                f"Successfully cloned {cloned_count} elements and {len(self.cloned_relationships)} relationships"
            )

            return {
                "success": True,
                "elements_cloned": cloned_count,
                "relationships_cloned": len(self.cloned_relationships),
                "element_ids": [elem.id for elem in self.cloned_elements.values()],
                "vendor_product_id": self.vendor_product_id,
                "application_component_id": self.application_component_id,
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error during ArchiMate element cloning: {str(e)}")
            raise e

    def _get_template_elements(self):
        """Get all template elements linked to this vendor product."""
        # Query through application_vendor_products junction table
        # vendor_products carries no organization_id, so vendor_product_id does
        # not scope anything — the template elements it reaches do belong to an
        # org, and without this predicate another tenant's templates are read
        # (and then cloned into this tenant's application).
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_and = " AND ae.organization_id = :org" if _org is not None else ""
        query = text(
            f"""
            SELECT ae.id FROM archimate_elements ae
            JOIN application_vendor_products avp ON ae.id = avp.archimate_element_id
            WHERE avp.vendor_product_id = :vendor_product_id
            AND ae.application_component_id IS NULL{_org_and}
            ORDER BY ae.type, ae.name
        """
        )

        result = db.session.execute(
            query,
            {"vendor_product_id": self.vendor_product_id,
             **({"org": _org} if _org is not None else {})},
        )
        return [ArchiMateElement.query.get(row[0]) for row in result]

    def _clone_element(self, template_element):
        """Clone a single template element to application-specific instance."""
        try:
            # Create new element as copy of template
            cloned_element = ArchiMateElement(
                name=template_element.name,
                type=template_element.type,
                layer=template_element.layer,
                description=template_element.description,
                documentation=template_element.documentation,
                properties=template_element.properties,
                application_component_id=self.application_component_id,
                architecture_id=None,  # Cloned elements don't belong to architecture models
                template_element_id=template_element.id,  # Track source for traceability
                source_product_id=self.vendor_product_id,  # Track vendor product source
                is_customized=False,
            )

            db.session.add(cloned_element)
            db.session.flush()  # Get the ID without committing

            logger.debug(
                f"Cloned element {template_element.name} (ID: {template_element.id}) -> {cloned_element.id}"
            )
            return cloned_element

        except Exception as e:
            logger.error(f"Error cloning element {template_element.id}: {str(e)}")
            return None

    def _clone_relationships(self):
        """Clone relationships between cloned elements."""
        if not self.cloned_elements:
            return

        # Get relationships between template elements
        template_element_ids = list(self.cloned_elements.keys())
        if len(template_element_ids) < 2:
            return

        # template_element_ids already come from the org-scoped template query
        # above; the explicit predicate is defence in depth for the raw path.
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_and = " AND organization_id = :org" if _org is not None else ""
        query = text(
            f"""
            SELECT * FROM archimate_relationships
            WHERE source_id IN :template_ids
            AND target_id IN :template_ids{_org_and}
        """
        )

        result = db.session.execute(
            query,
            {"template_ids": tuple(template_element_ids),
             **({"org": _org} if _org is not None else {})},
        )

        for row in result:
            source_id = row["source_id"]
            target_id = row["target_id"]

            # Only clone if both source and target were cloned
            if source_id in self.cloned_elements and target_id in self.cloned_elements:
                cloned_source = self.cloned_elements[source_id]
                cloned_target = self.cloned_elements[target_id]

                cloned_relationship = ArchiMateRelationship(
                    source_id=cloned_source.id,
                    target_id=cloned_target.id,
                    type=row["type"],
                    architecture_id=None,  # Cloned relationships don't belong to architecture models
                )

                db.session.add(cloned_relationship)
                self.cloned_relationships.append(cloned_relationship)

                logger.debug(f"Cloned relationship {row['type']} from {source_id} to {target_id}")

    def _update_junction_table(self):
        """Update application_vendor_products junction table for cloned elements."""
        if not self.cloned_elements:
            return

        for cloned_element in self.cloned_elements.values():
            # Check if junction record already exists
            existing_query = text(
                """
                SELECT 1 FROM application_vendor_products
                WHERE archimate_element_id = :element_id
                AND vendor_product_id = :vendor_product_id
            """
            )

            existing = db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id, vendor_product_id)
                existing_query,
                {"element_id": cloned_element.id, "vendor_product_id": self.vendor_product_id},
            ).first()

            if not existing:
                # Create junction record
                insert_query = text(
                    """
                    INSERT INTO application_vendor_products
                    (archimate_element_id, vendor_product_id, deployment_type, criticality, hosting_model, created_at)
                    VALUES (:element_id, :vendor_product_id, 'primary_system', 'business_critical', 'cloud', :created_at)
                """
                )

                db.session.execute(  # tenant-filtered: scoped via parent FK (archimate_element_id, vendor_product_id)
                    insert_query,
                    {
                        "element_id": cloned_element.id,
                        "vendor_product_id": self.vendor_product_id,
                        "created_at": datetime.utcnow(),
                    },
                )

                logger.debug(f"Created junction record for element {cloned_element.id}")


def clone_vendor_archimate_to_application(vendor_product_id, application_component_id):
    """
    Convenience function to clone all vendor product ArchiMate elements to an application.

    Args:
        vendor_product_id: ID of the vendor product
        application_component_id: ID of the application component

    Returns:
        dict: Cloning results
    """
    cloner = ArchiMateElementCloner(vendor_product_id, application_component_id)
    return cloner.clone_all_elements()


def get_vendor_template_elements(vendor_product_id):
    """
    Get all template elements for a vendor product.

    Args:
        vendor_product_id: ID of the vendor product

    Returns:
        list: Template ArchiMateElement objects
    """
    # vendor_products has no organization_id, so vendor_product_id alone does
    # not confine the result to one tenant; archimate_elements does carry it.
    from flask import g as _g
    _org = getattr(_g, "current_org_id", None)
    _org_and = " AND ae.organization_id = :org" if _org is not None else ""
    query = text(
        f"""
        SELECT ae.id FROM archimate_elements ae
        JOIN application_vendor_products avp ON ae.id = avp.archimate_element_id
        WHERE avp.vendor_product_id = :vendor_product_id
        AND ae.application_component_id IS NULL{_org_and}
        ORDER BY ae.type, ae.name
    """
    )

    result = db.session.execute(
        query,
        {"vendor_product_id": vendor_product_id,
         **({"org": _org} if _org is not None else {})},
    )
    return [ArchiMateElement.query.get(row[0]) for row in result]


class UnifiedArchiMateServices:
    """
    Single interface to all ArchiMate services.

    Provides a unified API for accessing all ArchiMate-related functionality:
    - Model validation
    - Metrics calculation
    - Viewpoint building
    - Element cloning

    Usage:
        services = UnifiedArchiMateServices()
        validation_result = services.validate_model(model)
        metrics = services.calculate_metrics(model)
        viewpoint = services.build_cooperation_viewpoint(application_id, depth=2)
        clone_result = services.clone_elements(vendor_product_id, application_id)
    """

    def __init__(self):
        """Initialize all service instances."""
        self.validator = ArchiMateValidator()
        self.metrics = ArchiMateMetricsService()
        self.element_cloner = None  # Initialized per-operation

    # Validation methods
    def validate_model(self, model):
        """
        Validate an ArchiMate model for compliance.

        Args:
            model: ArchitectureModel instance

        Returns:
            Dict with validation results
        """
        return self.validator.validate_model(model)

    def validate_element_type(self, element):
        """
        Validate that element type belongs to its declared layer.

        Args:
            element: ArchiMateElement to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        return self.validator.validate_element_type(element)

    def validate_relationship(self, relationship):
        """
        Validate ArchiMate relationship against metamodel rules.

        Args:
            relationship: ArchiMateRelationship to validate

        Returns:
            Tuple of (is_valid, error_message, allowed_types)
        """
        return self.validator.validate_relationship(relationship)

    def get_allowed_relationship_types(self, source_type, target_type):
        """
        Get list of allowed relationship types between two element types.

        Args:
            source_type: Source element type
            target_type: Target element type

        Returns:
            List of allowed relationship type strings
        """
        return self.validator.get_allowed_relationship_types(source_type, target_type)

    def get_valid_types_for_layer(self, layer):
        """
        Get all valid element types for a given layer.

        Args:
            layer: Layer name (case-insensitive)

        Returns:
            List of valid element type strings
        """
        return self.validator.get_valid_types_for_layer(layer)

    def suggest_relationships(self, source, existing_targets):
        """
        Suggest valid relationships from source to existing target elements.

        Args:
            source: Source ArchiMateElement
            existing_targets: List of potential target elements

        Returns:
            List of dictionaries with suggestions
        """
        return self.validator.suggest_relationships(source, existing_targets)

    # Metrics methods
    def calculate_all_metrics(self, model):
        """
        Calculate comprehensive metrics for an architecture model.

        Args:
            model: ArchitectureModel to analyze

        Returns:
            Dictionary with all calculated metrics
        """
        return self.metrics.calculate_all_metrics(model)

    def calculate_structure_metrics(self, elements, relationships):
        """
        Calculate structural metrics about the model.

        Args:
            elements: List of ArchiMateElement instances
            relationships: List of ArchiMateRelationship instances

        Returns:
            Dictionary with structural metrics
        """
        return self.metrics.calculate_structure_metrics(elements, relationships)

    def calculate_quality_metrics(self, elements, relationships):
        """
        Calculate quality metrics for documentation and completeness.

        Args:
            elements: List of ArchiMateElement instances
            relationships: List of ArchiMateRelationship instances

        Returns:
            Dictionary with quality metrics
        """
        return self.metrics.calculate_quality_metrics(elements, relationships)

    def calculate_layer_metrics(self, elements):
        """
        Calculate metrics about layer distribution and coverage.

        Args:
            elements: List of ArchiMateElement instances

        Returns:
            Dictionary with layer metrics
        """
        return self.metrics.calculate_layer_metrics(elements)

    def calculate_relationship_metrics(self, relationships, elements):
        """
        Calculate metrics about relationships and their quality.

        Args:
            relationships: List of ArchiMateRelationship instances
            elements: List of ArchiMateElement instances

        Returns:
            Dictionary with relationship metrics
        """
        return self.metrics.calculate_relationship_metrics(relationships, elements)

    def calculate_complexity_metrics(self, elements, relationships):
        """
        Calculate complexity metrics for the architecture.

        Args:
            elements: List of ArchiMateElement instances
            relationships: List of ArchiMateRelationship instances

        Returns:
            Dictionary with complexity metrics
        """
        return self.metrics.calculate_complexity_metrics(elements, relationships)

    def calculate_completeness_score(self, elements, relationships):
        """
        Calculate how complete the architecture model is.

        Args:
            elements: List of ArchiMateElement instances
            relationships: List of ArchiMateRelationship instances

        Returns:
            Dictionary with completeness metrics
        """
        return self.metrics.calculate_completeness_score(elements, relationships)

    def calculate_technical_debt(self, elements, relationships):
        """
        Calculate technical debt indicators.

        Args:
            elements: List of ArchiMateElement instances
            relationships: List of ArchiMateRelationship instances

        Returns:
            Dictionary with technical debt metrics
        """
        return self.metrics.calculate_technical_debt(elements, relationships)

    def calculate_overall_health_score(self, metrics):
        """
        Calculate overall architecture health score from individual metrics.

        Args:
            metrics: Dictionary with all calculated metrics

        Returns:
            Overall health score (0 - 100)
        """
        return self.metrics.calculate_overall_health_score(metrics)

    # Viewpoint builder methods
    def build_cooperation_viewpoint(self, application_id, depth=2):
        """
        Build Application Cooperation Viewpoint (ArchiMate 3.2).

        Args:
            application_id: ID of the ApplicationComponent
            depth: Relationship traversal depth (1 - 3)

        Returns:
            Dict with nodes, edges, and metadata for diagram generation
        """
        builder = ArchiMateViewpointBuilder(application_id)
        return builder.build_cooperation_viewpoint(depth)

    def build_usage_viewpoint(self, application_id, depth=2):
        """
        Build Application Usage Viewpoint (ArchiMate 3.2).

        Args:
            application_id: ID of the ApplicationComponent
            depth: Relationship traversal depth (1 - 3)

        Returns:
            Dict with nodes, edges, and metadata for diagram generation
        """
        builder = ArchiMateViewpointBuilder(application_id)
        return builder.build_usage_viewpoint(depth)

    def build_implementation_viewpoint(self, application_id):
        """
        Build Implementation & Migration Viewpoint (ArchiMate 3.2).

        Args:
            application_id: ID of the ApplicationComponent

        Returns:
            Dict with nodes, edges, and metadata for diagram generation
        """
        builder = ArchiMateViewpointBuilder(application_id)
        return builder.build_implementation_viewpoint()

    def build_motivation_viewpoint(self, application_id):
        """
        Build Motivation & Compliance Viewpoint (ArchiMate 3.2).

        Args:
            application_id: ID of the ApplicationComponent

        Returns:
            Dict with nodes, edges, and metadata for diagram generation
        """
        builder = ArchiMateViewpointBuilder(application_id)
        return builder.build_motivation_viewpoint()

    def calculate_impact_score(self, application_id, change_type="modification"):
        """
        Calculate impact score for changes to an application.

        Args:
            application_id: ID of the ApplicationComponent
            change_type: Type of change (modification, retirement, replacement)

        Returns:
            Dict with impact score (0 - 100) and breakdown by category
        """
        builder = ArchiMateViewpointBuilder(application_id)
        return builder.calculate_impact_score(change_type)

    # Element cloning methods
    def clone_elements(self, vendor_product_id, application_component_id):
        """
        Clone all ArchiMate elements from vendor product to application.

        Args:
            vendor_product_id: ID of the vendor product to clone from
            application_component_id: ID of the application component to clone to

        Returns:
            dict: Cloning results with element counts and IDs
        """
        cloner = ArchiMateElementCloner(vendor_product_id, application_component_id)
        return cloner.clone_all_elements()

    def get_vendor_template_elements(self, vendor_product_id):
        """
        Get all template elements for a vendor product.

        Args:
            vendor_product_id: ID of the vendor product

        Returns:
            list: Template ArchiMateElement objects
        """
        return get_vendor_template_elements(vendor_product_id)
