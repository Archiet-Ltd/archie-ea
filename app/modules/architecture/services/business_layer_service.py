"""
AI-Powered Business Layer Service for ArchiMate 3.2

This service provides comprehensive Business Layer modeling:
- Business Process extraction and modeling
- Business Service identification
- Business Actor/Role mapping
- Process flow analysis
- Business Object modeling
- Service realization mapping

ArchiMate 3.2 Business Layer Elements:
- BusinessActor: Organizational entity (person, department, company)
- BusinessRole: Responsibility assigned to actor
- BusinessProcess: Sequence of business behaviors
- BusinessFunction: Collection of business behavior based on criteria
- BusinessService: Service that business offers to customers
- BusinessObject: Concept used within business domain
- BusinessEvent: Organizational state change
- BusinessInteraction: Unit of collective behavior by 2+ roles
- Contract: Formal/informal agreement
- Representation: Perceptible form of information
- Product: Coherent collection of services/passive structure elements
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


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
      "authority": "Approve orders up to 50K",
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
