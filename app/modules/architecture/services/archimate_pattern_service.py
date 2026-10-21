"""
ArchiMate Pattern Recognition Service

Detects and recommends architecture patterns in ArchiMate models.
Includes both rule-based pattern detection and AI-powered pattern analysis.
"""

import logging
from collections import defaultdict
from typing import Dict, List

from app.models import ArchiMateElement, ArchiMateRelationship, ArchitectureModel
from app.models.archimate_element_types import is_application_kind

logger = logging.getLogger(__name__)


class ArchiMatePatternService:
    """
    Service for detecting and recommending architecture patterns.

    Patterns include:
    - Layered Architecture
    - Microservices
    - Service-Oriented Architecture (SOA)
    - Event-Driven Architecture
    - Hub-and-Spoke Integration
    - Three-Tier Architecture
    - CQRS
    - API Gateway
    - Database per Service
    - Strangler Fig (Migration)
    """

    def __init__(self):
        """Initialize pattern service."""
        self.patterns = self._load_pattern_definitions()

    def _load_pattern_definitions(self) -> Dict:
        """Load standard architecture pattern definitions."""
        return {
            "layered_architecture": {
                "name": "Layered Architecture",
                "description": "Clear separation of concerns across business, application, and technology layers",
                "indicators": {
                    "has_multiple_layers": True,
                    "min_layers": 3,
                    "cross_layer_serving": True,
                },
                "benefits": [
                    "Clear separation of concerns",
                    "Easier maintenance and evolution",
                    "Technology independence at business layer",
                ],
                "concerns": [
                    "May introduce performance overhead",
                    "Can lead to anemic domain models if not careful",
                ],
            },
            "service_oriented": {
                "name": "Service-Oriented Architecture (SOA)",
                "description": "Business capabilities exposed as reusable services",
                "indicators": {
                    "business_service_ratio": 0.3,  # At least 30% of business elements are services
                    "application_service_ratio": 0.4,  # At least 40% of app elements are services
                    "service_realization": True,  # Services realized by components
                },
                "benefits": [
                    "Reusable business capabilities",
                    "Technology agnostic interfaces",
                    "Easier integration",
                ],
                "concerns": [
                    "Service granularity challenges",
                    "Governance complexity",
                    "Performance overhead of service calls",
                ],
            },
            "microservices": {
                "name": "Microservices Architecture",
                "description": "Multiple small, independent application components with specific responsibilities",
                "indicators": {
                    "min_app_components": 5,
                    "independent_data": True,  # Each component has own data
                    "service_per_component": True,  # Each component exposes services
                    "low_coupling": True,  # Few composition relationships
                },
                "benefits": [
                    "Independent deployment and scaling",
                    "Technology diversity",
                    "Fault isolation",
                    "Team autonomy",
                ],
                "concerns": [
                    "Distributed system complexity",
                    "Data consistency challenges",
                    "Network latency",
                    "Operational overhead",
                ],
            },
            "event_driven": {
                "name": "Event-Driven Architecture",
                "description": "Components react to events rather than direct calls",
                "indicators": {
                    "event_ratio": 0.2,  # At least 20% of elements are events
                    "triggering_relationships": True,
                    "event_handlers": True,
                },
                "benefits": [
                    "Loose coupling",
                    "Scalability",
                    "Real-time processing",
                    "Flexibility",
                ],
                "concerns": [
                    "Event ordering challenges",
                    "Debugging difficulty",
                    "Event schema evolution",
                    "Eventual consistency",
                ],
            },
            "hub_and_spoke": {
                "name": "Hub-and-Spoke Integration",
                "description": "Central integration component connecting multiple systems",
                "indicators": {
                    "central_hub": True,  # One component with many connections
                    "hub_connectivity": 5,  # Hub connects to at least 5 components
                },
                "benefits": [
                    "Centralized integration logic",
                    "Easier monitoring",
                    "Reduced point-to-point connections",
                ],
                "concerns": [
                    "Single point of failure",
                    "Performance bottleneck",
                    "Scaling challenges",
                ],
            },
            "three_tier": {
                "name": "Three-Tier Architecture",
                "description": "Presentation, business logic, and data layers",
                "indicators": {
                    "has_presentation": True,
                    "has_business_logic": True,
                    "has_data_layer": True,
                    "tier_separation": True,
                },
                "benefits": [
                    "Well-understood pattern",
                    "Clear separation of concerns",
                    "Scalable tiers independently",
                ],
                "concerns": ["Can be overly rigid", "May not fit modern cloud architectures"],
            },
            "api_gateway": {
                "name": "API Gateway Pattern",
                "description": "Single entry point for external access to services",
                "indicators": {
                    "gateway_component": True,
                    "external_interface": True,
                    "routes_to_services": True,
                },
                "benefits": [
                    "Simplified client access",
                    "Centralized cross-cutting concerns",
                    "Protocol translation",
                ],
                "concerns": [
                    "Single point of failure",
                    "Additional network hop",
                    "Can become complex",
                ],
            },
            "database_per_service": {
                "name": "Database per Service",
                "description": "Each service has its own database",
                "indicators": {"data_per_component": True, "no_shared_data": True},
                "benefits": ["Service independence", "Technology diversity", "Easier scaling"],
                "concerns": [
                    "Data consistency",
                    "Complex queries across services",
                    "Data duplication",
                ],
            },
            "strangler_fig": {
                "name": "Strangler Fig Migration Pattern",
                "description": "Gradual migration from legacy to modern systems",
                "indicators": {
                    "has_legacy": True,
                    "has_modern": True,
                    "has_migration_elements": True,
                    "has_plateau_gap": True,
                },
                "benefits": [
                    "Incremental migration",
                    "Reduced risk",
                    "Continuous business operation",
                ],
                "concerns": ["Temporary complexity", "Dual maintenance", "Integration challenges"],
            },
        }

    def detect_patterns(self, model: ArchitectureModel) -> List[Dict]:
        """
        Detect architecture patterns in the model using rule-based analysis.

        Args:
            model: ArchitectureModel to analyze

        Returns:
            List of detected patterns with confidence scores and evidence
        """
        elements = list(model.archimate_elements.all())
        relationships = list(model.archimate_relationships.all())

        if not elements:
            return []

        detected_patterns = []

        # Analyze for each pattern
        for pattern_id, pattern_def in self.patterns.items():
            detection_result = self._detect_pattern(
                pattern_id, pattern_def, elements, relationships
            )

            if detection_result["confidence"] > 20:  # Only include if confidence > 20%
                detected_patterns.append(detection_result)

        # Sort by confidence
        detected_patterns.sort(key=lambda x: x["confidence"], reverse=True)

        return detected_patterns

    def _detect_pattern(
        self,
        pattern_id: str,
        pattern_def: Dict,
        elements: List[ArchiMateElement],
        relationships: List[ArchiMateRelationship],
    ) -> Dict:
        """Detect a specific pattern in the architecture."""
        confidence = 0
        evidence = []
        missing = []

        indicators = pattern_def.get("indicators", {})

        # Build analysis structures
        layer_counts = defaultdict(int)
        element_type_counts = defaultdict(int)
        relationship_type_counts = defaultdict(int)

        for elem in elements:
            layer_counts[elem.layer] += 1
            element_type_counts[elem.type] += 1

        for rel in relationships:
            relationship_type_counts[rel.type] += 1

        total_elements = len(elements)
        len(relationships)

        # Pattern-specific detection logic
        if pattern_id == "layered_architecture":
            num_layers = len(layer_counts)
            if num_layers >= indicators["min_layers"]:
                confidence += 40
                evidence.append(f"Model has {num_layers} layers showing clear separation")
            else:
                missing.append(f"Only {num_layers} layers (need {indicators['min_layers']})")

            # Check for serving relationships across layers
            serving_rels = relationship_type_counts.get("Serving", 0)
            if serving_rels > 0:
                confidence += 30
                evidence.append(f"{serving_rels} serving relationships across layers")

            # Check layer distribution
            if layer_counts.get("business", 0) > 0 and layer_counts.get("application", 0) > 0:
                confidence += 30
                evidence.append("Business and application layers both present")

        elif pattern_id == "service_oriented":
            business_services = element_type_counts.get("BusinessService", 0)
            app_services = element_type_counts.get("ApplicationService", 0)
            tech_services = element_type_counts.get("TechnologyService", 0)

            total_services = business_services + app_services + tech_services
            if total_services > total_elements * 0.2:  # At least 20% are services
                confidence += 50
                evidence.append(
                    f"{total_services} services defined ({total_services/total_elements * 100:.1f}% of elements)"
                )

            realization_rels = relationship_type_counts.get("Realization", 0)
            if realization_rels > 0:
                confidence += 30
                evidence.append(
                    f"{realization_rels} realization relationships showing service implementations"
                )

            if business_services > 0:
                confidence += 20
                evidence.append(f"{business_services} business services exposing capabilities")
            else:
                missing.append("No business services defined")

        elif pattern_id == "microservices":
            app_components = sum(
                n for t, n in element_type_counts.items() if is_application_kind(t)
            )
            data_objects = element_type_counts.get("DataObject", 0)

            if app_components >= indicators["min_app_components"]:
                confidence += 30
                evidence.append(f"{app_components} application components (microservices)")
            else:
                missing.append(
                    f"Only {app_components} components (need {indicators['min_app_components']})"
                )

            # Check if components have their own data
            if data_objects >= app_components * 0.5:  # At least 50% have data
                confidence += 25
                evidence.append("Components have independent data stores")

            # Check for low coupling (few composition relationships)
            composition_rels = relationship_type_counts.get("Composition", 0)
            if app_components > 0 and composition_rels / app_components < 0.3:
                confidence += 25
                evidence.append("Low coupling between components")

            # Check for service interfaces
            app_services = element_type_counts.get("ApplicationService", 0)
            if app_services >= app_components * 0.5:
                confidence += 20
                evidence.append("Components expose service interfaces")
            else:
                missing.append("Not all components expose services")

        elif pattern_id == "event_driven":
            events = (
                element_type_counts.get("BusinessEvent", 0)
                + element_type_counts.get("ApplicationEvent", 0)
                + element_type_counts.get("TechnologyEvent", 0)
                + element_type_counts.get("ImplementationEvent", 0)
            )

            if events > 0:
                confidence += 40
                evidence.append(f"{events} event elements defined")

                event_ratio = events / total_elements
                if event_ratio > 0.15:
                    confidence += 20
                    evidence.append(f"High event ratio ({event_ratio * 100:.1f}%)")
            else:
                missing.append("No event elements found")

            triggering_rels = relationship_type_counts.get("Triggering", 0)
            if triggering_rels > 0:
                confidence += 40
                evidence.append(f"{triggering_rels} triggering relationships showing event flows")
            else:
                missing.append("No triggering relationships")

        elif pattern_id == "hub_and_spoke":
            # Find component with most connections
            component_connections = defaultdict(int)
            for rel in relationships:
                if rel.source_element:
                    component_connections[rel.source_element.id] += 1
                if rel.target_element:
                    component_connections[rel.target_element.id] += 1

            if component_connections:
                max_connections = max(component_connections.values())
                if max_connections >= indicators["hub_connectivity"]:
                    confidence += 60
                    hub_element = [
                        e
                        for e in elements
                        if e.id == max(component_connections, key=component_connections.get)
                    ][0]
                    evidence.append(
                        f"Central hub '{hub_element.name}' with {max_connections} connections"
                    )
                else:
                    missing.append(
                        f"No component has {indicators['hub_connectivity']}+ connections (max: {max_connections})"
                    )

                # Check connection distribution
                avg_connections = sum(component_connections.values()) / len(component_connections)
                if (
                    max_connections > avg_connections * 3
                ):  # Hub has 3x more connections than average
                    confidence += 40
                    evidence.append("Clear hub-and-spoke topology")

        elif pattern_id == "three_tier":
            has_presentation = any(
                t in ["BusinessInterface", "ApplicationInterface"]
                for t in element_type_counts.keys()
            )
            has_business = any(
                t in ["BusinessProcess", "BusinessFunction", "ApplicationFunction"]
                for t in element_type_counts.keys()
            )
            has_data = any(
                t in ["DataObject", "BusinessObject", "Artifact"]
                for t in element_type_counts.keys()
            )

            if has_presentation:
                confidence += 30
                evidence.append("Presentation tier present")
            else:
                missing.append("No presentation tier")

            if has_business:
                confidence += 35
                evidence.append("Business logic tier present")
            else:
                missing.append("No business logic tier")

            if has_data:
                confidence += 35
                evidence.append("Data tier present")
            else:
                missing.append("No data tier")

        elif pattern_id == "api_gateway":
            # Look for gateway pattern: one interface serving many components
            interfaces = [e for e in elements if "Interface" in e.type]

            for interface in interfaces:
                # Count components this interface serves
                served_components = [
                    r
                    for r in relationships
                    if r.source_element_id == interface.id and r.type in ["Serving", "Realization"]
                ]

                if len(served_components) >= 3:
                    confidence += 50
                    evidence.append(
                        f"Gateway '{interface.name}' serving {len(served_components)} components"
                    )

            if confidence > 0:
                confidence += 30
                evidence.append("API Gateway pattern detected")
            else:
                missing.append("No interface serving multiple components")

        elif pattern_id == "database_per_service":
            app_components = sum(
                n for t, n in element_type_counts.items() if is_application_kind(t)
            )
            data_objects = element_type_counts.get("DataObject", 0)

            if app_components > 0 and data_objects > 0:
                # Check if data objects are accessed by single components
                data_accesses = defaultdict(set)
                for rel in relationships:
                    if (
                        rel.type == "Access"
                        and rel.target_element
                        and rel.target_element.type == "DataObject"
                    ):
                        if rel.source_element:
                            data_accesses[rel.target_element.id].add(rel.source_element.id)

                exclusive_data = sum(
                    1 for accessors in data_accesses.values() if len(accessors) == 1
                )
                if exclusive_data > 0:
                    ratio = exclusive_data / len(data_accesses) if data_accesses else 0
                    confidence += int(ratio * 100)
                    evidence.append(f"{exclusive_data} data objects accessed by single component")
                else:
                    missing.append("Data objects shared across components")

        elif pattern_id == "strangler_fig":
            # Look for implementation/migration elements
            (any(
                layer_counts.get(layer, 0) > 0 for layer in ["implementation", "migration"]
            ))

            migration_elements = (
                element_type_counts.get("WorkPackage", 0)
                + element_type_counts.get("Deliverable", 0)
                + element_type_counts.get("Plateau", 0)
                + element_type_counts.get("Gap", 0)
            )

            if migration_elements > 0:
                confidence += 50
                evidence.append(f"{migration_elements} migration/implementation elements present")
            else:
                missing.append("No migration planning elements")

            # Look for old and new versions of components
            component_names = [e.name.lower() for e in elements if "Component" in e.type]
            has_legacy = any("legacy" in name or "old" in name for name in component_names)
            has_new = any(
                "new" in name or "modern" in name or "v2" in name for name in component_names
            )

            if has_legacy and has_new:
                confidence += 50
                evidence.append("Both legacy and modern components identified")
            else:
                missing.append("No clear old/new component distinction")

        return {
            "pattern_id": pattern_id,
            "pattern_name": pattern_def["name"],
            "description": pattern_def["description"],
            "confidence": min(confidence, 100),  # Cap at 100%
            "evidence": evidence,
            "missing_elements": missing,
            "benefits": pattern_def["benefits"],
            "concerns": pattern_def["concerns"],
            "completeness": int(
                (len(evidence) / (len(evidence) + len(missing)) * 100)
                if (evidence or missing)
                else 0
            ),
        }

    def suggest_pattern_completion(self, model: ArchitectureModel, pattern_id: str) -> Dict:
        """
        Suggest elements and relationships to complete a pattern.

        Args:
            model: ArchitectureModel
            pattern_id: ID of pattern to complete

        Returns:
            Dictionary with suggestions for completing the pattern
        """
        pattern_def = self.patterns.get(pattern_id)
        if not pattern_def:
            return {"error": f"Pattern {pattern_id} not found"}

        # Detect current pattern state
        elements = list(model.archimate_elements.all())
        relationships = list(model.archimate_relationships.all())

        detection = self._detect_pattern(pattern_id, pattern_def, elements, relationships)

        suggestions = {
            "pattern_name": pattern_def["name"],
            "current_completeness": detection["completeness"],
            "missing_elements": detection["missing_elements"],
            "suggested_additions": [],
        }

        # Pattern-specific suggestions
        if pattern_id == "service_oriented" and detection["completeness"] < 80:
            if element_type_counts := defaultdict(int):
                for elem in elements:
                    element_type_counts[elem.type] += 1

                if element_type_counts.get("BusinessService", 0) == 0:
                    suggestions["suggested_additions"].append(
                        {
                            "type": "element",
                            "element_type": "BusinessService",
                            "layer": "business",
                            "reason": "Add business services to expose key capabilities",
                        }
                    )

                if element_type_counts.get("ApplicationService", 0) < 3:
                    suggestions["suggested_additions"].append(
                        {
                            "type": "element",
                            "element_type": "ApplicationService",
                            "layer": "application",
                            "reason": "Add more application services to support business services",
                        }
                    )

        # Add more pattern-specific suggestions as needed

        return suggestions
