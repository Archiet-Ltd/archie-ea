"""
Application Inference Service for ArchiMate Application Layer

Phase 3.1: APQC -> ArchiMate Derivation System

This service derives ArchiMate Application Layer elements by correlating:
1. APQC processes (standardized process classifications)
2. Application portfolio (ApplicationComponent model)
3. Vendor catalog (VendorProduct model)
4. Process-Application mappings

Inference Strategies:
- Direct Match: Uses existing ProcessApplicationMapping records
- Vendor Match: Matches APQC keywords with vendor product specialties
- Semantic Match: Compares application descriptions with process descriptions
- Transitive: Applications supporting capabilities that enable processes

ArchiMate Application Layer Elements Generated:
- ApplicationComponent: Software applications in the portfolio
- ApplicationFunction: Internal behavior of an application component
- ApplicationService: Externally visible service offered by an application
"""

import json
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from sqlalchemy import func

from app import db
from app.models.application_portfolio import ApplicationCapabilityMapping, ApplicationComponent
from app.models.apqc_process import APQCProcess, CapabilityProcessMapping, ProcessApplicationMapping
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.vendor.vendor_organization import VendorProduct
from app.services.application_architecture_mapper import APQC_KEYWORD_MAP

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class InferredApplication:
    """
    Represents an inferred application that supports an APQC process.

    Captures the result of application inference including the source of
    the inference and confidence scoring.

    Attributes:
        application_id: ID from portfolio if matched, None for vendor catalog matches
        application_name: Name of the application or product
        vendor_name: Vendor organization name if applicable
        inference_source: How the application was inferred
            - 'portfolio_match': Direct ProcessApplicationMapping exists
            - 'vendor_catalog': Matched via vendor product APQC keywords
            - 'semantic_inference': Description similarity match
            - 'transitive_capability': Via capability-process-application chain
        confidence: Confidence score (0.0 to 1.0)
        supported_apqc_codes: List of APQC process codes this application supports
        archimate_elements: List of ArchiMate elements to create
    """

    application_id: Optional[int]
    application_name: str
    vendor_name: Optional[str]
    inference_source: str
    confidence: float
    supported_apqc_codes: List[str] = field(default_factory=list)
    archimate_elements: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "application_id": self.application_id,
            "application_name": self.application_name,
            "vendor_name": self.vendor_name,
            "inference_source": self.inference_source,
            "confidence": self.confidence,
            "supported_apqc_codes": self.supported_apqc_codes,
            "archimate_elements": self.archimate_elements,
        }


# =============================================================================
# CONFIDENCE SCORING WEIGHTS
# =============================================================================

# Confidence weights for different inference sources
CONFIDENCE_WEIGHTS = {
    "portfolio_match": {
        "base": 0.85,
        "full_support": 0.15,
        "partial_support": 0.10,
        "minimal_support": 0.05,
    },
    "vendor_catalog": {
        "base": 0.70,
        "exact_match": 0.20,
        "keyword_match": 0.10,
    },
    "semantic_inference": {
        "base": 0.50,
        "high_similarity": 0.30,
        "medium_similarity": 0.15,
        "low_similarity": 0.05,
    },
    "transitive_capability": {
        "base": 0.60,
        "high_relationship_strength": 0.25,
        "medium_relationship_strength": 0.15,
        "low_relationship_strength": 0.05,
    },
}


# =============================================================================
# APPLICATION INFERENCE SERVICE
# =============================================================================


