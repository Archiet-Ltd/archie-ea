"""

pplication Mapping Orchestrator Service.

Unified mapping engine supporting both ATOMIC and PREVIEW transaction modes.
Single source of truth for all application mapping operations.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging
from app.models.archimate_element_types import is_application_kind

logger = logging.getLogger(__name__)


@dataclass
class MappingOptions:
    """Configuration options for mapping operations."""
    map_capabilities: bool = True
    map_processes: bool = True
    generate_archimate: bool = True
    archimate_mode: str = "standard"  # quick, standard, comprehensive
    match_vendor_products: bool = True
    clone_vendor_archimate: bool = False
    use_confidence_review: bool = False
    confidence_threshold: float = 0.7
    created_by: str = "orchestrator"


@dataclass
class MappingResult:
    """Result of a mapping operation."""
    success: bool = False
    application_id: Optional[int] = None
    application_name: str = "Unknown"
    created: bool = False
    updated: bool = False
    transaction_mode: str = ""
    mappings_created: Dict[str, int] = field(default_factory=lambda: {
        "capabilities": 0,
        "processes": 0,
        "archimate_elements": 0,
        "vendor_archimate": 0
    })
    ai_analysis: Dict[str, Any] = field(default_factory=lambda: {
        "capability_mappings": [],
        "process_mappings": [],
        "archimate_elements": []
    })
    vendor_info: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)


class ApplicationMappingOrchestrator:
    """
    Unified mapping engine for application analysis and mapping.

    Supports two transaction modes:
    - ATOMIC: All operations committed in a single transaction
    - PREVIEW: Analysis performed, results returned, no persistence

    This is the single source of truth for all mapping operations.
    """

    def __init__(self):
        self._ai_service = None
        self._archimate_service = None
        self._vendor_service = None

    def _get_ai_service(self):
        """Lazy load AI import service."""
        if self._ai_service is None:
            from app.services.ai_import_service import get_ai_import_service
            self._ai_service = get_ai_import_service()
        return self._ai_service

    def _get_archimate_service(self):
        """Lazy load ArchiMate service."""
        if self._archimate_service is None:
            from app.services.archimate.archimate_llm_service import ArchiMateLLMService as ArchimateService
            self._archimate_service = ArchimateService()
        return self._archimate_service

    def _get_vendor_service(self):
        """Lazy load vendor product service."""
        if self._vendor_service is None:
            from app.services.vendor_product_service import VendorProductService
            self._vendor_service = VendorProductService()
        return self._vendor_service

    def _execute_atomic(
        self,
        app_data: Dict[str, Any],
        options: MappingOptions,
        result: MappingResult
    ) -> MappingResult:
        """Execute atomic transaction - all operations committed together."""
        from app import db
        from app.models.application_portfolio import ApplicationComponent

        try:
            name = app_data.get("name", "").strip()
            if not name:
                result.errors.append("Application name required")
                return result

            # Create or update application
            existing = ApplicationComponent.query.filter(
                ApplicationComponent.name.ilike(name)
            ).first()

            if existing:
                for key, value in app_data.items():
                    if hasattr(existing, key) and value and key != "id":
                        setattr(existing, key, value)
                app = existing
                result.updated = True
            else:
                valid_fields = {col.name for col in ApplicationComponent.__table__.columns}
                filtered = {k: v for k, v in app_data.items() if k in valid_fields and k != "id"}
                app = ApplicationComponent(**filtered)
                db.session.add(app)
                result.created = True

            db.session.flush()
            result.application_id = app.id

            # Build context for AI analysis
            ai_service = self._get_ai_service()
            app_context = ai_service._build_application_context(app)

            # Capability mapping
            if options.map_capabilities:
                self._map_capabilities_atomic(app, app_context, options, result)

            # Process mapping
            if options.map_processes:
                self._map_processes_atomic(app, app_context, options, result)

            # ArchiMate generation
            if options.generate_archimate:
                self._generate_archimate_atomic(app, app_context, options, result)

            # Vendor product matching
            if options.match_vendor_products:
                self._match_vendor_products_atomic(app, app_data, options, result)

            # Clone vendor ArchiMate templates
            if options.clone_vendor_archimate:
                self._clone_vendor_archimate_atomic(app, options, result)

            db.session.commit()
            result.success = True

        except Exception as e:
            db.session.rollback()
            logger.error(f"Atomic transaction failed: {e}")
            result.errors.append(f"Transaction error: {str(e)}")

        return result

    def _execute_preview(
        self,
        app_data: Dict[str, Any],
        options: MappingOptions,
        result: MappingResult
    ) -> MappingResult:
        """Execute preview mode - analyze only, no persistence."""
        from app.models.application_portfolio import ApplicationComponent

        try:
            name = app_data.get("name", "").strip()
            if not name:
                result.errors.append("Application name required")
                return result

            # Create temporary app object (not persisted)
            temp_app = ApplicationComponent(
                name=name,
                description=app_data.get("description", ""),
                business_purpose=app_data.get("business_purpose", ""),
            )
            temp_app.id = 0  # Indicate not persisted
            result.application_id = 0

            # Build context for AI analysis
            ai_service = self._get_ai_service()
            app_context = ai_service._build_application_context(temp_app)

            # Capability mapping (preview)
            if options.map_capabilities:
                caps = ai_service._map_capabilities_with_ai(temp_app, app_context)
                result.ai_analysis["capability_mappings"] = caps
                result.mappings_created["capabilities"] = len([
                    c for c in caps
                    if c.get("confidence_score", 0) >= options.confidence_threshold
                ])

            # Process mapping (preview)
            if options.map_processes:
                procs = ai_service._classify_processes_with_ai(temp_app, app_context)
                result.ai_analysis["process_mappings"] = procs
                result.mappings_created["processes"] = len([
                    p for p in procs
                    if p.get("similarity_score", 0) >= options.confidence_threshold
                ])

            # ArchiMate generation (preview)
            if options.generate_archimate:
                arch = ai_service._generate_archimate_with_ai(
                    temp_app, app_context, mode=options.archimate_mode
                )
                result.ai_analysis["archimate_elements"] = arch
                result.mappings_created["archimate_elements"] = len(arch)

            # Vendor product matching (preview)
            if options.match_vendor_products:
                self._match_vendor_products_preview(name, app_data, options, result)

            # Note: Vendor ArchiMate cloning not available in preview (requires persisted app)
            if options.clone_vendor_archimate:
                result.errors.append(
                    "Vendor ArchiMate cloning requires ATOMIC mode (application must be persisted)"
                )

            result.success = True

        except Exception as e:
            logger.error(f"Preview analysis failed: {e}")
            result.errors.append(f"Preview error: {str(e)}")

        return result

    def _map_capabilities_atomic(
        self,
        app: Any,
        app_context: Dict[str, Any],
        options: MappingOptions,
        result: MappingResult
    ) -> None:
        """Create capability mappings in atomic transaction."""
        from app import db
        from app.models.unified_application_capability_mapping import (
            UnifiedApplicationCapabilityMapping,
        )

        try:
            ai_service = self._get_ai_service()
            caps = ai_service._map_capabilities_with_ai(app, app_context)
            result.ai_analysis["capability_mappings"] = caps

            # Batch prefetch existing capability mappings to avoid N+1
            cap_ids = [m["capability_id"] for m in caps if m.get("confidence_score", 0) >= options.confidence_threshold and "capability_id" in m]
            existing_cap_set = set()
            if cap_ids:
                existing_caps = UnifiedApplicationCapabilityMapping.query.filter(
                    UnifiedApplicationCapabilityMapping.application_component_id == app.id,
                    UnifiedApplicationCapabilityMapping.unified_capability_id.in_(cap_ids)
                ).all()
                existing_cap_set = {ec.unified_capability_id for ec in existing_caps}

            for m in caps:
                if m.get("confidence_score", 0) >= options.confidence_threshold:
                    if m["capability_id"] not in existing_cap_set:
                        db.session.add(
                            UnifiedApplicationCapabilityMapping(
                                application_component_id=app.id,
                                unified_capability_id=m["capability_id"],
                            )
                        )
                        result.mappings_created["capabilities"] += 1
        except Exception as e:
            result.errors.append(f"Capability mapping error: {e}")

    def _map_processes_atomic(
        self,
        app: Any,
        app_context: Dict[str, Any],
        options: MappingOptions,
        result: MappingResult
    ) -> None:
        """Create process mappings in atomic transaction."""
        from app import db
        from app.models.apqc_process import ProcessApplicationMapping

        try:
            ai_service = self._get_ai_service()
            procs = ai_service._classify_processes_with_ai(app, app_context)
            result.ai_analysis["process_mappings"] = procs

            # Batch prefetch existing process mappings to avoid N+1
            proc_ids = [m["process_id"] for m in procs if m.get("similarity_score", 0) >= options.confidence_threshold and "process_id" in m]
            existing_proc_set = set()
            if proc_ids:
                existing_procs = ProcessApplicationMapping.query.filter(
                    ProcessApplicationMapping.application_id == app.id,
                    ProcessApplicationMapping.apqc_process_id.in_(proc_ids)
                ).all()
                existing_proc_set = {ep.apqc_process_id for ep in existing_procs}

            for m in procs:
                if m.get("similarity_score", 0) >= options.confidence_threshold:
                    if m["process_id"] not in existing_proc_set:
                        db.session.add(
                            ProcessApplicationMapping(
                                application_id=app.id,
                                apqc_process_id=m["process_id"],
                            )
                        )
                        result.mappings_created["processes"] += 1
        except Exception as e:
            result.errors.append(f"Process mapping error: {e}")

    def _generate_archimate_atomic(
        self,
        app: Any,
        app_context: Dict[str, Any],
        options: MappingOptions,
        result: MappingResult
    ) -> None:
        """Generate ArchiMate elements in atomic transaction."""
        try:
            ai_service = self._get_ai_service()
            elements = ai_service._generate_archimate_with_ai(app, app_context)
            result.ai_analysis["archimate_elements"] = elements

            if elements:
                archimate_service = self._get_archimate_service()
                primary_id = None
                for elem in elements:
                    el = archimate_service.create_element_from_dict(
                        elem, created_by=options.created_by
                    )
                    if el:
                        result.mappings_created["archimate_elements"] += 1
                        if not primary_id and is_application_kind(elem.get("type")):
                            primary_id = el.id
                if primary_id:
                    app.archimate_element_id = primary_id
        except Exception as e:
            result.errors.append(f"ArchiMate generation error: {e}")

    def _match_vendor_products_atomic(
        self,
        app: Any,
        app_data: Dict[str, Any],
        options: MappingOptions,
        result: MappingResult
    ) -> None:
        """Match and persist vendor products in atomic transaction."""
        try:
            vendor_service = self._get_vendor_service()
            vendor_result = vendor_service.extract_vendor_product(
                application_name=app.name, description=app.description or ""
            )

            if (vendor_result.product_id and
                vendor_result.product_confidence >= options.confidence_threshold):
                app.vendor_product_id = vendor_result.product_id
                result.vendor_info = {
                    "vendor": vendor_result.vendor_name,
                    "product": vendor_result.product_name,
                    "version": vendor_result.version,
                    "confidence": vendor_result.product_confidence,
                    "persisted": True
                }
            elif (vendor_result.vendor_id and
                  vendor_result.vendor_confidence >= options.confidence_threshold):
                app.vendor_name = vendor_result.vendor_name
                result.vendor_info = {
                    "vendor": vendor_result.vendor_name,
                    "confidence": vendor_result.vendor_confidence,
                    "persisted": True
                }
        except Exception as e:
            result.errors.append(f"Vendor matching error: {e}")

    def _match_vendor_products_preview(
        self,
        app_name: str,
        app_data: Dict[str, Any],
        options: MappingOptions,
        result: MappingResult
    ) -> None:
        """Preview vendor product matching (no persistence)."""
        try:
            vendor_service = self._get_vendor_service()
            vendor_result = vendor_service.extract_vendor_product(
                application_name=app_name,
                description=app_data.get("description", "")
            )

            if vendor_result.vendor_id or vendor_result.product_id:
                result.vendor_info = {
                    "vendor": vendor_result.vendor_name,
                    "product": vendor_result.product_name,
                    "confidence": (vendor_result.vendor_confidence or
                                  vendor_result.product_confidence),
                    "persisted": False
                }
        except Exception as e:
            logger.warning(f"Vendor matching failed for {app_name}: {e}")

    def _clone_vendor_archimate_atomic(
        self,
        app: Any,
        options: MappingOptions,
        result: MappingResult
    ) -> None:
        """Clone vendor ArchiMate templates in atomic transaction."""
        try:
            from app.services.application_architecture_mapper import (
                ApplicationArchitectureMapperService,
            )

            vendor_result = (
                ApplicationArchitectureMapperService.clone_vendor_archimate_to_application(
                    application_id=app.id, created_by=options.created_by
                )
            )
            if vendor_result.get("success"):
                result.mappings_created["vendor_archimate"] = vendor_result.get(
                    "elements_cloned", 0
                )
                if result.vendor_info is None:
                    result.vendor_info = {}
                result.vendor_info["archimate_cloned"] = True
        except Exception as e:
            result.errors.append(f"Vendor ArchiMate clone error: {e}")


# Singleton instance
_orchestrator = ApplicationMappingOrchestrator()


def get_application_mapping_orchestrator() -> ApplicationMappingOrchestrator:
    """Get the singleton orchestrator instance."""
    return _orchestrator
