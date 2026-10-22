"""
ArchiMate Template Generation Service.

Generates ArchiMate 3.2 architectures using predefined templates and keyword extraction.
Provides instant results without LLM dependency. Complements pattern detection service.
"""
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from app.models.archimate_element_types import is_application_kind

logger = logging.getLogger(__name__)


class ArchiMateTemplateService:
    """Service for generating ArchiMate models using predefined templates."""

    # Common architectural templates
    TEMPLATES = {
        "comprehensive_enterprise": {
            "name": "Comprehensive Enterprise Architecture",
            "description": "Complete ArchiMate 3.2 architecture across all layers",
            "elements": [
                # Motivation Layer
                {"name": "Business Stakeholders", "type": "Stakeholder", "layer": "motivation"},
                {"name": "Technology Team", "type": "Stakeholder", "layer": "motivation"},
                {"name": "End Users", "type": "Stakeholder", "layer": "motivation"},
                {"name": "Regulatory Compliance", "type": "Driver", "layer": "motivation"},
                {"name": "Digital Transformation", "type": "Driver", "layer": "motivation"},
                {"name": "Improve Operational Efficiency", "type": "Goal", "layer": "motivation"},
                {"name": "Enhance Customer Experience", "type": "Goal", "layer": "motivation"},
                {
                    "name": "System Availability Requirement",
                    "type": "Requirement",
                    "layer": "motivation",
                },
                {"name": "Security Requirement", "type": "Requirement", "layer": "motivation"},
                # Strategy Layer
                {"name": "Digital Operations", "type": "Capability", "layer": "strategy"},
                {"name": "Data Management", "type": "Capability", "layer": "strategy"},
                {"name": "Customer Engagement", "type": "Capability", "layer": "strategy"},
                {
                    "name": "System Modernization Initiative",
                    "type": "CourseOfAction",
                    "layer": "strategy",
                },
                # Business Layer
                {
                    "name": "Business Process Management",
                    "type": "BusinessProcess",
                    "layer": "business",
                },
                {"name": "Customer Service", "type": "BusinessService", "layer": "business"},
                {"name": "Business User", "type": "BusinessActor", "layer": "business"},
                {"name": "Business Data", "type": "BusinessObject", "layer": "business"},
                # Application Layer
                {
                    "name": "Core Application",
                    "type": "ApplicationComponent",
                    "layer": "application",
                },
                {"name": "API Service", "type": "ApplicationService", "layer": "application"},
                {"name": "Application Data", "type": "DataObject", "layer": "application"},
                # Technology Layer
                {"name": "Application Server", "type": "Node", "layer": "technology"},
                {"name": "Database Server", "type": "Node", "layer": "technology"},
                {
                    "name": "Infrastructure Services",
                    "type": "TechnologyService",
                    "layer": "technology",
                },
            ],
            "keywords": [
                "enterprise",
                "comprehensive",
                "complete",
                "end-to-end",
                "full architecture",
            ],
        },
        "microservices": {
            "name": "Microservices Architecture",
            "description": "Distributed system with independent services",
            "elements": [
                # Motivation Layer
                {"name": "Development Team", "type": "Stakeholder", "layer": "motivation"},
                {"name": "Scalability Requirement", "type": "Driver", "layer": "motivation"},
                {"name": "Achieve High Availability", "type": "Goal", "layer": "motivation"},
                # Strategy Layer
                {
                    "name": "Service-Oriented Architecture",
                    "type": "Capability",
                    "layer": "strategy",
                },
                {
                    "name": "Microservices Adoption Strategy",
                    "type": "CourseOfAction",
                    "layer": "strategy",
                },
                # Business Layer
                {"name": "Service Management", "type": "BusinessProcess", "layer": "business"},
                # Application Layer
                {"name": "API Gateway", "type": "ApplicationComponent", "layer": "application"},
                {
                    "name": "Service Registry",
                    "type": "ApplicationComponent",
                    "layer": "application",
                },
                {
                    "name": "Authentication Service",
                    "type": "ApplicationService",
                    "layer": "application",
                },
                # Technology Layer
                {"name": "Database Cluster", "type": "Node", "layer": "technology"},
                {"name": "Message Queue", "type": "TechnologyService", "layer": "technology"},
                {"name": "Load Balancer", "type": "Node", "layer": "technology"},
            ],
            "keywords": [
                "microservice",
                "distributed",
                "service-oriented",
                "api gateway",
                "rest api",
            ],
        },
        "crud_application": {
            "name": "CRUD Application",
            "description": "Standard create, read, update, delete application",
            "elements": [
                # Motivation Layer
                {"name": "Application Users", "type": "Stakeholder", "layer": "motivation"},
                {"name": "Data Management Need", "type": "Driver", "layer": "motivation"},
                {"name": "Efficient Data Operations", "type": "Goal", "layer": "motivation"},
                {
                    "name": "Data Integrity Requirement",
                    "type": "Requirement",
                    "layer": "motivation",
                },
                # Strategy Layer
                {"name": "Data Management Capability", "type": "Capability", "layer": "strategy"},
                # Business Layer
                {"name": "Data Entry Process", "type": "BusinessProcess", "layer": "business"},
                {"name": "Data Query Service", "type": "BusinessService", "layer": "business"},
                # Application Layer
                {"name": "User Interface", "type": "ApplicationComponent", "layer": "application"},
                {
                    "name": "Business Logic Layer",
                    "type": "ApplicationComponent",
                    "layer": "application",
                },
                {
                    "name": "Data Access Layer",
                    "type": "ApplicationComponent",
                    "layer": "application",
                },
                {"name": "Database", "type": "DataObject", "layer": "application"},
                # Technology Layer
                {"name": "Application Server", "type": "Node", "layer": "technology"},
                {"name": "Database Server", "type": "Node", "layer": "technology"},
            ],
            "keywords": [
                "crud",
                "create read update delete",
                "form",
                "data entry",
                "database",
                "management",
            ],
        },
        "integration_layer": {
            "name": "Integration Layer",
            "description": "System integration and data exchange",
            "elements": [
                {"name": "Integration Hub", "type": "ApplicationComponent", "layer": "application"},
                {
                    "name": "Data Transformation",
                    "type": "ApplicationFunction",
                    "layer": "application",
                },
                {"name": "Message Broker", "type": "TechnologyService", "layer": "technology"},
                {"name": "API Adapter", "type": "ApplicationComponent", "layer": "application"},
                {"name": "Data Mapper", "type": "ApplicationComponent", "layer": "application"},
            ],
            "keywords": ["integration", "api", "etl", "data exchange", "adapter", "connector"],
        },
        "data_pipeline": {
            "name": "Data Pipeline",
            "description": "Data ingestion, processing, and analytics",
            "elements": [
                {"name": "Data Ingestion", "type": "ApplicationFunction", "layer": "application"},
                {"name": "Data Processing", "type": "ApplicationFunction", "layer": "application"},
                {"name": "Data Storage", "type": "DataObject", "layer": "application"},
                {
                    "name": "Analytics Engine",
                    "type": "ApplicationComponent",
                    "layer": "application",
                },
                {"name": "Data Warehouse", "type": "DataObject", "layer": "application"},
                {"name": "ETL Service", "type": "ApplicationService", "layer": "application"},
            ],
            "keywords": [
                "data pipeline",
                "etl",
                "analytics",
                "data warehouse",
                "processing",
                "ingestion",
            ],
        },
        "web_application": {
            "name": "Web Application",
            "description": "Standard web-based application",
            "elements": [
                {"name": "Web Frontend", "type": "ApplicationComponent", "layer": "application"},
                {"name": "Web Server", "type": "Node", "layer": "technology"},
                {
                    "name": "Application Backend",
                    "type": "ApplicationComponent",
                    "layer": "application",
                },
                {
                    "name": "Session Management",
                    "type": "ApplicationService",
                    "layer": "application",
                },
                {"name": "Authentication", "type": "ApplicationService", "layer": "application"},
                {"name": "Data Storage", "type": "DataObject", "layer": "application"},
            ],
            "keywords": ["web app", "website", "portal", "web interface", "browser"],
        },
        "mobile_app": {
            "name": "Mobile Application",
            "description": "Mobile-first application architecture",
            "elements": [
                {"name": "Mobile App", "type": "ApplicationComponent", "layer": "application"},
                {"name": "Backend API", "type": "ApplicationService", "layer": "application"},
                {
                    "name": "Push Notification Service",
                    "type": "ApplicationService",
                    "layer": "application",
                },
                {"name": "Mobile Backend", "type": "ApplicationComponent", "layer": "application"},
                {"name": "Cloud Storage", "type": "DataObject", "layer": "application"},
                {"name": "API Gateway", "type": "TechnologyService", "layer": "technology"},
            ],
            "keywords": ["mobile app", "ios", "android", "mobile", "smartphone", "tablet"],
        },
        "event_driven": {
            "name": "Event-Driven Architecture",
            "description": "Asynchronous event-based system",
            "elements": [
                {"name": "Event Producer", "type": "ApplicationComponent", "layer": "application"},
                {"name": "Event Bus", "type": "TechnologyService", "layer": "technology"},
                {"name": "Event Consumer", "type": "ApplicationComponent", "layer": "application"},
                {"name": "Event Store", "type": "DataObject", "layer": "application"},
                {"name": "Event Handler", "type": "ApplicationFunction", "layer": "application"},
            ],
            "keywords": [
                "event-driven",
                "event sourcing",
                "message bus",
                "pub-sub",
                "asynchronous",
            ],
        },
        "api_service": {
            "name": "API Service",
            "description": "RESTful API or web service",
            "elements": [
                # Motivation Layer
                {"name": "API Consumers", "type": "Stakeholder", "layer": "motivation"},
                {"name": "Integration Requirement", "type": "Driver", "layer": "motivation"},
                {"name": "Enable System Interoperability", "type": "Goal", "layer": "motivation"},
                # Strategy Layer
                {"name": "API-First Strategy", "type": "CourseOfAction", "layer": "strategy"},
                {"name": "Integration Capability", "type": "Capability", "layer": "strategy"},
                # Business Layer
                {"name": "Service Integration", "type": "BusinessProcess", "layer": "business"},
                # Application Layer
                {"name": "API Endpoint", "type": "ApplicationService", "layer": "application"},
                {"name": "Business Logic", "type": "ApplicationComponent", "layer": "application"},
                {"name": "Data Access", "type": "ApplicationFunction", "layer": "application"},
                {"name": "API Documentation", "type": "Representation", "layer": "application"},
                # Technology Layer
                {"name": "API Gateway", "type": "TechnologyService", "layer": "technology"},
                {"name": "Rate Limiter", "type": "ApplicationFunction", "layer": "application"},
            ],
            "keywords": ["api", "rest", "endpoint", "web service", "json", "http"],
        },
        "manufacturing_system": {
            "name": "Manufacturing Execution System",
            "description": "Digital manufacturing and production management architecture",
            "elements": [
                # Motivation Layer
                {
                    "name": "Manufacturing Operations Team",
                    "type": "Stakeholder",
                    "layer": "motivation",
                },
                {
                    "name": "Quality Control Department",
                    "type": "Stakeholder",
                    "layer": "motivation",
                },
                {"name": "Production Management", "type": "Stakeholder", "layer": "motivation"},
                {"name": "Operational Excellence", "type": "Driver", "layer": "motivation"},
                {"name": "Industry 4.0 Transformation", "type": "Driver", "layer": "motivation"},
                {"name": "Improve Production Efficiency", "type": "Goal", "layer": "motivation"},
                {"name": "Ensure Product Quality", "type": "Goal", "layer": "motivation"},
                {
                    "name": "Real-Time Visibility Requirement",
                    "type": "Requirement",
                    "layer": "motivation",
                },
                {"name": "Traceability Requirement", "type": "Requirement", "layer": "motivation"},
                # Strategy Layer
                {"name": "Digital Manufacturing", "type": "Capability", "layer": "strategy"},
                {"name": "Manufacturing Execution", "type": "Capability", "layer": "strategy"},
                {"name": "Quality Management", "type": "Capability", "layer": "strategy"},
                {"name": "Material Traceability", "type": "Capability", "layer": "strategy"},
                {"name": "Equipment Management", "type": "Capability", "layer": "strategy"},
                {"name": "Smart Factory Initiative", "type": "CourseOfAction", "layer": "strategy"},
                # Business Layer
                {"name": "Production Planning", "type": "BusinessProcess", "layer": "business"},
                {
                    "name": "Manufacturing Operations",
                    "type": "BusinessProcess",
                    "layer": "business",
                },
                {"name": "Quality Inspection", "type": "BusinessProcess", "layer": "business"},
                {"name": "MES Service", "type": "BusinessService", "layer": "business"},
                {"name": "Production Data", "type": "BusinessObject", "layer": "business"},
                # Application Layer
                {"name": "MES Application", "type": "ApplicationComponent", "layer": "application"},
                {
                    "name": "Quality Management System",
                    "type": "ApplicationComponent",
                    "layer": "application",
                },
                {
                    "name": "Production Scheduling",
                    "type": "ApplicationService",
                    "layer": "application",
                },
                {"name": "Material Tracking", "type": "ApplicationService", "layer": "application"},
                {"name": "Manufacturing Data", "type": "DataObject", "layer": "application"},
                # Technology Layer
                {"name": "Production Database", "type": "Node", "layer": "technology"},
                {
                    "name": "Industrial IoT Platform",
                    "type": "TechnologyService",
                    "layer": "technology",
                },
                {"name": "Edge Computing Infrastructure", "type": "Node", "layer": "technology"},
                {"name": "SCADA System", "type": "TechnologyService", "layer": "technology"},
            ],
            "keywords": [
                "manufacturing",
                "production",
                "factory",
                "industrial",
                "mes",
                "scada",
                "iot",
                "industry 4.0",
                "quality control",
            ],
        },
    }

    def detect_template(self, requirements: str) -> Optional[str]:
        """
        Detect which template best matches the requirements.

        Args:
            requirements: Requirement text

        Returns:
            Template name or None
        """
        if not requirements:
            return None

        requirements_lower = requirements.lower()

        # Score each template based on keyword matches
        scores = {}
        for template_name, template_data in self.TEMPLATES.items():
            score = 0
            keywords = template_data.get("keywords", [])

            for keyword in keywords:
                if keyword in requirements_lower:
                    # Weight by keyword length (longer = more specific)
                    score += len(keyword.split())

            if score > 0:
                scores[template_name] = score

        if not scores:
            # Default to comprehensive enterprise architecture
            return "comprehensive_enterprise"

        # Return template with highest score
        best_template = max(scores, key=scores.get)
        logger.info(f"Detected template: {best_template} (score: {scores[best_template]})")

        return best_template

    def extract_entities(self, requirements: str) -> List[str]:
        """
        Extract entity/component names from requirements using simple NLP.

        Args:
            requirements: Requirement text

        Returns:
            List of extracted entity names
        """
        entities = []

        # Pattern 1: Capitalized phrases (likely proper nouns)
        capitalized = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", requirements)
        entities.extend(capitalized)

        # Pattern 2: Quoted names
        quoted = re.findall(r'"([^"]+)"', requirements)
        entities.extend(quoted)

        # Pattern 3: "The X system/service/application"
        system_refs = re.findall(
            r"(?:the|a|an)\s+([A-Z][a-zA-Z\s]+?)\s+(?:system|service|application|component|module)",
            requirements,
        )
        entities.extend(system_refs)

        # Pattern 4: Technical terms (APIs, databases, etc.)
        technical_terms = re.findall(
            r"\b([A-Z][a-zA-Z]*(?:API|DB|Service|Server|Client|Engine))\b", requirements
        )
        entities.extend(technical_terms)

        # Clean and deduplicate
        entities = [e.strip() for e in entities if e and len(e.strip()) > 2]
        entities = list(dict.fromkeys(entities))  # Preserve order, remove duplicates

        # Limit to reasonable number
        return entities[:10]

    def generate_from_template(
        self,
        requirements: str,
        template_name: Optional[str] = None,
        model_name: Optional[str] = None,
        compliance_context: Optional[Dict] = None,
    ) -> Dict:
        """
        Generate ArchiMate model from template.

        Args:
            requirements: Requirement text
            template_name: Optional specific template (auto-detected if None)
            model_name: Optional model name
            compliance_context: Optional compliance framework context for intelligent template selection

        Returns:
            ArchiMate model dictionary
        """
        # Smart template selection based on compliance framework
        if not template_name:
            if compliance_context and "framework_code" in compliance_context:
                template_name = self._select_template_for_framework(
                    compliance_context["framework_code"], requirements
                )
                logger.info(
                    f"Selected template '{template_name}' for framework {compliance_context['framework_code']}"
                )
            else:
                template_name = self.detect_template(requirements)

        if not template_name or template_name not in self.TEMPLATES:
            logger.warning(f"Unknown template: {template_name}, using comprehensive_enterprise")
            template_name = "comprehensive_enterprise"

        template = self.TEMPLATES[template_name]

        # Extract entities from requirements
        extracted_entities = self.extract_entities(requirements)

        # Generate elements
        elements = []
        element_id = 1

        # Add template base elements
        for elem_template in template["elements"]:
            element = {
                "id": f"elem_{element_id}",
                "name": elem_template["name"],
                "type": elem_template["type"],
                "layer": elem_template["layer"],
                "description": f"Generated from {template_name} template",
            }
            elements.append(element)
            element_id += 1

        # Add custom elements from extracted entities
        for entity_name in extracted_entities[:5]:  # Limit to 5 custom entities
            # Determine type based on name
            if any(word in entity_name.lower() for word in ["service", "api", "interface"]):
                elem_type = "ApplicationService"
            elif any(word in entity_name.lower() for word in ["data", "database", "storage"]):
                elem_type = "DataObject"
            elif any(word in entity_name.lower() for word in ["server", "node", "infrastructure"]):
                elem_type = "Node"
                layer = "technology"
            else:
                elem_type = "ApplicationComponent"

            layer = "technology" if elem_type == "Node" else "application"

            element = {
                "id": f"elem_{element_id}",
                "name": entity_name,
                "type": elem_type,
                "layer": layer,
                "description": "Extracted from requirements",
            }
            elements.append(element)
            element_id += 1

        # Generate relationships
        relationships = self._generate_relationships(elements, template_name)

        # Build model
        model = {
            "model_name": model_name or f"{template['name']} - Generated",
            "model_description": template["description"],
            "template_used": template_name,
            "generation_method": "template_based",
            "elements": elements,
            "relationships": relationships,
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "template": template_name,
                "extracted_entities": extracted_entities,
                "total_elements": len(elements),
                "total_relationships": len(relationships),
            },
            "validation_results": {
                "is_valid": True,
                "errors": [],
                "warnings": [
                    "Template-based generation provides functional architecture",
                    "Consider AI-powered generation for more sophisticated analysis",
                ],
            },
        }

        logger.info(
            f"Generated ArchiMate model using template '{template_name}': "
            f"{len(elements)} elements, {len(relationships)} relationships"
        )

        return model

    def _select_template_for_framework(self, framework_code: str, requirements: str) -> str:
        """
        Select optimal template based on compliance framework.

        Args:
            framework_code: Compliance framework code (e.g., 'UK-DPP', 'UK-EPD-LCA')
            requirements: Requirements text for additional context

        Returns:
            Template name
        """
        # Framework-specific template mappings
        framework_map = {
            "UK-DPP": "manufacturing_system",  # Digital Product Passport needs full manufacturing arch
            "UK-EPD-LCA": "manufacturing_system",  # Environmental Product Declaration needs LCA tracking
            "UK-REACH": "integration_layer",  # Chemical registry needs data integration
            "UK-HSE-COSHH": "manufacturing_system",  # Health & safety needs workplace monitoring
            "UK-UKCA": "comprehensive_enterprise",  # Conformity assessment needs full compliance arch
            "ISO - 9001": "comprehensive_enterprise",  # Quality management needs complete enterprise view
            "ISO - 14001": "manufacturing_system",  # Environmental management needs operations view
            "ISO - 27001": "comprehensive_enterprise",  # Information security needs enterprise-wide arch
            "GDPR": "data_pipeline",  # Data protection needs data flow architecture
            "SOX": "comprehensive_enterprise",  # Financial controls need enterprise governance
            "PCI-DSS": "microservices",  # Payment card security needs service isolation
            "HIPAA": "microservices",  # Healthcare privacy needs secure service boundaries
        }

        # Check for exact framework code match
        if framework_code in framework_map:
            return framework_map[framework_code]

        # Check for partial matches (e.g., 'UK-' prefix for manufacturing)
        if framework_code.startswith("UK-"):
            return "manufacturing_system"

        # Fall back to keyword detection
        return self.detect_template(requirements)

    def _generate_relationships(self, elements: List[Dict], template_name: str) -> List[Dict]:
        """
        Generate relationships between elements based on template.

        Args:
            elements: List of element dictionaries
            template_name: Template being used

        Returns:
            List of relationship dictionaries
        """
        relationships = []
        rel_id = 1

        # Group elements by layer and type
        app_components = [e for e in elements if is_application_kind(e["type"])]
        app_services = [e for e in elements if e["type"] == "ApplicationService"]
        data_objects = [e for e in elements if e["type"] == "DataObject"]
        tech_nodes = [e for e in elements if e["layer"] == "technology"]

        # Rule 1: Application components access data objects
        for comp in app_components[:3]:  # Limit relationships
            for data in data_objects[:2]:
                relationships.append(
                    {
                        "id": f"rel_{rel_id}",
                        "type": "access",
                        "source_id": comp["id"],
                        "target_id": data["id"],
                        "description": f"{comp['name']} accesses {data['name']}",
                    }
                )
                rel_id += 1

        # Rule 2: Application services realize components
        for i, service in enumerate(app_services):
            if i < len(app_components):
                relationships.append(
                    {
                        "id": f"rel_{rel_id}",
                        "type": "realization",
                        "source_id": app_components[i]["id"],
                        "target_id": service["id"],
                        "description": f"{app_components[i]['name']} realizes {service['name']}",
                    }
                )
                rel_id += 1

        # Rule 3: Technology nodes serve application components
        for i, comp in enumerate(app_components):
            if i < len(tech_nodes):
                relationships.append(
                    {
                        "id": f"rel_{rel_id}",
                        "type": "serving",
                        "source_id": tech_nodes[i]["id"],
                        "target_id": comp["id"],
                        "description": f"{tech_nodes[i]['name']} serves {comp['name']}",
                    }
                )
                rel_id += 1

        # Rule 4: Components flow/compose with each other
        if len(app_components) >= 2:
            for i in range(len(app_components) - 1):
                relationships.append(
                    {
                        "id": f"rel_{rel_id}",
                        "type": "flow",
                        "source_id": app_components[i]["id"],
                        "target_id": app_components[i + 1]["id"],
                        "description": f"Data flows from {app_components[i]['name']} to {app_components[i + 1]['name']}",
                    }
                )
                rel_id += 1

        return relationships

    def list_available_templates(self) -> List[Dict]:
        """
        Get list of available templates.

        Returns:
            List of template summaries
        """
        return [
            {
                "name": name,
                "title": data["name"],
                "description": data["description"],
                "keywords": data["keywords"],
            }
            for name, data in self.TEMPLATES.items()
        ]
