"""
-> app.modules.architecture.services.layer_service

Unified ArchiMate Layer Services Module

Consolidates all ArchiMate layer-specific services into a single, maintainable module.
This includes Application, Business, and Technology layer services for ArchiMate 3.2 modeling.

Services consolidated:
- ApplicationLayerService: Application layer modeling (components, services, data objects, interfaces)
- BusinessLayerService: Business layer modeling (processes, actors, roles, services, objects)
- TechnologyLayerService: Technology layer modeling (nodes, devices, system software, services, networks)

Original files:
- app/services/archimate/application_layer_service.py (~745 lines)
- app/services/archimate/business_layer_service.py (~707 lines)
- app/services/archimate/technology_layer_service.py (~1002 lines)

This consolidation maintains 100% backward compatibility while reducing code fragmentation.
All original classes and methods are preserved exactly as they were.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
from app.services.llm_service import LLMService
from app.models.archimate_element_types import is_application_kind

logger = logging.getLogger(__name__)


# ============================================================================
# Application Layer Service
# ============================================================================


class ApplicationLayerService:
    """
    AI-powered service for ArchiMate 3.2 Application Layer modeling.

    Capabilities:
    - Identify application components from portfolio
    - Map application services
    - Model data objects and flows
    - Create application interfaces
    - Link applications to business processes
    - Analyze technology dependencies
    """

    def __init__(self):
        self.llm_service = LLMService()

    # ========================================================================
    # Application Component Methods
    # ========================================================================

    def identify_application_components(
        self, application_portfolio: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Identify application components from application portfolio description.

        ApplicationComponent: Encapsulation of application functionality
        (SAP ERP, Salesforce CRM, Custom Inventory System)

        Args:
            application_portfolio: Description of application landscape
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of ApplicationComponent ArchiMateElements

        Example:
            >>> portfolio = '''
            ... - SAP ERP for finance and procurement
            ... - Salesforce for CRM
            ... - Custom inventory management system (Java)
            ... - PowerBI for reporting
            ... '''
            >>> components = service.identify_application_components(portfolio, 1)
        """
        prompt = self._build_component_identification_prompt(application_portfolio)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            components_data = json.loads(response)

            components = []
            for comp_info in components_data.get("components", []):
                component = self._create_application_element(
                    comp_info, architecture_id, element_type="ApplicationComponent"
                )
                components.append(component)

            db.session.commit()
            return components

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Application component identification failed: {str(e)}")

    def analyze_application_dependencies(
        self, component_id: int, technical_context: Optional[str] = None
    ) -> Dict:
        """
        Analyze dependencies for an application component.

        Args:
            component_id: ID of the ApplicationComponent
            technical_context: Optional technical context

        Returns:
            Dict with dependency analysis:
            {
                'upstream_dependencies': [...],  # Apps this depends on
                'downstream_dependents': [...],  # Apps depending on this
                'data_dependencies': [...],
                'integration_patterns': [...]
            }
        """
        component = db.session.get(ArchiMateElement, component_id)
        if not component or not is_application_kind(component.type):
            raise ValueError(f"ApplicationComponent {component_id} not found")

        prompt = self._build_dependency_analysis_prompt(component, technical_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            dependency_data = json.loads(response)

            # Store in component properties
            props = json.loads(component.properties) if component.properties else {}
            props["dependencies"] = dependency_data
            props["analyzed_at"] = datetime.utcnow().isoformat()
            component.properties = json.dumps(props)

            db.session.commit()

            return dependency_data

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Dependency analysis failed: {str(e)}")

    # ========================================================================
    # Application Service Methods
    # ========================================================================

    def identify_application_services(
        self, component_id: int, api_documentation: Optional[str] = None
    ) -> List[ArchiMateElement]:
        """
        Identify application services exposed by a component.

        ApplicationService: Explicitly defined exposed behavior
        (REST API, SOAP service, GraphQL endpoint)

        Args:
            component_id: ID of the ApplicationComponent
            api_documentation: Optional API documentation

        Returns:
            List of ApplicationService ArchiMateElements
        """
        component = db.session.get(ArchiMateElement, component_id)
        if not component or not is_application_kind(component.type):
            raise ValueError(f"ApplicationComponent {component_id} not found")

        prompt = self._build_service_identification_prompt(component, api_documentation)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            services_data = json.loads(response)

            services = []
            for service_info in services_data.get("services", []):
                service = self._create_application_element(
                    service_info, component.architecture_id, element_type="ApplicationService"
                )

                # Component realizes Service
                relationship = ArchiMateRelationship(
                    type="realization",
                    source_id=component_id,
                    target_id=service.id,
                    architecture_id=component.architecture_id,
                )
                db.session.add(relationship)

                services.append(service)

            db.session.commit()
            return services

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Application service identification failed: {str(e)}")

    # ========================================================================
    # Data Object Methods
    # ========================================================================

    def extract_data_objects(
        self, data_model_description: str, architecture_id: int, component_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """
        Extract data objects from data model description.

        DataObject: Data structured for automated processing
        (Customer record, Order, Product catalog)

        Args:
            data_model_description: Description of data model
            architecture_id: ID of the ArchitectureModel
            component_id: Optional ApplicationComponent that owns the data

        Returns:
            List of DataObject ArchiMateElements
        """
        prompt = self._build_data_object_prompt(data_model_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            data_objects_data = json.loads(response)

            data_objects = []
            for obj_info in data_objects_data.get("data_objects", []):
                data_obj = self._create_application_element(
                    obj_info, architecture_id, element_type="DataObject"
                )

                # If component specified, create composition relationship
                if component_id:
                    relationship = ArchiMateRelationship(
                        type="composition",
                        source_id=component_id,
                        target_id=data_obj.id,
                        architecture_id=architecture_id,
                    )
                    db.session.add(relationship)

                data_objects.append(data_obj)

            db.session.commit()
            return data_objects

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Data object extraction failed: {str(e)}")

    def model_data_flow(
        self, source_component_id: int, target_component_id: int, data_object_ids: List[int]
    ) -> List[ArchiMateRelationship]:
        """
        Model data flow between application components.

        Args:
            source_component_id: Source ApplicationComponent
            target_component_id: Target ApplicationComponent
            data_object_ids: List of DataObject IDs being transferred

        Returns:
            List of flow relationships
        """
        source = db.session.get(ArchiMateElement, source_component_id)
        target = db.session.get(ArchiMateElement, target_component_id)

        if not source or not target:
            raise ValueError("Invalid source or target component")

        relationships = []

        # Create flow relationship
        flow_rel = ArchiMateRelationship(
            type="flow",
            source_id=source_component_id,
            target_id=target_component_id,
            architecture_id=source.architecture_id,
        )
        db.session.add(flow_rel)
        relationships.append(flow_rel)

        # Link data objects to flow (via properties)
        ({
            "data_objects": data_object_ids,
            "flow_type": "data_transfer",
            "created_at": datetime.utcnow().isoformat(),
        })

        # Store data flow metadata
        for data_obj_id in data_object_ids:
            data_obj = db.session.get(ArchiMateElement, data_obj_id)
            if data_obj and data_obj.type == "DataObject":
                # Create access relationship (component accesses data)
                access_rel = ArchiMateRelationship(
                    type="access",
                    source_id=source_component_id,
                    target_id=data_obj_id,
                    architecture_id=source.architecture_id,
                )
                db.session.add(access_rel)
                relationships.append(access_rel)

        db.session.commit()
        return relationships

    # ========================================================================
    # Application Interface Methods
    # ========================================================================

    def define_application_interface(
        self, component_id: int, interface_spec: str
    ) -> ArchiMateElement:
        """
        Define an application interface (API, UI, integration point).

        ApplicationInterface: Point of access to application services

        Args:
            component_id: ID of the ApplicationComponent
            interface_spec: Interface specification (OpenAPI, etc.)

        Returns:
            ApplicationInterface ArchiMateElement
        """
        component = db.session.get(ArchiMateElement, component_id)
        if not component or not is_application_kind(component.type):
            raise ValueError(f"ApplicationComponent {component_id} not found")

        prompt = self._build_interface_definition_prompt(component, interface_spec)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            interface_data = json.loads(response)

            interface = self._create_application_element(
                interface_data, component.architecture_id, element_type="ApplicationInterface"
            )

            # Component assigned to Interface
            relationship = ArchiMateRelationship(
                type="assignment",
                source_id=component_id,
                target_id=interface.id,
                architecture_id=component.architecture_id,
            )
            db.session.add(relationship)

            db.session.commit()
            return interface

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Interface definition failed: {str(e)}")

    # ========================================================================
    # Business-Application Mapping Methods
    # ========================================================================

    def map_application_to_business_process(
        self, component_id: int, process_id: int, usage_description: Optional[str] = None
    ) -> ArchiMateRelationship:
        """
        Map application component to business process it supports.

        Args:
            component_id: ID of the ApplicationComponent
            process_id: ID of the BusinessProcess
            usage_description: Optional description of how app supports process

        Returns:
            ArchiMateRelationship (serving)
        """
        component = db.session.get(ArchiMateElement, component_id)
        process = db.session.get(ArchiMateElement, process_id)

        if not component or not is_application_kind(component.type):
            raise ValueError(f"ApplicationComponent {component_id} not found")
        if not process or process.type != "BusinessProcess":
            raise ValueError(f"BusinessProcess {process_id} not found")

        # Application serves Business Process
        relationship = ArchiMateRelationship(
            type="serving",
            source_id=component_id,
            target_id=process_id,
            architecture_id=component.architecture_id,
        )

        if usage_description:
            props = {"usage": usage_description}
            relationship.properties = json.dumps(props)

        db.session.add(relationship)
        db.session.commit()

        return relationship

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _create_application_element(
        self, element_info: Dict, architecture_id: int, element_type: str
    ) -> ArchiMateElement:
        """Create Application Layer ArchiMateElement."""
        properties = element_info.get("properties", {})
        properties["created_at"] = datetime.utcnow().isoformat()

        element = ArchiMateElement(
            name=element_info["name"],
            type=element_type,
            layer="application",
            description=element_info.get("description", ""),
            documentation=element_info.get("documentation", ""),
            properties=json.dumps(properties),
            architecture_id=architecture_id,
        )

        db.session.add(element)
        db.session.flush()
        return element

    # ========================================================================
    # Prompt Building Methods
    # ========================================================================

    def _build_component_identification_prompt(self, application_portfolio: str) -> str:
        """Build application component identification prompt."""
        return f"""Identify APPLICATION COMPONENTS from this application portfolio.

Application Portfolio:
{application_portfolio}

An Application Component is a modular, deployable software system:
- Commercial: SAP ERP, Salesforce, Oracle Database, ServiceNow
- Custom: Inventory Management System, Customer Portal
- SaaS: Microsoft 365, AWS Services, Google Workspace

For each component:
- name: Application name
- description: What it does
- vendor: Vendor/developer
- version: Version if known
- technology: Tech stack (Java, .NET, SaaS, etc.)
- deployment: on-premises | cloud | hybrid
- lifecycle_status: production | development | sunset | decommissioned

Return JSON:
{{
  "components": [
    {{
      "name": "SAP ERP",
      "description": "Enterprise resource planning system for finance, procurement, and operations",
      "vendor": "SAP",
      "version": "S/4HANA 2021",
      "technology": "SAP ABAP, Fiori UI",
      "deployment": "on-premises",
      "lifecycle_status": "production",
      "properties": {{
        "criticality": "critical",
        "users": "500 concurrent",
        "modules": ["FI", "CO", "MM", "SD"]
      }}
    }},
    {{
      "name": "Salesforce CRM",
      "description": "Customer relationship management platform",
      "vendor": "Salesforce",
      "version": "Lightning",
      "technology": "SaaS, Apex, Lightning Web Components",
      "deployment": "cloud",
      "lifecycle_status": "production",
      "properties": {{
        "criticality": "high",
        "users": "200 licenses"
      }}
    }}
  ]
}}
"""

    def _build_dependency_analysis_prompt(
        self, component: ArchiMateElement, technical_context: Optional[str]
    ) -> str:
        """Build dependency analysis prompt."""
        context_section = (
            f"\n\nTechnical Context:\n{technical_context}" if technical_context else ""
        )

        return f"""Analyze dependencies for this application component.

Component: {component.name}
Description: {component.description}
{context_section}

Identify:
1. **Upstream Dependencies**: Applications this component depends on
2. **Downstream Dependents**: Applications depending on this component
3. **Data Dependencies**: Databases, data stores, data flows
4. **Integration Patterns**: How integrations work (REST API, MQ, file transfer, etc.)

Return JSON:
{{
  "upstream_dependencies": [
    {{
      "component": "Customer Master Database",
      "dependency_type": "data_read",
      "protocol": "JDBC",
      "criticality": "high",
      "failure_impact": "Cannot process orders without customer data"
    }}
  ],
  "downstream_dependents": [
    {{
      "component": "Reporting Dashboard",
      "usage": "Reads order data for analytics",
      "protocol": "REST API",
      "frequency": "real-time"
    }}
  ],
  "data_dependencies": [
    {{
      "database": "PostgreSQL Order DB",
      "access_pattern": "read-write",
      "data_volume": "1M records"
    }}
  ],
  "integration_patterns": ["REST API", "Message Queue (RabbitMQ)", "Batch file transfer"]
}}
"""

    def _build_service_identification_prompt(
        self, component: ArchiMateElement, api_documentation: Optional[str]
    ) -> str:
        """Build application service identification prompt."""
        api_section = f"\n\nAPI Documentation:\n{api_documentation}" if api_documentation else ""

        return f"""Identify APPLICATION SERVICES exposed by this component.

Component: {component.name}
Description: {component.description}
{api_section}

An Application Service is explicitly defined exposed behavior:
- REST API endpoints
- SOAP web services
- GraphQL APIs
- Message queue interfaces
- Batch processing services

For each service:
- name: Service name
- description: What the service does
- interface_type: REST | SOAP | GraphQL | MQ | Batch | RPC
- endpoint: API endpoint/URL
- operations: Key operations provided
- authentication: Auth method
- sla: Performance/availability SLA

Return JSON:
{{
  "services": [
    {{
      "name": "Customer Order API",
      "description": "RESTful API for creating and managing customer orders",
      "interface_type": "REST",
      "endpoint": "/api/v2/orders",
      "operations": ["POST /orders", "GET /orders/:id", "PUT /orders/:id", "DELETE /orders/:id"],
      "authentication": "OAuth 2.0",
      "sla": "99.9% uptime, <200ms response time p95",
      "properties": {{
        "rate_limit": "1000 req/min",
        "versioning": "URI versioning"
      }}
    }}
  ]
}}
"""

    def _build_data_object_prompt(self, data_model_description: str) -> str:
        """Build data object extraction prompt."""
        return f"""Extract DATA OBJECTS from this data model description.

Data Model:
{data_model_description}

A Data Object is data structured for automated processing:
- Entities: Customer, Order, Product, Invoice
- Documents: PDF invoice, XML message
- Files: CSV export, log file

For each data object:
- name: Data object name
- description: What data it contains
- attributes: Key data fields
- format: JSON | XML | CSV | Binary | etc.
- persistence: transient | persistent
- master_system: System of record

Return JSON:
{{
  "data_objects": [
    {{
      "name": "Customer Order",
      "description": "Customer order with line items, pricing, and shipping details",
      "attributes": ["order_id", "customer_id", "order_date", "total_amount", "status", "line_items[]"],
      "format": "JSON",
      "persistence": "persistent",
      "master_system": "Order Management System",
      "properties": {{
        "schema_version": "2.1",
        "retention_period": "7 years"
      }}
    }}
  ]
}}
"""

    def _build_interface_definition_prompt(
        self, component: ArchiMateElement, interface_spec: str
    ) -> str:
        """Build application interface definition prompt."""
        return f"""Define an APPLICATION INTERFACE for this component.

Component: {component.name}
Description: {component.description}

Interface Specification:
{interface_spec}

Provide:
- name: Interface name
- description: Interface purpose
- interface_type: API | UI | Integration | Batch
- protocol: HTTP/REST | SOAP | gRPC | WebSocket | FTP | etc.
- authentication: Auth mechanism
- data_format: JSON | XML | CSV | Binary

Return JSON:
{{
  "name": "Order Management REST API",
  "description": "RESTful interface for order operations",
  "interface_type": "API",
  "protocol": "HTTP/REST",
  "authentication": "OAuth 2.0 + API Key",
  "data_format": "JSON",
  "properties": {{
    "base_url": "https://api.example.com/v2",
    "documentation_url": "https://docs.example.com/api",
    "openapi_spec": "3.0"
  }}
}}
"""

    # ========================================================================
    # Business Capability Identification (Extended for Smart Import)
    # ========================================================================

    def identify_business_capabilities(
        self, application_name: str, description: str, business_domain: str
    ) -> List[Dict]:
        """
        Identify standard Business Capabilities based on application context.
        Used for Smart Import auto-discovery.

        Args:
            application_name: Name of the application
            description: Application description
            business_domain: Business domain (e.g., "HR", "Finance")

        Returns:
            List of capability names/descriptions
        """
        prompt = self._build_capability_identification_prompt(
            application_name, description, business_domain
        )

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            data = json.loads(response)
            return data.get("capabilities", [])
        except Exception as e:
            # Log warning but return empty list to not block import
            logger.warning(f"Capability identification failed: {str(e)}")
            return []

    def _build_capability_identification_prompt(
        self, app_name: str, description: str, domain: str
    ) -> str:
        return f"""Identify standard BUSINESS CAPABILITIES for this application.

Application: {app_name}
Domain: {domain}
Description: {description}

Based on the domain and description, suggest standard Business Capabilities (e.g., from APQC PCF or similar frameworks) that this application likely supports.

Return JSON:
{{
  "capabilities": [
    {{
      "name": "General Ledger Management",
      "description": "Managing financial records",
      "confidence": "high"
    }}
  ]
}}
"""


# ============================================================================
# Business Layer Service
# ============================================================================


class BusinessLayerService:
    """
    AI-powered service for ArchiMate 3.2 Business Layer modeling.

    Capabilities:
    - Extract business processes from descriptions
    - Identify business actors and roles
    - Model business services
    - Generate process flows
    - Map business objects
    - Create realization relationships
    """

    def __init__(self):
        self.llm_service = LLMService()

    # ========================================================================
    # Business Process Methods
    # ========================================================================

    def extract_business_processes(
        self, business_description: str, architecture_id: int, capability_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """
        Extract business processes from business description using AI.

        A Business Process is a sequence of business behaviors that produces
        a specific service or product.

        Args:
            business_description: Text describing business operations
            architecture_id: ID of the ArchitectureModel
            capability_id: Optional BusinessCapability this process supports

        Returns:
            List of BusinessProcess ArchiMateElements

        Example:
            >>> desc = '''
            ... Order-to-Cash process: Customer places order, credit check,
            ... inventory check, order fulfillment, shipping, invoicing, payment.
            ... '''
            >>> processes = service.extract_business_processes(desc, arch_id=1)
        """
        prompt = self._build_process_extraction_prompt(business_description, capability_id)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            processes_data = json.loads(response)

            processes = []
            for process_info in processes_data.get("processes", []):
                process = self._create_business_element(
                    process_info, architecture_id, element_type="BusinessProcess"
                )
                processes.append(process)

            db.session.commit()
            return processes

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Business process extraction failed: {str(e)}")

    def model_process_flow(
        self, process_id: int, detailed_description: Optional[str] = None
    ) -> Dict:
        """
        AI-powered process flow modeling with steps and sequence.

        Args:
            process_id: ID of the BusinessProcess ArchiMateElement
            detailed_description: Optional detailed process description

        Returns:
            Dict with process flow:
            {
                'process_steps': [
                    {'step_name': '...', 'sequence': 1, 'actor': '...', 'duration': '...'},
                    ...
                ],
                'decision_points': [...],
                'exception_paths': [...],
                'inputs': [...],
                'outputs': [...]
            }
        """
        process = db.session.get(ArchiMateElement, process_id)
        if not process or process.type != "BusinessProcess":
            raise ValueError(f"BusinessProcess {process_id} not found")

        prompt = self._build_process_flow_prompt(process, detailed_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            flow_data = json.loads(response)

            # Store flow in process properties
            props = json.loads(process.properties) if process.properties else {}
            props["process_flow"] = flow_data
            props["flow_modeled_at"] = datetime.utcnow().isoformat()
            process.properties = json.dumps(props)

            db.session.commit()

            return flow_data

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Process flow modeling failed: {str(e)}")

    # ========================================================================
    # Business Actor/Role Methods
    # ========================================================================

    def identify_business_actors(
        self, business_context: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Identify business actors from business context.

        BusinessActor: Organizational entity capable of performing behavior
        (person, department, organization)

        Args:
            business_context: Business context description
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of BusinessActor ArchiMateElements
        """
        prompt = self._build_actor_identification_prompt(business_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            actors_data = json.loads(response)

            actors = []
            for actor_info in actors_data.get("actors", []):
                actor = self._create_business_element(
                    actor_info, architecture_id, element_type="BusinessActor"
                )
                actors.append(actor)

            db.session.commit()
            return actors

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Business actor identification failed: {str(e)}")

    def define_business_roles(
        self, actor_id: int, business_context: Optional[str] = None
    ) -> List[ArchiMateElement]:
        """
        Define business roles for an actor.

        BusinessRole: Responsibility assigned to one or more actors

        Args:
            actor_id: ID of the BusinessActor ArchiMateElement
            business_context: Optional business context

        Returns:
            List of BusinessRole ArchiMateElements assigned to actor
        """
        actor = db.session.get(ArchiMateElement, actor_id)
        if not actor or actor.type != "BusinessActor":
            raise ValueError(f"BusinessActor {actor_id} not found")

        prompt = self._build_role_definition_prompt(actor, business_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            roles_data = json.loads(response)

            roles = []
            for role_info in roles_data.get("roles", []):
                role = self._create_business_element(
                    role_info, actor.architecture_id, element_type="BusinessRole"
                )

                # Create assignment relationship (Actor assigned to Role)
                relationship = ArchiMateRelationship(
                    type="assignment",
                    source_id=actor_id,
                    target_id=role.id,
                    architecture_id=actor.architecture_id,
                )
                db.session.add(relationship)

                roles.append(role)

            db.session.commit()
            return roles

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Business role definition failed: {str(e)}")

    # ========================================================================
    # Business Service Methods
    # ========================================================================

    def identify_business_services(
        self, business_context: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Identify business services offered by the organization.

        BusinessService: Explicitly defined behavior that a business
        exposes to its environment

        Args:
            business_context: Business context description
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of BusinessService ArchiMateElements
        """
        prompt = self._build_service_identification_prompt(business_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            services_data = json.loads(response)

            services = []
            for service_info in services_data.get("services", []):
                service = self._create_business_element(
                    service_info, architecture_id, element_type="BusinessService"
                )
                services.append(service)

            db.session.commit()
            return services

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Business service identification failed: {str(e)}")

    def map_service_realization(
        self, service_id: int, process_ids: Optional[List[int]] = None
    ) -> List[ArchiMateRelationship]:
        """
        Map which business processes realize a business service.

        Args:
            service_id: ID of the BusinessService ArchiMateElement
            process_ids: Optional list of BusinessProcess IDs (if None, uses AI)

        Returns:
            List of realization relationships
        """
        service = db.session.get(ArchiMateElement, service_id)
        if not service or service.type != "BusinessService":
            raise ValueError(f"BusinessService {service_id} not found")

        if not process_ids:
            # Use AI to find processes that realize this service
            process_ids = self._find_realizing_processes(service)

        relationships = []
        for process_id in process_ids:
            process = db.session.get(ArchiMateElement, process_id)
            if process and process.type == "BusinessProcess":
                # Process realizes Service
                relationship = ArchiMateRelationship(
                    type="realization",
                    source_id=process_id,
                    target_id=service_id,
                    architecture_id=service.architecture_id,
                )
                db.session.add(relationship)
                relationships.append(relationship)

        db.session.commit()
        return relationships

    # ========================================================================
    # Business Object Methods
    # ========================================================================

    def extract_business_objects(
        self, business_context: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Extract business objects (data/information entities) from context.

        BusinessObject: Concept used within business domain
        (Order, Customer, Product, Invoice, etc.)

        Args:
            business_context: Business context description
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of BusinessObject ArchiMateElements
        """
        prompt = self._build_business_object_prompt(business_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            objects_data = json.loads(response)

            business_objects = []
            for obj_info in objects_data.get("business_objects", []):
                obj = self._create_business_element(
                    obj_info, architecture_id, element_type="BusinessObject"
                )
                business_objects.append(obj)

            db.session.commit()
            return business_objects

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Business object extraction failed: {str(e)}")

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _create_business_element(
        self, element_info: Dict, architecture_id: int, element_type: str
    ) -> ArchiMateElement:
        """Create Business Layer ArchiMateElement."""
        properties = element_info.get("properties", {})
        properties["created_at"] = datetime.utcnow().isoformat()

        element = ArchiMateElement(
            name=element_info["name"],
            type=element_type,
            layer="business",
            description=element_info.get("description", ""),
            documentation=element_info.get("documentation", ""),
            properties=json.dumps(properties),
            architecture_id=architecture_id,
        )

        db.session.add(element)
        db.session.flush()
        return element

    def _find_realizing_processes(self, service: ArchiMateElement) -> List[int]:
        """Use AI to find processes that realize a service."""
        processes = ArchiMateElement.query.filter_by(
            architecture_id=service.architecture_id, type="BusinessProcess", layer="business"
        ).all()

        if not processes:
            return []

        prompt = f"""Identify which business processes realize this business service:

Service: {service.name}
Description: {service.description}

Available Processes:
{json.dumps([{'id': p.id, 'name': p.name, 'description': p.description} for p in processes], indent=2)}

Return JSON with process IDs that realize the service:
{{"process_ids": [1, 3, 7]}}
"""

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            data = json.loads(response)
            return data.get("process_ids", [])
        except Exception:
            logger.debug("Failed to identify relevant processes", exc_info=True)
            return []

    # ========================================================================
    # Prompt Building Methods
    # ========================================================================

    def _build_process_extraction_prompt(
        self, business_description: str, capability_id: Optional[int]
    ) -> str:
        """Build business process extraction prompt."""
        capability_section = ""
        if capability_id:
            from app.models import BusinessCapability

            capability = db.session.get(BusinessCapability, capability_id)
            if capability:
                capability_section = (
                    f"\n\nCapability: {capability.name}\nDescription: {capability.description}"
                )

        return f"""You are a business process analyst. Extract BUSINESS PROCESSES from this description.

Business Description:
{business_description}
{capability_section}

A Business Process is a sequence of business behaviors that achieves a specific result.

Common process patterns:
- Order-to-Cash: Quote → Order → Fulfillment → Invoice → Payment
- Procure-to-Pay: Requisition → PO → Receipt → Invoice → Payment
- Hire-to-Retire: Recruit → Hire → Onboard → Develop → Offboard
- Lead-to-Cash: Lead → Qualify → Opportunity → Quote → Close

For each process:
- name: Process name (e.g., "Order-to-Cash", "Customer Onboarding")
- description: What the process does
- trigger: What starts the process
- outcome: What the process produces
- owner: Who owns the process (role/department)
- frequency: How often (per day, per month, ad-hoc)
- duration: Typical duration (minutes, hours, days)

Return JSON:
{{
  "processes": [
    {{
      "name": "Order-to-Cash",
      "description": "Complete process from customer order placement through payment receipt",
      "trigger": "Customer places order",
      "outcome": "Payment received, order fulfilled",
      "owner": "Sales Operations",
      "frequency": "100 orders per day",
      "duration": "3 - 5 days average",
      "properties": {{
        "complexity": "high",
        "automation_level": "partial",
        "sla": "95% within 5 days"
      }}
    }}
  ]
}}

Extract 3 - 8 key business processes.
"""

    def _build_process_flow_prompt(
        self, process: ArchiMateElement, detailed_description: Optional[str]
    ) -> str:
        """Build process flow modeling prompt."""
        detail_section = (
            f"\n\nDetailed Description:\n{detailed_description}" if detailed_description else ""
        )

        return f"""Model the detailed flow for this business process.

Process: {process.name}
Description: {process.description}
{detail_section}

Provide:
1. **Process Steps**: Sequential steps with actors and durations
2. **Decision Points**: Where process branches based on conditions
3. **Exception Paths**: What happens when things go wrong
4. **Inputs**: What data/materials enter the process
5. **Outputs**: What the process produces

Return JSON:
{{
  "process_steps": [
    {{
      "step_name": "Receive Customer Order",
      "sequence": 1,
      "actor": "Sales Representative",
      "duration": "15 minutes",
      "description": "Enter order details into system"
    }},
    {{
      "step_name": "Credit Check",
      "sequence": 2,
      "actor": "Finance System",
      "duration": "5 minutes",
      "description": "Automated credit score validation"
    }}
  ],
  "decision_points": [
    {{
      "step": 2,
      "decision": "Credit approved?",
      "if_yes": "Continue to inventory check",
      "if_no": "Manual credit review by Finance"
    }}
  ],
  "exception_paths": [
    {{
      "exception": "Product out of stock",
      "handling": "Offer alternative product or backorder"
    }}
  ],
  "inputs": ["Customer order", "Product catalog", "Pricing rules"],
  "outputs": ["Confirmed order", "Invoice", "Shipment tracking"]
}}
"""

    def _build_actor_identification_prompt(self, business_context: str) -> str:
        """Build business actor identification prompt."""
        return f"""Identify BUSINESS ACTORS from this business context.

Business Context:
{business_context}

A Business Actor is an organizational entity capable of performing behavior:
- Individual: "Customer Service Agent", "Procurement Manager"
- Department: "Finance Department", "IT Operations"
- Organization: "Partner Company", "Vendor", "Customer"

For each actor:
- name: Actor name
- description: What/who they are
- actor_type: individual | department | organization | external
- responsibilities: What they're responsible for

Return JSON:
{{
  "actors": [
    {{
      "name": "Customer Service Agent",
      "description": "Front-line employee handling customer inquiries and issues",
      "actor_type": "individual",
      "responsibilities": "Handle customer calls, resolve issues, escalate complex cases",
      "properties": {{
        "count": "50 agents",
        "location": "Call center"
      }}
    }},
    {{
      "name": "Finance Department",
      "description": "Department responsible for financial operations and compliance",
      "actor_type": "department",
      "responsibilities": "Invoice processing, payment authorization, financial reporting"
    }}
  ]
}}
"""

    def _build_role_definition_prompt(
        self, actor: ArchiMateElement, business_context: Optional[str]
    ) -> str:
        """Build business role definition prompt."""
        context_section = f"\n\nBusiness Context:\n{business_context}" if business_context else ""

        return f"""Define BUSINESS ROLES for this actor.

Actor: {actor.name}
Description: {actor.description}
{context_section}

A Business Role is a named responsibility assigned to an actor.
Actors can have multiple roles.

For each role:
- name: Role name
- description: Role responsibilities
- authority: Decision-making authority
- accountability: What they're accountable for

Return JSON:
{{
  "roles": [
    {{
      "name": "Order Approver",
      "description": "Authority to approve customer orders within credit limits",
      "authority": "Approve orders up to $50K",
      "accountability": "Ensuring orders meet credit and compliance requirements"
    }}
  ]
}}
"""

    def _build_service_identification_prompt(self, business_context: str) -> str:
        """Build business service identification prompt."""
        return f"""Identify BUSINESS SERVICES offered by the organization.

Business Context:
{business_context}

A Business Service is an explicitly defined behavior that business exposes:
- External: Services to customers/partners
- Internal: Services between departments

For each service:
- name: Service name
- description: What the service provides
- service_type: external | internal
- consumers: Who uses the service
- sla: Service level expectations

Return JSON:
{{
  "services": [
    {{
      "name": "Customer Order Processing",
      "description": "Service to accept and process customer orders",
      "service_type": "external",
      "consumers": "Customers, Sales channels",
      "sla": "Order confirmation within 1 hour, 99% uptime",
      "properties": {{
        "pricing": "Volume-based",
        "availability": "24/7"
      }}
    }}
  ]
}}
"""

    def _build_business_object_prompt(self, business_context: str) -> str:
        """Build business object extraction prompt."""
        return f"""Extract BUSINESS OBJECTS (data/information entities) from context.

Business Context:
{business_context}

Business Objects are concepts used in business domain:
- Order, Quote, Invoice, Payment
- Customer, Product, Contract
- Policy, Claim, Application

For each object:
- name: Object name (singular, e.g., "Customer Order")
- description: What it represents
- attributes: Key data attributes
- lifecycle: States it goes through

Return JSON:
{{
  "business_objects": [
    {{
      "name": "Customer Order",
      "description": "A customer's request to purchase products/services",
      "attributes": ["Order ID", "Customer ID", "Order Date", "Total Amount", "Status"],
      "lifecycle": ["Draft", "Submitted", "Approved", "Fulfilled", "Invoiced", "Paid", "Closed"],
      "properties": {{
        "retention_period": "7 years",
        "master_system": "ERP"
      }}
    }}
  ]
}}
"""


# ============================================================================
# Technology Layer Service
# ============================================================================


class TechnologyLayerService:
    """
    AI-powered service for ArchiMate 3.2 Technology Layer modeling.

    Capabilities:
    - Identify infrastructure nodes (physical/virtual servers, containers)
    - Map technology services (databases, middleware, OS services)
    - Model deployment architecture
    - Analyze technology dependencies and constraints
    - Create infrastructure-application mappings
    - Model network topology
    """

    def __init__(self):
        self.llm_service = LLMService()

    # ========================================================================
    # Node Methods (Servers, VMs, Containers)
    # ========================================================================

    def identify_infrastructure_nodes(
        self, infrastructure_description: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Identify infrastructure nodes from infrastructure description.

        Node: Computational or physical resource hosting applications
        (Physical server, VM, Docker container, Kubernetes pod, cluster)

        Args:
            infrastructure_description: Description of infrastructure landscape
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of Node ArchiMateElements

        Example:
            >>> infra = '''
            ... - 10 Dell PowerEdge R740 physical servers in primary datacenter
            ... - VMware vSphere cluster with 50 VMs
            ... - Kubernetes cluster (5 nodes) for microservices
            ... - AWS EC2 instances for dev/test environments
            ... '''
            >>> nodes = service.identify_infrastructure_nodes(infra, 1)
        """
        prompt = self._build_node_identification_prompt(infrastructure_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            nodes_data = json.loads(response)

            nodes = []
            for node_info in nodes_data.get("nodes", []):
                node = self._create_technology_element(node_info, architecture_id, element_type="Node")
                nodes.append(node)

            db.session.commit()
            return nodes

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Infrastructure node identification failed: {str(e)}")

    def model_deployment_architecture(
        self,
        application_component_id: int,
        node_ids: List[int],
        deployment_description: Optional[str] = None,
    ) -> List[ArchiMateRelationship]:
        """
        Map how applications deploy to infrastructure nodes.

        Args:
            application_component_id: ID of the ApplicationComponent
            node_ids: List of Node IDs where app is deployed
            deployment_description: Optional deployment details

        Returns:
            List of assignment relationships (Node assigned to ApplicationComponent)
        """
        app_component = db.session.get(ArchiMateElement, application_component_id)
        if not app_component or not is_application_kind(app_component.type):
            raise ValueError(f"ApplicationComponent {application_component_id} not found")

        relationships = []

        for node_id in node_ids:
            node = db.session.get(ArchiMateElement, node_id)
            if not node or node.type != "Node":
                continue

            # Node assigned to ApplicationComponent (node hosts the app)
            relationship = ArchiMateRelationship(
                type="assignment",
                source_id=node_id,
                target_id=application_component_id,
                architecture_id=app_component.architecture_id,
            )

            if deployment_description:
                props = {"deployment_details": deployment_description}
                relationship.properties = json.dumps(props)

            db.session.add(relationship)
            relationships.append(relationship)

        db.session.commit()
        return relationships

    # ========================================================================
    # Device Methods (Physical Hardware)
    # ========================================================================

    def identify_devices(
        self, device_inventory: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """
        Identify physical devices from device inventory.

        Device: Physical IT resource (laptop, router, firewall, sensor, mobile device)

        Args:
            device_inventory: Description of physical devices
            architecture_id: ID of the ArchitectureModel

        Returns:
            List of Device ArchiMateElements
        """
        prompt = self._build_device_identification_prompt(device_inventory)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            devices_data = json.loads(response)

            devices = []
            for device_info in devices_data.get("devices", []):
                device = self._create_technology_element(
                    device_info, architecture_id, element_type="Device"
                )
                devices.append(device)

            db.session.commit()
            return devices

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Device identification failed: {str(e)}")

    # ========================================================================
    # System Software Methods (OS, Database, Middleware)
    # ========================================================================

    def identify_system_software(
        self, software_inventory: str, architecture_id: int, node_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """
        Identify system software from software inventory.

        SystemSoftware: Software environment for components
        (Operating System, Database engine, Web server, Application server, Middleware)

        Args:
            software_inventory: Description of system software
            architecture_id: ID of the ArchitectureModel
            node_id: Optional Node ID where software runs

        Returns:
            List of SystemSoftware ArchiMateElements
        """
        prompt = self._build_system_software_prompt(software_inventory)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            software_data = json.loads(response)

            software_list = []
            for sw_info in software_data.get("system_software", []):
                sw = self._create_technology_element(
                    sw_info, architecture_id, element_type="SystemSoftware"
                )

                # If node specified, create assignment relationship
                if node_id:
                    node = db.session.get(ArchiMateElement, node_id)
                    if node and node.type == "Node":
                        # Node assigned to SystemSoftware (node runs the OS/database)
                        relationship = ArchiMateRelationship(
                            type="assignment",
                            source_id=node_id,
                            target_id=sw.id,
                            architecture_id=architecture_id,
                        )
                        db.session.add(relationship)

                software_list.append(sw)

            db.session.commit()
            return software_list

        except Exception as e:
            db.session.rollback()
            raise Exception(f"System software identification failed: {str(e)}")

    # ========================================================================
    # Technology Service Methods
    # ========================================================================

    def identify_technology_services(
        self, system_software_id: int, service_description: Optional[str] = None
    ) -> List[ArchiMateElement]:
        """
        Identify technology services provided by system software.

        TechnologyService: Explicitly defined exposed technology behavior
        (Database service, File storage service, Authentication service, Messaging service)

        Args:
            system_software_id: ID of the SystemSoftware
            service_description: Optional service description

        Returns:
            List of TechnologyService ArchiMateElements
        """
        system_software = db.session.get(ArchiMateElement, system_software_id)
        if not system_software or system_software.type != "SystemSoftware":
            raise ValueError(f"SystemSoftware {system_software_id} not found")

        prompt = self._build_technology_service_prompt(system_software, service_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            services_data = json.loads(response)

            services = []
            for service_info in services_data.get("services", []):
                service = self._create_technology_element(
                    service_info, system_software.architecture_id, element_type="TechnologyService"
                )

                # SystemSoftware realizes TechnologyService
                relationship = ArchiMateRelationship(
                    type="realization",
                    source_id=system_software_id,
                    target_id=service.id,
                    architecture_id=system_software.architecture_id,
                )
                db.session.add(relationship)

                services.append(service)

            db.session.commit()
            return services

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Technology service identification failed: {str(e)}")

    def map_technology_to_application_service(
        self, technology_service_id: int, application_service_id: int
    ) -> ArchiMateRelationship:
        """
        Map technology service supporting application service.

        Args:
            technology_service_id: ID of the TechnologyService
            application_service_id: ID of the ApplicationService

        Returns:
            ArchiMateRelationship (serving)
        """
        tech_service = db.session.get(ArchiMateElement, technology_service_id)
        app_service = db.session.get(ArchiMateElement, application_service_id)

        if not tech_service or tech_service.type != "TechnologyService":
            raise ValueError(f"TechnologyService {technology_service_id} not found")
        if not app_service or app_service.type != "ApplicationService":
            raise ValueError(f"ApplicationService {application_service_id} not found")

        # TechnologyService serves ApplicationService
        relationship = ArchiMateRelationship(
            type="serving",
            source_id=technology_service_id,
            target_id=application_service_id,
            architecture_id=tech_service.architecture_id,
        )

        db.session.add(relationship)
        db.session.commit()

        return relationship

    # ========================================================================
    # Network & Communication Methods
    # ========================================================================

    def model_network_topology(self, network_description: str, architecture_id: int) -> Dict:
        """
        Model network topology from network description.

        Creates:
        - CommunicationNetwork elements (LAN, WAN, VPN)
        - Path relationships (network links)

        Args:
            network_description: Description of network infrastructure
            architecture_id: ID of the ArchitectureModel

        Returns:
            Dict with networks and paths
        """
        prompt = self._build_network_topology_prompt(network_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            network_data = json.loads(response)

            networks = []
            for net_info in network_data.get("networks", []):
                network = self._create_technology_element(
                    net_info, architecture_id, element_type="CommunicationNetwork"
                )
                networks.append(network)

            paths = []
            for path_info in network_data.get("paths", []):
                # Paths are relationships between nodes
                source_node = self._find_or_create_node(path_info["source"], architecture_id)
                target_node = self._find_or_create_node(path_info["target"], architecture_id)

                path_rel = ArchiMateRelationship(
                    type="flow",
                    source_id=source_node.id,
                    target_id=target_node.id,
                    architecture_id=architecture_id,
                    properties=json.dumps(path_info.get("properties", {})),
                )
                db.session.add(path_rel)
                paths.append(path_rel)

            db.session.commit()

            return {"networks": networks, "paths": paths}

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Network topology modeling failed: {str(e)}")

    # ========================================================================
    # Artifact Methods (Files, Database Tables, Container Images)
    # ========================================================================

    def identify_artifacts(
        self, artifact_description: str, architecture_id: int, node_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """
        Identify artifacts (physical data) from description.

        Artifact: Physical piece of data (database table, file, Docker image, JAR file)

        Args:
            artifact_description: Description of artifacts
            architecture_id: ID of the ArchitectureModel
            node_id: Optional Node ID where artifacts are stored

        Returns:
            List of Artifact ArchiMateElements
        """
        prompt = self._build_artifact_identification_prompt(artifact_description)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            artifacts_data = json.loads(response)

            artifacts = []
            for artifact_info in artifacts_data.get("artifacts", []):
                artifact = self._create_technology_element(
                    artifact_info, architecture_id, element_type="Artifact"
                )

                # If node specified, create assignment relationship
                if node_id:
                    node = db.session.get(ArchiMateElement, node_id)
                    if node and node.type == "Node":
                        # Node assigned to Artifact (node stores the artifact)
                        relationship = ArchiMateRelationship(
                            type="assignment",
                            source_id=node_id,
                            target_id=artifact.id,
                            architecture_id=architecture_id,
                        )
                        db.session.add(relationship)

                artifacts.append(artifact)

            db.session.commit()
            return artifacts

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Artifact identification failed: {str(e)}")

    # ========================================================================
    # Dependency & Constraint Analysis
    # ========================================================================

    def analyze_technology_dependencies(
        self, node_id: int, infrastructure_context: Optional[str] = None
    ) -> Dict:
        """
        Analyze technology dependencies for a node.

        Args:
            node_id: ID of the Node
            infrastructure_context: Optional infrastructure context

        Returns:
            Dict with dependency analysis:
            {
                'dependencies': [...],  # Other nodes/services this depends on
                'dependents': [...],    # Nodes/services depending on this
                'network_dependencies': [...],
                'storage_dependencies': [...]
            }
        """
        node = db.session.get(ArchiMateElement, node_id)
        if not node or node.type != "Node":
            raise ValueError(f"Node {node_id} not found")

        prompt = self._build_technology_dependency_prompt(node, infrastructure_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            dependency_data = json.loads(response)

            # Store in node properties
            props = json.loads(node.properties) if node.properties else {}
            props["dependencies"] = dependency_data
            props["analyzed_at"] = datetime.utcnow().isoformat()
            node.properties = json.dumps(props)

            db.session.commit()

            return dependency_data

        except Exception as e:
            db.session.rollback()
            raise Exception(f"Technology dependency analysis failed: {str(e)}")

    def identify_technology_constraints(
        self, architecture_id: int, infrastructure_context: str
    ) -> Dict:
        """
        Identify technology constraints and limitations.

        Args:
            architecture_id: ID of the ArchitectureModel
            infrastructure_context: Infrastructure context

        Returns:
            Dict with constraints:
            {
                'capacity_constraints': [...],
                'performance_constraints': [...],
                'security_constraints': [...],
                'compatibility_constraints': [...]
            }
        """
        prompt = self._build_technology_constraints_prompt(infrastructure_context)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            constraints_data = json.loads(response)

            return constraints_data

        except Exception as e:
            raise Exception(f"Technology constraint identification failed: {str(e)}")

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def _create_technology_element(
        self, element_info: Dict, architecture_id: int, element_type: str
    ) -> ArchiMateElement:
        """Create Technology Layer ArchiMateElement."""
        properties = element_info.get("properties", {})
        properties["created_at"] = datetime.utcnow().isoformat()

        element = ArchiMateElement(
            name=element_info["name"],
            type=element_type,
            layer="technology",
            description=element_info.get("description", ""),
            documentation=element_info.get("documentation", ""),
            properties=json.dumps(properties),
            architecture_id=architecture_id,
        )

        db.session.add(element)
        db.session.flush()
        return element

    def _find_or_create_node(self, node_name: str, architecture_id: int) -> ArchiMateElement:
        """Find existing node or create new one."""
        # Try to find existing node
        existing = ArchiMateElement.query.filter_by(
            name=node_name, type="Node", architecture_id=architecture_id
        ).first()

        if existing:
            return existing

        # Create new node
        node = ArchiMateElement(
            name=node_name, type="Node", layer="technology", architecture_id=architecture_id
        )
        db.session.add(node)
        db.session.flush()
        return node

    # ========================================================================
    # Prompt Building Methods
    # ========================================================================

    def _build_node_identification_prompt(self, infrastructure_description: str) -> str:
        """Build node identification prompt."""
        return f"""Identify INFRASTRUCTURE NODES from this infrastructure description.

Infrastructure:
{infrastructure_description}

A Node is a computational or physical resource:
- Physical servers: Dell PowerEdge, HP ProLiant, IBM Power Systems
- Virtual machines: VMware VMs, Hyper-V VMs
- Containers: Docker containers, Kubernetes pods
- Cloud instances: AWS EC2, Azure VMs, GCP Compute Engine
- Clusters: Kubernetes cluster, Database cluster, Load balancer cluster

For each node:
- name: Node name/identifier
- description: What it does/hosts
- node_type: physical_server | virtual_machine | container | cloud_instance | cluster
- environment: production | staging | development | test
- location: datacenter/region
- specifications: CPU, RAM, storage
- operating_system: OS name and version

Return JSON:
{{
  "nodes": [
    {{
      "name": "PROD-DB - 01",
      "description": "Primary PostgreSQL database server for production",
      "node_type": "physical_server",
      "environment": "production",
      "location": "Primary Datacenter",
      "specifications": "Dell PowerEdge R740, 64GB RAM, 2TB SSD RAID10",
      "operating_system": "Ubuntu Server 22.04 LTS",
      "properties": {{
        "ip_address": "10.0.1.50",
        "high_availability": true,
        "backup_node": "PROD-DB - 02"
      }}
    }},
    {{
      "name": "K8S-PROD-CLUSTER",
      "description": "Kubernetes cluster for microservices",
      "node_type": "cluster",
      "environment": "production",
      "location": "AWS us-east - 1",
      "specifications": "5 nodes, 20 vCPU each, 64GB RAM each",
      "operating_system": "Amazon Linux 2",
      "properties": {{
        "kubernetes_version": "1.28",
        "node_count": 5,
        "pod_capacity": 500
      }}
    }}
  ]
}}
"""

    def _build_device_identification_prompt(self, device_inventory: str) -> str:
        """Build device identification prompt."""
        return f"""Identify PHYSICAL DEVICES from this device inventory.

Device Inventory:
{device_inventory}

A Device is a physical IT resource:
- Network: Router, Switch, Firewall, Load balancer
- End-user: Laptop, Desktop, Mobile device, Tablet
- IoT: Sensor, Controller, Smart device
- Peripherals: Printer, Scanner, Storage appliance

For each device:
- name: Device name
- description: Device purpose
- device_type: router | switch | firewall | laptop | mobile | iot_sensor | printer | storage
- manufacturer: Vendor
- model: Model number
- location: Physical location

Return JSON:
{{
  "devices": [
    {{
      "name": "Core Router CR - 01",
      "description": "Core network router for datacenter",
      "device_type": "router",
      "manufacturer": "Cisco",
      "model": "Catalyst 9600",
      "location": "Primary Datacenter Rack A1",
      "properties": {{
        "ports": 48,
        "throughput": "10 Gbps",
        "management_ip": "192.168.1.1"
      }}
    }}
  ]
}}
"""

    def _build_system_software_prompt(self, software_inventory: str) -> str:
        """Build system software identification prompt."""
        return f"""Identify SYSTEM SOFTWARE from this software inventory.

Software Inventory:
{software_inventory}

System Software is software providing execution environment:
- Operating Systems: Windows Server, Linux (Ubuntu, RHEL), Unix
- Database engines: PostgreSQL, MySQL, Oracle, MongoDB, Redis
- Web servers: Apache, Nginx, IIS
- Application servers: Tomcat, JBoss, WebLogic, WebSphere
- Middleware: RabbitMQ, Kafka, ActiveMQ, IBM MQ
- Container runtimes: Docker Engine, containerd

For each system software:
- name: Software name
- description: What it provides
- software_category: os | database | web_server | app_server | middleware | container_runtime
- vendor: Vendor/organization
- version: Software version
- license: License type

Return JSON:
{{
  "system_software": [
    {{
      "name": "PostgreSQL 15",
      "description": "Relational database management system",
      "software_category": "database",
      "vendor": "PostgreSQL Global Development Group",
      "version": "15.4",
      "license": "PostgreSQL License (open source)",
      "properties": {{
        "port": 5432,
        "max_connections": 200,
        "shared_buffers": "8GB"
      }}
    }},
    {{
      "name": "Nginx",
      "description": "Web server and reverse proxy",
      "software_category": "web_server",
      "vendor": "NGINX Inc",
      "version": "1.24.0",
      "license": "BSD - 2 - Clause",
      "properties": {{
        "worker_processes": 4,
        "worker_connections": 1024
      }}
    }}
  ]
}}
"""

    def _build_technology_service_prompt(
        self, system_software: ArchiMateElement, service_description: Optional[str]
    ) -> str:
        """Build technology service identification prompt."""
        desc_section = (
            f"\n\nService Description:\n{service_description}" if service_description else ""
        )

        return f"""Identify TECHNOLOGY SERVICES provided by this system software.

System Software: {system_software.name}
Description: {system_software.description}
{desc_section}

A Technology Service is explicitly defined exposed technology behavior:
- Database service (CRUD operations, query service)
- File storage service (read/write/delete)
- Authentication service (LDAP, OAuth)
- Messaging service (pub/sub, queues)
- Caching service (get/set/evict)

For each service:
- name: Service name
- description: What the service does
- service_type: database | storage | messaging | caching | authentication | monitoring
- protocol: Protocol used
- port: Service port
- sla: Performance/availability SLA

Return JSON:
{{
  "services": [
    {{
      "name": "PostgreSQL Query Service",
      "description": "Relational data query and transaction service",
      "service_type": "database",
      "protocol": "PostgreSQL wire protocol",
      "port": 5432,
      "sla": "99.99% uptime, <10ms query latency p95",
      "properties": {{
        "max_query_time": "30s",
        "isolation_level": "READ COMMITTED"
      }}
    }}
  ]
}}
"""

    def _build_network_topology_prompt(self, network_description: str) -> str:
        """Build network topology modeling prompt."""
        return f"""Model NETWORK TOPOLOGY from this network description.

Network Description:
{network_description}

Identify:
1. **Communication Networks**: LAN, WAN, VPN, DMZ, Internet
2. **Paths**: Network connections between nodes (with bandwidth, latency)

Return JSON:
{{
  "networks": [
    {{
      "name": "Corporate LAN",
      "description": "Local area network for corporate offices",
      "network_type": "LAN",
      "properties": {{
        "bandwidth": "10 Gbps",
        "subnet": "10.0.0.0/16",
        "vlan_id": 100
      }}
    }}
  ],
  "paths": [
    {{
      "source": "PROD-WEB - 01",
      "target": "PROD-DB - 01",
      "properties": {{
        "bandwidth": "1 Gbps",
        "latency": "<1ms",
        "protocol": "TCP/IP"
      }}
    }}
  ]
}}
"""

    def _build_artifact_identification_prompt(self, artifact_description: str) -> str:
        """Build artifact identification prompt."""
        return f"""Identify ARTIFACTS from this description.

Artifacts:
{artifact_description}

An Artifact is a physical piece of data:
- Database tables/schemas
- Files (config files, data files, logs)
- Container images (Docker images)
- Executable files (JAR, WAR, EXE, DLL)
- Scripts (shell scripts, SQL scripts)

For each artifact:
- name: Artifact name
- description: What it contains
- artifact_type: database_table | file | container_image | executable | script
- format: File format/structure
- size: Approximate size
- storage_location: Where it's stored

Return JSON:
{{
  "artifacts": [
    {{
      "name": "customers_table",
      "description": "PostgreSQL table storing customer master data",
      "artifact_type": "database_table",
      "format": "PostgreSQL table",
      "size": "10GB (5M rows)",
      "storage_location": "PROD-DB - 01:/var/lib/postgresql/data",
      "properties": {{
        "schema": "public",
        "indexes": ["idx_customer_email", "idx_customer_id"],
        "partitioned": true
      }}
    }}
  ]
}}
"""

    def _build_technology_dependency_prompt(
        self, node: ArchiMateElement, infrastructure_context: Optional[str]
    ) -> str:
        """Build technology dependency analysis prompt."""
        context_section = (
            f"\n\nInfrastructure Context:\n{infrastructure_context}"
            if infrastructure_context
            else ""
        )

        return f"""Analyze TECHNOLOGY DEPENDENCIES for this node.

Node: {node.name}
Description: {node.description}
{context_section}

Identify:
1. **Dependencies**: Infrastructure this node depends on
2. **Dependents**: Infrastructure depending on this node
3. **Network Dependencies**: Network connectivity requirements
4. **Storage Dependencies**: Storage requirements

Return JSON:
{{
  "dependencies": [
    {{
      "node": "Primary Storage Array",
      "dependency_type": "storage",
      "criticality": "critical",
      "failure_impact": "Node cannot boot without storage access"
    }}
  ],
  "dependents": [
    {{
      "node": "Web Application Servers",
      "usage": "Database queries",
      "criticality": "high"
    }}
  ],
  "network_dependencies": [
    {{
      "network": "Corporate LAN",
      "bandwidth_required": "100 Mbps",
      "latency_requirement": "<10ms"
    }}
  ],
  "storage_dependencies": [
    {{
      "storage": "SAN Volume 1",
      "capacity_required": "2TB",
      "iops_required": 10000
    }}
  ]
}}
"""

    def _build_technology_constraints_prompt(self, infrastructure_context: str) -> str:
        """Build technology constraints identification prompt."""
        return f"""Identify TECHNOLOGY CONSTRAINTS from infrastructure context.

Infrastructure Context:
{infrastructure_context}

Identify constraints:
1. **Capacity Constraints**: CPU, memory, storage, network limitations
2. **Performance Constraints**: Latency, throughput, response time limits
3. **Security Constraints**: Network segmentation, access controls, compliance
4. **Compatibility Constraints**: OS versions, software dependencies, integration limits

Return JSON:
{{
  "capacity_constraints": [
    {{
      "resource": "Database server CPU",
      "limit": "80% utilization threshold",
      "current_usage": "65%",
      "headroom": "23% (6 months at current growth)"
    }}
  ],
  "performance_constraints": [
    {{
      "constraint": "API response time SLA",
      "requirement": "<200ms p95",
      "current_performance": "150ms p95",
      "bottleneck": "Database query optimization needed"
    }}
  ],
  "security_constraints": [
    {{
      "constraint": "PCI-DSS network segmentation",
      "requirement": "Cardholder data environment isolated",
      "implementation": "Dedicated VLAN with firewall rules"
    }}
  ],
  "compatibility_constraints": [
    {{
      "constraint": "Java version compatibility",
      "requirement": "Application requires Java 11+",
      "limitation": "Some legacy systems still on Java 8"
    }}
  ]
}}
"""


# ============================================================================
# Unified ArchiMate Layer Services Interface
# ============================================================================


class UnifiedArchiMateLayerServices:
    """
    Single interface to all ArchiMate layer services.

    Provides a unified API for accessing all layer-specific ArchiMate functionality:
    - Application layer modeling (components, services, data objects, interfaces)
    - Business layer modeling (processes, actors, roles, services, objects)
    - Technology layer modeling (nodes, devices, system software, services, networks)

    Usage:
        services = UnifiedArchiMateLayerServices()

        # Application layer
        components = services.identify_application_components(portfolio, arch_id)
        services_list = services.identify_application_services(component_id)

        # Business layer
        processes = services.extract_business_processes(description, arch_id)
        actors = services.identify_business_actors(context, arch_id)

        # Technology layer
        nodes = services.identify_infrastructure_nodes(infra_desc, arch_id)
        devices = services.identify_devices(device_inventory, arch_id)
    """

    def __init__(self):
        """Initialize all layer service instances."""
        self.application_layer = ApplicationLayerService()
        self.business_layer = BusinessLayerService()
        self.technology_layer = TechnologyLayerService()

    # Application Layer Methods
    def identify_application_components(
        self, application_portfolio: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """Identify application components from portfolio."""
        return self.application_layer.identify_application_components(
            application_portfolio, architecture_id
        )

    def analyze_application_dependencies(
        self, component_id: int, technical_context: Optional[str] = None
    ) -> Dict:
        """Analyze dependencies for an application component."""
        return self.application_layer.analyze_application_dependencies(
            component_id, technical_context
        )

    def identify_application_services(
        self, component_id: int, api_documentation: Optional[str] = None
    ) -> List[ArchiMateElement]:
        """Identify application services exposed by a component."""
        return self.application_layer.identify_application_services(component_id, api_documentation)

    def extract_data_objects(
        self, data_model_description: str, architecture_id: int, component_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """Extract data objects from data model description."""
        return self.application_layer.extract_data_objects(
            data_model_description, architecture_id, component_id
        )

    def model_data_flow(
        self, source_component_id: int, target_component_id: int, data_object_ids: List[int]
    ) -> List[ArchiMateRelationship]:
        """Model data flow between application components."""
        return self.application_layer.model_data_flow(
            source_component_id, target_component_id, data_object_ids
        )

    def define_application_interface(
        self, component_id: int, interface_spec: str
    ) -> ArchiMateElement:
        """Define an application interface (API, UI, integration point)."""
        return self.application_layer.define_application_interface(component_id, interface_spec)

    def map_application_to_business_process(
        self, component_id: int, process_id: int, usage_description: Optional[str] = None
    ) -> ArchiMateRelationship:
        """Map application component to business process it supports."""
        return self.application_layer.map_application_to_business_process(
            component_id, process_id, usage_description
        )

    def identify_business_capabilities(
        self, application_name: str, description: str, business_domain: str
    ) -> List[Dict]:
        """Identify standard Business Capabilities based on application context."""
        return self.application_layer.identify_business_capabilities(
            application_name, description, business_domain
        )

    # Business Layer Methods
    def extract_business_processes(
        self, business_description: str, architecture_id: int, capability_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """Extract business processes from business description using AI."""
        return self.business_layer.extract_business_processes(
            business_description, architecture_id, capability_id
        )

    def model_process_flow(
        self, process_id: int, detailed_description: Optional[str] = None
    ) -> Dict:
        """AI-powered process flow modeling with steps and sequence."""
        return self.business_layer.model_process_flow(process_id, detailed_description)

    def identify_business_actors(
        self, business_context: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """Identify business actors from business context."""
        return self.business_layer.identify_business_actors(business_context, architecture_id)

    def define_business_roles(
        self, actor_id: int, business_context: Optional[str] = None
    ) -> List[ArchiMateElement]:
        """Define business roles for an actor."""
        return self.business_layer.define_business_roles(actor_id, business_context)

    def identify_business_services(
        self, business_context: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """Identify business services offered by the organization."""
        return self.business_layer.identify_business_services(business_context, architecture_id)

    def map_service_realization(
        self, service_id: int, process_ids: Optional[List[int]] = None
    ) -> List[ArchiMateRelationship]:
        """Map which business processes realize a business service."""
        return self.business_layer.map_service_realization(service_id, process_ids)

    def extract_business_objects(
        self, business_context: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """Extract business objects (data/information entities) from context."""
        return self.business_layer.extract_business_objects(business_context, architecture_id)

    # Technology Layer Methods
    def identify_infrastructure_nodes(
        self, infrastructure_description: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """Identify infrastructure nodes from infrastructure description."""
        return self.technology_layer.identify_infrastructure_nodes(
            infrastructure_description, architecture_id
        )

    def model_deployment_architecture(
        self,
        application_component_id: int,
        node_ids: List[int],
        deployment_description: Optional[str] = None,
    ) -> List[ArchiMateRelationship]:
        """Map how applications deploy to infrastructure nodes."""
        return self.technology_layer.model_deployment_architecture(
            application_component_id, node_ids, deployment_description
        )

    def identify_devices(
        self, device_inventory: str, architecture_id: int
    ) -> List[ArchiMateElement]:
        """Identify physical devices from device inventory."""
        return self.technology_layer.identify_devices(device_inventory, architecture_id)

    def identify_system_software(
        self, software_inventory: str, architecture_id: int, node_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """Identify system software from software inventory."""
        return self.technology_layer.identify_system_software(
            software_inventory, architecture_id, node_id
        )

    def identify_technology_services(
        self, system_software_id: int, service_description: Optional[str] = None
    ) -> List[ArchiMateElement]:
        """Identify technology services provided by system software."""
        return self.technology_layer.identify_technology_services(
            system_software_id, service_description
        )

    def map_technology_to_application_service(
        self, technology_service_id: int, application_service_id: int
    ) -> ArchiMateRelationship:
        """Map technology service supporting application service."""
        return self.technology_layer.map_technology_to_application_service(
            technology_service_id, application_service_id
        )

    def model_network_topology(self, network_description: str, architecture_id: int) -> Dict:
        """Model network topology from network description."""
        return self.technology_layer.model_network_topology(network_description, architecture_id)

    def identify_artifacts(
        self, artifact_description: str, architecture_id: int, node_id: Optional[int] = None
    ) -> List[ArchiMateElement]:
        """Identify artifacts (physical data) from description."""
        return self.technology_layer.identify_artifacts(
            artifact_description, architecture_id, node_id
        )

    def analyze_technology_dependencies(
        self, node_id: int, infrastructure_context: Optional[str] = None
    ) -> Dict:
        """Analyze technology dependencies for a node."""
        return self.technology_layer.analyze_technology_dependencies(
            node_id, infrastructure_context
        )

    def identify_technology_constraints(
        self, architecture_id: int, infrastructure_context: str
    ) -> Dict:
        """Identify technology constraints and limitations."""
        return self.technology_layer.identify_technology_constraints(
            architecture_id, infrastructure_context
        )

    def get_service_status(self) -> Dict:
        """Get status of all layer services."""
        return {
            "layer_services": {
                "application_layer": "active",
                "business_layer": "active",
                "technology_layer": "active",
            },
            "total_services": 3,
            "active_services": 3,
            "consolidation": "complete",
        }