class ApplicationInferenceService:
    """
    Service to derive ArchiMate Application Layer elements by correlating:
    1. APQC processes
    2. Application portfolio (ApplicationComponent model)
    3. Vendor catalog (VendorProduct model)
    4. Process-Application mappings

    This service implements Phase 3.1 of the APQC -> ArchiMate derivation system,
    providing intelligent application inference from standardized process frameworks.
    """

    def __init__(self):
        """Initialize the ApplicationInferenceService."""
        self._vendor_keyword_cache: Dict[str, List[str]] = {}
        self._build_vendor_keyword_cache()
        logger.info("ApplicationInferenceService initialized")

    def _build_vendor_keyword_cache(self) -> None:
        """
        Build a reverse lookup cache from vendors to APQC keywords.

        Creates a mapping of vendor names to the APQC processes they typically support
        based on the PRODUCT_TO_VENDOR_MAP and APQC_KEYWORD_MAP.
        """
        # Map vendor specialties to APQC keywords
        vendor_specialties = {
            "SAP": ["erp", "finance", "accounting", "supply chain", "manufacturing", "hr"],
            "Oracle": ["erp", "finance", "database", "cloud", "supply chain", "hr"],
            "Microsoft": ["office", "collaboration", "crm", "erp", "analytics", "cloud"],
            "Salesforce": ["crm", "sales", "marketing", "customer service", "analytics"],
            "Workday": ["hr", "payroll", "talent", "finance", "workforce"],
            "ServiceNow": ["it service", "service desk", "incident", "workflow"],
            "Atlassian": ["project", "collaboration", "development", "documentation"],
            "Snowflake": ["analytics", "data", "warehouse", "business intelligence"],
            "Tableau": ["analytics", "reporting", "business intelligence", "visualization"],
        }

        for vendor, keywords in vendor_specialties.items():
            apqc_codes = []
            for keyword in keywords:
                if keyword.lower() in APQC_KEYWORD_MAP:
                    apqc_codes.extend(APQC_KEYWORD_MAP[keyword.lower()])
            self._vendor_keyword_cache[vendor] = list(set(apqc_codes))

        logger.debug(f"Built vendor keyword cache with {len(self._vendor_keyword_cache)} vendors")

    # =========================================================================
    # MAIN INFERENCE METHODS
    # =========================================================================

    def infer_applications_for_process(self, apqc_process_id: int) -> List[InferredApplication]:
        """
        Infer which applications support a given APQC process.

        Uses multiple inference strategies in order of confidence:
        1. Direct match via ProcessApplicationMapping
        2. Vendor catalog match via APQC keywords
        3. Semantic matching on descriptions
        4. Transitive inference through capabilities

        Args:
            apqc_process_id: ID of the APQC process to find applications for

        Returns:
            List of InferredApplication instances sorted by confidence
        """
        logger.info(f"Inferring applications for APQC process ID: {apqc_process_id}")

        # Fetch the APQC process
        apqc_process = db.session.get(APQCProcess, apqc_process_id)
        if not apqc_process:
            logger.warning(f"APQC process {apqc_process_id} not found")
            return []

        inferred_applications: List[InferredApplication] = []
        seen_apps: set = set()  # Track to avoid duplicates

        # Strategy 1: Direct portfolio match
        direct_matches = self._infer_from_portfolio_mapping(apqc_process)
        for app in direct_matches:
            key = (app.application_id, app.application_name)
            if key not in seen_apps:
                seen_apps.add(key)
                inferred_applications.append(app)

        # Strategy 2: Vendor catalog match
        vendor_matches = self._infer_from_vendor_catalog(apqc_process)
        for app in vendor_matches:
            key = (app.application_id, app.application_name)
            if key not in seen_apps:
                seen_apps.add(key)
                inferred_applications.append(app)

        # Strategy 3: Semantic inference
        semantic_matches = self._infer_from_semantic_matching(apqc_process)
        for app in semantic_matches:
            key = (app.application_id, app.application_name)
            if key not in seen_apps:
                seen_apps.add(key)
                inferred_applications.append(app)

        # Strategy 4: Transitive through capabilities
        transitive_matches = self._infer_from_capability_chain(apqc_process)
        for app in transitive_matches:
            key = (app.application_id, app.application_name)
            if key not in seen_apps:
                seen_apps.add(key)
                inferred_applications.append(app)

        # Sort by confidence descending
        inferred_applications.sort(key=lambda x: x.confidence, reverse=True)

        logger.info(
            f"Inferred {len(inferred_applications)} applications for process "
            f"{apqc_process.process_code}: {apqc_process.process_name}"
        )

        return inferred_applications

    def derive_application_elements(
        self, application_id: int, apqc_context: List[str]
    ) -> List[ArchiMateElement]:
        """
        Derive ArchiMate Application Layer elements from an application with APQC context.

        Generates:
        - ApplicationComponent: The main application element
        - ApplicationFunction: Internal behaviors based on supported processes
        - ApplicationService: Services exposed based on process outputs

        Args:
            application_id: ID of the ApplicationComponent
            apqc_context: List of related APQC process codes

        Returns:
            List of created ArchiMateElement instances
        """
        logger.info(
            f"Deriving ArchiMate elements for application {application_id} "
            f"with {len(apqc_context)} APQC codes"
        )

        application = db.session.get(ApplicationComponent, application_id)
        if not application:
            logger.warning(f"Application {application_id} not found")
            return []

        elements: List[ArchiMateElement] = []

        # Create or get ApplicationComponent element
        app_element = self._ensure_application_component_element(application)
        if app_element:
            elements.append(app_element)

        # Get supported APQC processes from context
        apqc_processes = APQCProcess.query.filter(APQCProcess.process_code.in_(apqc_context)).all()

        # Create ApplicationFunction elements based on processes
        functions = self._derive_application_functions(application, apqc_processes)
        for func_data in functions:
            func_element = self._create_application_function_element(application, func_data)
            if func_element:
                elements.append(func_element)
                # Create composition relationship
                if app_element:
                    self._create_composition_relationship(app_element, func_element)

        # Create ApplicationService elements
        services = self._derive_application_services(application, apqc_processes)
        for svc_data in services:
            svc_element = self._create_application_service_element(application, svc_data)
            if svc_element:
                elements.append(svc_element)
                # Create realization relationship from function to service
                if app_element:
                    self._create_realization_relationship(app_element, svc_element)

        try:
            db.session.commit()
            logger.info(
                f"Created {len(elements)} ArchiMate elements for application {application.name}"
            )
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to commit ArchiMate elements: {str(e)}")
            raise

        return elements

    def create_application_process_relationships(
        self,
        application_elements: List[ArchiMateElement],
        business_processes: List[ArchiMateElement],
    ) -> List[ArchiMateRelationship]:
        """
        Create 'serves' relationships between applications and business processes.

        In ArchiMate, ApplicationService 'serves' BusinessProcess, meaning
        the application provides functionality that supports the business process.

        Args:
            application_elements: List of application layer ArchiMateElements
            business_processes: List of business process ArchiMateElements

        Returns:
            List of created ArchiMateRelationship instances
        """
        logger.info(
            f"Creating serves relationships between {len(application_elements)} "
            f"applications and {len(business_processes)} processes"
        )

        relationships: List[ArchiMateRelationship] = []

        # Filter to get ApplicationService and ApplicationComponent elements
        app_services = [
            e
            for e in application_elements
            if e.type in ("ApplicationService", "ApplicationComponent")
        ]

        # Filter to get BusinessProcess elements
        bus_processes = [e for e in business_processes if e.type == "BusinessProcess"]

        for app_element in app_services:
            for process_element in bus_processes:
                # Check if relationship already exists
                existing = ArchiMateRelationship.query.filter_by(
                    type="serves", source_id=app_element.id, target_id=process_element.id
                ).first()

                if not existing:
                    relationship = ArchiMateRelationship(
                        type="serves",
                        source_id=app_element.id,
                        target_id=process_element.id,
                        architecture_id=app_element.architecture_id,
                    )
                    db.session.add(relationship)
                    relationships.append(relationship)
                    logger.debug(
                        f"Created serves relationship: {app_element.name} -> {process_element.name}"
                    )

        try:
            db.session.commit()
            logger.info(f"Created {len(relationships)} serves relationships")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to create relationships: {str(e)}")
            raise

        return relationships

    def infer_application_functions(
        self, application: ApplicationComponent, supported_processes: List[APQCProcess]
    ) -> List[dict]:
        """
        Infer ApplicationFunction elements based on what processes the application supports.

        ApplicationFunction represents a behavior element that groups automated
        behavior performed by an application component.

        Args:
            application: The ApplicationComponent instance
            supported_processes: List of APQC processes the application supports

        Returns:
            List of dicts with function definitions:
            {
                'name': str,
                'description': str,
                'process_codes': List[str],
                'function_type': str,
                'automation_level': str
            }
        """
        logger.info(
            f"Inferring application functions for {application.name} "
            f"based on {len(supported_processes)} processes"
        )

        functions = self._derive_application_functions(application, supported_processes)

        logger.info(f"Inferred {len(functions)} functions for {application.name}")

        return functions

    # =========================================================================
    # INFERENCE STRATEGY IMPLEMENTATIONS
    # =========================================================================

    def _infer_from_portfolio_mapping(self, apqc_process: APQCProcess) -> List[InferredApplication]:
        """
        Strategy 1: Direct match via ProcessApplicationMapping.

        This is the highest confidence match as it uses explicit mappings.
        """
        inferred = []

        # Query existing ProcessApplicationMapping records. Has no
        # organization_id of its own; foreign rows are filtered out below
        # (db.session.get on a TenantMixin model returns None for a
        # tenant mismatch on a cache miss), but the unfenced query still
        # loaded every organisation's rows into memory (pr310-v1 review,
        # DEFECT-3).
        from app.services.apqc_mapping_tenant_fence import (
            fenced_application_mappings_query,
        )

        mappings = fenced_application_mappings_query().filter(
            ProcessApplicationMapping.apqc_process_id == apqc_process.id
        ).all()

        for mapping in mappings:
            # Fetch the application
            application = db.session.get(ApplicationComponent, mapping.application_id)
            if not application:
                continue

            # Calculate confidence based on support level
            confidence = CONFIDENCE_WEIGHTS["portfolio_match"]["base"]
            if mapping.support_level == "full":
                confidence += CONFIDENCE_WEIGHTS["portfolio_match"]["full_support"]
            elif mapping.support_level == "partial":
                confidence += CONFIDENCE_WEIGHTS["portfolio_match"]["partial_support"]
            else:
                confidence += CONFIDENCE_WEIGHTS["portfolio_match"]["minimal_support"]

            # Generate ArchiMate elements to create
            archimate_elements = self._generate_archimate_elements_for_app(
                application, [apqc_process.process_code]
            )

            inferred.append(
                InferredApplication(
                    application_id=application.id,
                    application_name=application.name,
                    vendor_name=application.vendor_name,
                    inference_source="portfolio_match",
                    confidence=min(confidence, 1.0),
                    supported_apqc_codes=[apqc_process.process_code],
                    archimate_elements=archimate_elements,
                )
            )

        logger.debug(f"Portfolio match found {len(inferred)} applications")
        return inferred

    def _infer_from_vendor_catalog(self, apqc_process: APQCProcess) -> List[InferredApplication]:
        """
        Strategy 2: Vendor catalog match via APQC keywords.

        Matches vendor products based on their specialty areas and
        the APQC process keywords.
        """
        inferred = []
        process_code = apqc_process.process_code

        # Find matching vendor products based on keyword cache
        for vendor_name, apqc_codes in self._vendor_keyword_cache.items():
            # Check if any cached APQC codes match this process
            for cached_code in apqc_codes:
                if process_code.startswith(cached_code) or cached_code.startswith(
                    process_code.split(".")[0]
                ):
                    # Find vendor products for this vendor
                    from app.models.vendor.vendor_organization import VendorOrganization

                    vendor_org = VendorOrganization.query.filter(
                        func.lower(VendorOrganization.name) == vendor_name.lower()
                    ).first()

                    if vendor_org:
                        for product in vendor_org.products:
                            # Check if product has APQC mapping
                            confidence = CONFIDENCE_WEIGHTS["vendor_catalog"]["base"]

                            # Check product family alignment
                            if self._product_family_matches_process(product, apqc_process):
                                confidence += CONFIDENCE_WEIGHTS["vendor_catalog"]["exact_match"]
                            else:
                                confidence += CONFIDENCE_WEIGHTS["vendor_catalog"]["keyword_match"]

                            archimate_elements = self._generate_archimate_elements_for_product(
                                product, [process_code]
                            )

                            inferred.append(
                                InferredApplication(
                                    application_id=None,  # Vendor catalog, not in portfolio
                                    application_name=product.name,
                                    vendor_name=vendor_name,
                                    inference_source="vendor_catalog",
                                    confidence=min(confidence, 1.0),
                                    supported_apqc_codes=[process_code],
                                    archimate_elements=archimate_elements,
                                )
                            )
                    break  # Found a match for this vendor

        logger.debug(f"Vendor catalog match found {len(inferred)} applications")
        return inferred

    def _infer_from_semantic_matching(self, apqc_process: APQCProcess) -> List[InferredApplication]:
        """
        Strategy 3: Semantic matching on descriptions.

        Compares application descriptions with process descriptions
        using text similarity.
        """
        inferred = []
        process_text = f"{apqc_process.process_name} {apqc_process.process_description or ''}"
        process_text = process_text.lower()

        # Query applications with descriptions
        applications = (
            ApplicationComponent.query.filter(ApplicationComponent.description.isnot(None))
            .limit(100)
            .all()
        )  # Limit for performance

        for application in applications:
            app_text = f"{application.name} {application.description or ''}"
            app_text = app_text.lower()

            # Calculate similarity
            similarity = self._calculate_text_similarity(process_text, app_text)

            if similarity > 0.3:  # Threshold for inclusion
                confidence = CONFIDENCE_WEIGHTS["semantic_inference"]["base"]

                if similarity > 0.7:
                    confidence += CONFIDENCE_WEIGHTS["semantic_inference"]["high_similarity"]
                elif similarity > 0.5:
                    confidence += CONFIDENCE_WEIGHTS["semantic_inference"]["medium_similarity"]
                else:
                    confidence += CONFIDENCE_WEIGHTS["semantic_inference"]["low_similarity"]

                archimate_elements = self._generate_archimate_elements_for_app(
                    application, [apqc_process.process_code]
                )

                inferred.append(
                    InferredApplication(
                        application_id=application.id,
                        application_name=application.name,
                        vendor_name=application.vendor_name,
                        inference_source="semantic_inference",
                        confidence=min(confidence, 1.0),
                        supported_apqc_codes=[apqc_process.process_code],
                        archimate_elements=archimate_elements,
                    )
                )

        logger.debug(f"Semantic matching found {len(inferred)} applications")
        return inferred

    def _infer_from_capability_chain(self, apqc_process: APQCProcess) -> List[InferredApplication]:
        """
        Strategy 4: Transitive inference through capabilities.

        Finds applications via:
        APQC Process -> Capability (via CapabilityProcessMapping)
        -> Application (via ApplicationCapabilityMapping)
        """
        inferred = []

        # Find capabilities that enable this process. CapabilityProcessMapping
        # has no organization_id of its own (pr310-v1 review, DEFECT-4).
        from app.services.apqc_mapping_tenant_fence import (
            fenced_capability_mappings_query,
        )

        capability_mappings = fenced_capability_mappings_query().filter(
            CapabilityProcessMapping.apqc_process_id == apqc_process.id
        ).all()

        for cap_mapping in capability_mappings:
            # Find applications that support this capability
            # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
            app_mappings = ApplicationCapabilityMapping.query.filter_by(
                capability_id=cap_mapping.capability_id
            ).all()

            for app_mapping in app_mappings:
                application = app_mapping.application
                if not application:
                    continue

                # Calculate confidence based on relationship strengths
                confidence = CONFIDENCE_WEIGHTS["transitive_capability"]["base"]

                # Boost based on capability-process relationship strength
                if cap_mapping.relationship_strength >= 4:
                    confidence += CONFIDENCE_WEIGHTS["transitive_capability"][
                        "high_relationship_strength"
                    ]
                elif cap_mapping.relationship_strength >= 3:
                    confidence += CONFIDENCE_WEIGHTS["transitive_capability"][
                        "medium_relationship_strength"
                    ]
                else:
                    confidence += CONFIDENCE_WEIGHTS["transitive_capability"][
                        "low_relationship_strength"
                    ]

                # Further boost based on application-capability support level
                if app_mapping.support_level == "full":
                    confidence += 0.05

                archimate_elements = self._generate_archimate_elements_for_app(
                    application, [apqc_process.process_code]
                )

                inferred.append(
                    InferredApplication(
                        application_id=application.id,
                        application_name=application.name,
                        vendor_name=application.vendor_name,
                        inference_source="transitive_capability",
                        confidence=min(confidence, 1.0),
                        supported_apqc_codes=[apqc_process.process_code],
                        archimate_elements=archimate_elements,
                    )
                )

        logger.debug(f"Transitive inference found {len(inferred)} applications")
        return inferred

    # =========================================================================
    # ARCHIMATE ELEMENT GENERATION
    # =========================================================================

    def _ensure_application_component_element(
        self, application: ApplicationComponent
    ) -> Optional[ArchiMateElement]:
        """
        Ensure an ArchiMate ApplicationComponent element exists for the application.
        """
        # Check if already linked
        if application.archimate_element_id:
            existing = db.session.get(ArchiMateElement, application.archimate_element_id)
            if existing:
                return existing

        # Create new element
        element = ArchiMateElement(
            name=application.name,
            type="ApplicationComponent",
            layer="application",
            description=application.description or f"Application: {application.name}",
            scope="application",
        )
        db.session.add(element)
        db.session.flush()

        # Link to application
        application.archimate_element_id = element.id

        logger.debug(f"Created ApplicationComponent element for {application.name}")
        return element

    def _create_application_function_element(
        self, application: ApplicationComponent, func_data: dict
    ) -> Optional[ArchiMateElement]:
        """
        Create an ArchiMate ApplicationFunction element.
        """
        element = ArchiMateElement(
            name=func_data.get("name", f"{application.name} Function"),
            type="ApplicationFunction",
            layer="application",
            description=func_data.get("description", ""),
            scope="application",
        )

        # Store additional data in properties
        properties = {
            "process_codes": func_data.get("process_codes", []),
            "function_type": func_data.get("function_type", "operational"),
            "automation_level": func_data.get("automation_level", "full"),
            "source_application_id": application.id,
        }
        element.properties = json.dumps(properties)

        db.session.add(element)
        db.session.flush()

        logger.debug(f"Created ApplicationFunction element: {element.name}")
        return element

    def _create_application_service_element(
        self, application: ApplicationComponent, svc_data: dict
    ) -> Optional[ArchiMateElement]:
        """
        Create an ArchiMate ApplicationService element.
        """
        element = ArchiMateElement(
            name=svc_data.get("name", f"{application.name} Service"),
            type="ApplicationService",
            layer="application",
            description=svc_data.get("description", ""),
            scope="application",
        )

        # Store additional data in properties
        properties = {
            "process_codes": svc_data.get("process_codes", []),
            "service_type": svc_data.get("service_type", "business"),
            "interface_type": svc_data.get("interface_type", "internal"),
            "source_application_id": application.id,
        }
        element.properties = json.dumps(properties)

        db.session.add(element)
        db.session.flush()

        logger.debug(f"Created ApplicationService element: {element.name}")
        return element

    def _create_composition_relationship(
        self, parent: ArchiMateElement, child: ArchiMateElement
    ) -> Optional[ArchiMateRelationship]:
        """
        Create a composition relationship between parent and child elements.
        """
        # Check if exists
        existing = ArchiMateRelationship.query.filter_by(
            type="composition", source_id=parent.id, target_id=child.id
        ).first()

        if existing:
            return existing

        relationship = ArchiMateRelationship(
            type="composition",
            source_id=parent.id,
            target_id=child.id,
            architecture_id=parent.architecture_id,
        )
        db.session.add(relationship)
        return relationship

    def _create_realization_relationship(
        self, realizing: ArchiMateElement, realized: ArchiMateElement
    ) -> Optional[ArchiMateRelationship]:
        """
        Create a realization relationship (realizing element realizes realized element).
        """
        # Check if exists
        existing = ArchiMateRelationship.query.filter_by(
            type="realization", source_id=realizing.id, target_id=realized.id
        ).first()

        if existing:
            return existing

        relationship = ArchiMateRelationship(
            type="realization",
            source_id=realizing.id,
            target_id=realized.id,
            architecture_id=realizing.architecture_id,
        )
        db.session.add(relationship)
        return relationship

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _derive_application_functions(
        self, application: ApplicationComponent, supported_processes: List[APQCProcess]
    ) -> List[dict]:
        """
        Derive application functions based on supported processes.

        Groups processes by category and creates functions for each group.
        """
        functions = []

        # Group processes by category level 1
        category_groups: Dict[str, List[APQCProcess]] = {}
        for process in supported_processes:
            category = process.category_level_1 or "General"
            if category not in category_groups:
                category_groups[category] = []
            category_groups[category].append(process)

        # Create a function for each category group
        for category, processes in category_groups.items():
            process_codes = [p.process_code for p in processes]
            process_names = [p.process_name for p in processes]

            # Determine function name from category
            category_clean = category.split(" ", 1)[-1] if " " in category else category
            func_name = f"{application.name} - {category_clean} Function"

            # Determine automation level from application type
            automation_level = "full"
            if application.application_type == "manual":
                automation_level = "manual"
            elif application.application_type == "semi-automated":
                automation_level = "partial"

            functions.append(
                {
                    "name": func_name,
                    "description": f"Automated function supporting: {', '.join(process_names[:3])}{'...' if len(process_names) > 3 else ''}",
                    "process_codes": process_codes,
                    "function_type": "operational",
                    "automation_level": automation_level,
                }
            )

        return functions

    def _derive_application_services(
        self, application: ApplicationComponent, supported_processes: List[APQCProcess]
    ) -> List[dict]:
        """
        Derive application services based on supported processes.

        Creates externally visible services based on process outputs.
        """
        services = []

        # Group processes by type
        process_types: Dict[str, List[APQCProcess]] = {}
        for process in supported_processes:
            ptype = process.process_type or "Core"
            if ptype not in process_types:
                process_types[ptype] = []
            process_types[ptype].append(process)

        # Create a service for each process type
        for ptype, processes in process_types.items():
            process_codes = [p.process_code for p in processes]

            svc_name = f"{application.name} {ptype} Service"

            # Determine service type
            service_type = "business"
            if application.api_available:
                service_type = "integration"

            # Determine interface type
            interface_type = "internal"
            if application.deployment_model in ("saas", "cloud"):
                interface_type = "external"

            services.append(
                {
                    "name": svc_name,
                    "description": f"Service providing {ptype.lower()} functionality",
                    "process_codes": process_codes,
                    "service_type": service_type,
                    "interface_type": interface_type,
                }
            )

        return services

    def _generate_archimate_elements_for_app(
        self, application: ApplicationComponent, process_codes: List[str]
    ) -> List[dict]:
        """
        Generate ArchiMate element definitions for an application.
        """
        elements = []

        # ApplicationComponent
        elements.append(
            {
                "type": "ApplicationComponent",
                "name": application.name,
                "description": application.description or f"Application: {application.name}",
                "layer": "application",
            }
        )

        # ApplicationFunction
        elements.append(
            {
                "type": "ApplicationFunction",
                "name": f"{application.name} Core Function",
                "description": f"Core functionality of {application.name}",
                "layer": "application",
                "process_codes": process_codes,
            }
        )

        # ApplicationService
        if application.api_available:
            elements.append(
                {
                    "type": "ApplicationService",
                    "name": f"{application.name} Service",
                    "description": f"Service exposed by {application.name}",
                    "layer": "application",
                    "process_codes": process_codes,
                }
            )

        return elements

    def _generate_archimate_elements_for_product(
        self, product: VendorProduct, process_codes: List[str]
    ) -> List[dict]:
        """
        Generate ArchiMate element definitions for a vendor product.
        """
        elements = []
        vendor_name = product.vendor_organization.name if product.vendor_organization else "Unknown"

        # ApplicationComponent
        elements.append(
            {
                "type": "ApplicationComponent",
                "name": f"{vendor_name} {product.name}",
                "description": product.description or f"Vendor product: {product.name}",
                "layer": "application",
            }
        )

        # ApplicationFunction based on product family
        if product.product_family:
            family_name = product.product_family.family_name
            elements.append(
                {
                    "type": "ApplicationFunction",
                    "name": f"{product.name} {family_name} Function",
                    "description": f"{family_name} functionality",
                    "layer": "application",
                    "process_codes": process_codes,
                }
            )

        # ApplicationService if API available
        if product.api_availability:
            elements.append(
                {
                    "type": "ApplicationService",
                    "name": f"{product.name} Integration Service",
                    "description": f"Integration service for {product.name}",
                    "layer": "application",
                    "process_codes": process_codes,
                }
            )

        return elements

    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate similarity between two text strings.

        Uses SequenceMatcher for basic similarity scoring.
        """
        return SequenceMatcher(None, text1, text2).ratio()

    def _product_family_matches_process(
        self, product: VendorProduct, apqc_process: APQCProcess
    ) -> bool:
        """
        Check if a product family aligns with an APQC process.
        """
        family_process_map = {
            "ERP": ["4.", "5.", "8."],  # Supply chain, Manufacturing, Finance
            "CRM": ["3."],  # Sales & Marketing
            "HCM": ["6."],  # HR
            "Analytics": ["1."],  # Strategy & Planning
            "IT Service": ["7."],  # IT Management
        }

        if not product.product_family:
            return False

        family_name = product.product_family.family_name
        process_prefixes = family_process_map.get(family_name, [])
        for prefix in process_prefixes:
            if apqc_process.process_code.startswith(prefix):
                return True

        return False
