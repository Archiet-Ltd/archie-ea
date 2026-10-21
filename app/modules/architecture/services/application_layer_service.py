"""
AI-Powered Application Layer Service for ArchiMate 3.2

This service provides comprehensive Application Layer modeling:
- Application Component identification (systems, modules)
- Application Service mapping
- Data Object modeling
- Application Interface definition
- Application-to-Business mapping
- Technology dependency analysis

ArchiMate 3.2 Application Layer Elements:
- ApplicationComponent: Modular, deployable, replaceable software (SAP, Salesforce, Custom App)
- ApplicationFunction: Automated behavior of application component
- ApplicationInteraction: Behavior by 2+ application components
- ApplicationService: Explicitly defined exposed application behavior
- ApplicationInterface: Point of access where application services are made available
- DataObject: Data structured for automated processing
- ApplicationEvent: Application state change
- ApplicationProcess: Sequence of application behaviors
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
from app.services.llm_service import LLMService
from app.models.archimate_element_types import is_application_kind


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
