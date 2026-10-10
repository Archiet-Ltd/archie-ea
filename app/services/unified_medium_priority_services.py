"""
Unified Medium Priority Services

Consolidates medium priority services into a single, comprehensive service:
1. Gap services (capability_gap_service, gap_analysis_service, gap_discovery_service)
2. Capability services (application_capability_catalog, capability_mapping, etc.)
3. Validation services (archimate_validation, implementation_validation)
4. Template services (template_instantiation, template_performance_optimizer)
5. Analysis services (impact_analysis, vendor_analysis, document_analysis)

Phase 5: Medium priority consolidations with full preservation
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional



# Import gap services
try:
    from .capability_gap_service import CapabilityGapService

    GAP_AVAILABLE = True
except ImportError:
    GAP_AVAILABLE = False

try:
    from .gap_analysis_service import GapAnalysisService

    GAP_ANALYSIS_AVAILABLE = True
except ImportError:
    GAP_ANALYSIS_AVAILABLE = False

try:
    from .gap_discovery_service import GapDiscoveryService

    GAP_DISCOVERY_AVAILABLE = True
except ImportError:
    GAP_DISCOVERY_AVAILABLE = False

# Import capability services
try:
    from .application_capability_catalog import ApplicationCapabilityCatalogService

    CAPABILITY_CATALOG_AVAILABLE = True
except ImportError:
    CAPABILITY_CATALOG_AVAILABLE = False

try:
    from .application_capability_mapper import ApplicationCapabilityMapperService

    CAPABILITY_MAPPER_AVAILABLE = True
except ImportError:
    CAPABILITY_MAPPER_AVAILABLE = False

try:
    from .capability_mapping_service import CapabilityMappingService

    CAPABILITY_MAPPING_AVAILABLE = True
except ImportError:
    CAPABILITY_MAPPING_AVAILABLE = False

try:
    from .capability_health_service import CapabilityHealthService

    CAPABILITY_HEALTH_AVAILABLE = True
except ImportError:
    CAPABILITY_HEALTH_AVAILABLE = False

# Import validation services
try:
    from .archimate_validation_service import ArchiMateValidationService

    ARCHIMATE_VALIDATION_AVAILABLE = True
except ImportError:
    ARCHIMATE_VALIDATION_AVAILABLE = False

try:
    from .implementation_validation_service import ImplementationValidationService

    IMPLEMENTATION_VALIDATION_AVAILABLE = True
except ImportError:
    IMPLEMENTATION_VALIDATION_AVAILABLE = False

# Import template services
try:
    from .template_instantiation_service import TemplateInstantiationService

    TEMPLATE_INSTANTIATION_AVAILABLE = True
except ImportError:
    TEMPLATE_INSTANTIATION_AVAILABLE = False

try:
    from .template_performance_optimizer import TemplatePerformanceOptimizer

    TEMPLATE_OPTIMIZER_AVAILABLE = True
except ImportError:
    TEMPLATE_OPTIMIZER_AVAILABLE = False

# Import analysis services
try:
    from app.modules.solutions_strategic.v2.services.impact_analysis_service import ImpactAnalysisService

    IMPACT_ANALYSIS_AVAILABLE = True
except ImportError:
    IMPACT_ANALYSIS_AVAILABLE = False

try:
    from .vendor_analysis_service import VendorAnalysisService

    VENDOR_ANALYSIS_AVAILABLE = True
except ImportError:
    VENDOR_ANALYSIS_AVAILABLE = False


class ServiceCategory(Enum):
    """Service categories for medium priority consolidation"""

    GAP = "gap"
    CAPABILITY = "capability"
    VALIDATION = "validation"
    TEMPLATE = "template"
    ANALYSIS = "analysis"


class UnifiedMediumPriorityService:
    """
    Unified Medium Priority Service

    Consolidates all medium priority services into a single interface while
    maintaining modular architecture and preserving all existing functionality.

    Categories:
    - Gap services: Capability gap analysis and discovery
    - Capability services: Application-capability mapping and cataloging
    - Validation services: ArchiMate and implementation validation
    - Template services: Template instantiation and optimization
    - Analysis services: Impact and vendor analysis
    """

    def __init__(self, category: Optional[ServiceCategory] = None, user_id: Optional[int] = None):
        """
        Initialize unified service

        Args:
            category: Specific service category (None for all)
            user_id: User ID for audit logging
        """
        self.category = category
        self.user_id = user_id
        self.logger = logging.getLogger(__name__)

        # Initialize service modules
        self._init_services()

        # Unified configuration
        self.config = self._load_unified_config()

    def _init_services(self):
        """Initialize all service modules"""
        self.services = {}

        # Gap services
        if GAP_AVAILABLE:
            try:
                self.services["capability_gap"] = CapabilityGapService()
                self.logger.info("Capability gap service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize capability gap service: {e}")

        if GAP_ANALYSIS_AVAILABLE:
            try:
                self.services["gap_analysis"] = GapAnalysisService()
                self.logger.info("Gap analysis service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize gap analysis service: {e}")

        if GAP_DISCOVERY_AVAILABLE:
            try:
                self.services["gap_discovery"] = GapDiscoveryService()
                self.logger.info("Gap discovery service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize gap discovery service: {e}")

        # Capability services
        if CAPABILITY_CATALOG_AVAILABLE:
            try:
                self.services["capability_catalog"] = ApplicationCapabilityCatalogService()
                self.logger.info("Capability catalog service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize capability catalog service: {e}")

        if CAPABILITY_MAPPER_AVAILABLE:
            try:
                self.services["capability_mapper"] = ApplicationCapabilityMapperService()
                self.logger.info("Capability mapper service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize capability mapper service: {e}")

        if CAPABILITY_MAPPING_AVAILABLE:
            try:
                self.services["capability_mapping"] = CapabilityMappingService()
                self.logger.info("Capability mapping service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize capability mapping service: {e}")

        if CAPABILITY_HEALTH_AVAILABLE:
            try:
                self.services["capability_health"] = CapabilityHealthService()
                self.logger.info("Capability health service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize capability health service: {e}")

        # Validation services
        if ARCHIMATE_VALIDATION_AVAILABLE:
            try:
                self.services["archimate_validation"] = ArchiMateValidationService()
                self.logger.info("ArchiMate validation service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize ArchiMate validation service: {e}")

        if IMPLEMENTATION_VALIDATION_AVAILABLE:
            try:
                self.services["implementation_validation"] = ImplementationValidationService()
                self.logger.info("Implementation validation service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize implementation validation service: {e}")

        # Template services
        if TEMPLATE_INSTANTIATION_AVAILABLE:
            try:
                self.services["template_instantiation"] = TemplateInstantiationService()
                self.logger.info("Template instantiation service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize template instantiation service: {e}")

        if TEMPLATE_OPTIMIZER_AVAILABLE:
            try:
                self.services["template_optimizer"] = TemplatePerformanceOptimizer()
                self.logger.info("Template optimizer service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize template optimizer service: {e}")

        # Analysis services
        if IMPACT_ANALYSIS_AVAILABLE:
            try:
                self.services["impact_analysis"] = ImpactAnalysisService()
                self.logger.info("Impact analysis service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize impact analysis service: {e}")

        if VENDOR_ANALYSIS_AVAILABLE:
            try:
                self.services["vendor_analysis"] = VendorAnalysisService()
                self.logger.info("Vendor analysis service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize vendor analysis service: {e}")

    def _load_unified_config(self) -> Dict[str, Any]:
        """Load unified configuration"""
        config = {
            "audit_logging": True,
            "cache_enabled": True,
            "validation_strict": True,
            "performance_tracking": True,
            "timeout": 30,
        }

        # Load from database if available
        try:
            # Implementation would load from configuration
            pass
        except Exception as e:
            self.logger.warning(f"Failed to load config from database: {e}")

        return config

    # === GAP SERVICE METHODS ===

    def analyze_capability_gap(
        self, capability_id: int, context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Analyze capability gap

        Args:
            capability_id: Capability ID
            context: Analysis context

        Returns:
            Gap analysis results
        """
        if "capability_gap" not in self.services:
            return {"success": False, "error": "Capability gap service not available"}

        try:
            service = self.services["capability_gap"]
            result = service.analyze_gap(capability_id, context)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit("analyze_capability_gap", {"capability_id": capability_id}, result)

            return result

        except Exception as e:
            self.logger.error(f"Capability gap analysis failed: {e}")
            return {"success": False, "error": str(e)}

    def discover_gaps(self, scope: str = "all", filters: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Discover gaps in the specified scope

        Args:
            scope: Analysis scope
            filters: Optional filters

        Returns:
            Discovered gaps
        """
        if "gap_discovery" not in self.services:
            return {"success": False, "error": "Gap discovery service not available"}

        try:
            service = self.services["gap_discovery"]
            result = service.discover_gaps(scope, filters)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit("discover_gaps", {"scope": scope, "filters": filters}, result)

            return result

        except Exception as e:
            self.logger.error(f"Gap discovery failed: {e}")
            return {"success": False, "error": str(e)}

    # === CAPABILITY SERVICE METHODS ===

    def catalog_capabilities(self, application_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Catalog capabilities

        Args:
            application_id: Optional application ID filter

        Returns:
            Capability catalog
        """
        if "capability_catalog" not in self.services:
            return {"success": False, "error": "Capability catalog service not available"}

        try:
            service = self.services["capability_catalog"]
            result = service.get_catalog(application_id)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit("catalog_capabilities", {"application_id": application_id}, result)

            return result

        except Exception as e:
            self.logger.error(f"Capability catalog failed: {e}")
            return {"success": False, "error": str(e)}

    def map_application_capabilities(
        self, application_id: int, capability_ids: List[int]
    ) -> Dict[str, Any]:
        """
        Map capabilities to application

        Args:
            application_id: Application ID
            capability_ids: List of capability IDs

        Returns:
            Mapping result
        """
        if "capability_mapper" not in self.services:
            return {"success": False, "error": "Capability mapper service not available"}

        try:
            service = self.services["capability_mapper"]
            result = service.map_capabilities(application_id, capability_ids)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit(
                    "map_application_capabilities",
                    {"application_id": application_id, "capability_ids": capability_ids},
                    result,
                )

            return result

        except Exception as e:
            self.logger.error(f"Capability mapping failed: {e}")
            return {"success": False, "error": str(e)}

    def assess_capability_health(self, capability_id: int) -> Dict[str, Any]:
        """
        Assess capability health

        Args:
            capability_id: Capability ID

        Returns:
            Health assessment
        """
        if "capability_health" not in self.services:
            return {"success": False, "error": "Capability health service not available"}

        try:
            service = self.services["capability_health"]
            result = service.assess_health(capability_id)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit(
                    "assess_capability_health", {"capability_id": capability_id}, result
                )

            return result

        except Exception as e:
            self.logger.error(f"Capability health assessment failed: {e}")
            return {"success": False, "error": str(e)}

    # === VALIDATION SERVICE METHODS ===

    def validate_archimate_model(self, model_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate ArchiMate model

        Args:
            model_data: Model data to validate

        Returns:
            Validation results
        """
        if "archimate_validation" not in self.services:
            return {"success": False, "error": "ArchiMate validation service not available"}

        try:
            service = self.services["archimate_validation"]
            result = service.validate_model(model_data)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit(
                    "validate_archimate_model", {"model_type": model_data.get("type")}, result
                )

            return result

        except Exception as e:
            self.logger.error(f"ArchiMate validation failed: {e}")
            return {"success": False, "error": str(e)}

    def validate_implementation(self, implementation_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate implementation

        Args:
            implementation_data: Implementation data

        Returns:
            Validation results
        """
        if "implementation_validation" not in self.services:
            return {"success": False, "error": "Implementation validation service not available"}

        try:
            service = self.services["implementation_validation"]
            result = service.validate(implementation_data)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit(
                    "validate_implementation",
                    {"implementation_type": implementation_data.get("type")},
                    result,
                )

            return result

        except Exception as e:
            self.logger.error(f"Implementation validation failed: {e}")
            return {"success": False, "error": str(e)}

    # === TEMPLATE SERVICE METHODS ===

    def instantiate_template(self, template_id: int, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Instantiate template

        Args:
            template_id: Template ID
            parameters: Template parameters

        Returns:
            Instantiated template
        """
        if "template_instantiation" not in self.services:
            return {"success": False, "error": "Template instantiation service not available"}

        try:
            service = self.services["template_instantiation"]
            result = service.instantiate(template_id, parameters)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit("instantiate_template", {"template_id": template_id}, result)

            return result

        except Exception as e:
            self.logger.error(f"Template instantiation failed: {e}")
            return {"success": False, "error": str(e)}

    def optimize_template_performance(self, template_id: int) -> Dict[str, Any]:
        """
        Optimize template performance

        Args:
            template_id: Template ID

        Returns:
            Optimization results
        """
        if "template_optimizer" not in self.services:
            return {"success": False, "error": "Template optimizer service not available"}

        try:
            service = self.services["template_optimizer"]
            result = service.optimize(template_id)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit(
                    "optimize_template_performance", {"template_id": template_id}, result
                )

            return result

        except Exception as e:
            self.logger.error(f"Template optimization failed: {e}")
            return {"success": False, "error": str(e)}

    # === ANALYSIS SERVICE METHODS ===

    def analyze_impact(self, change_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze impact of changes

        Args:
            change_data: Change data

        Returns:
            Impact analysis
        """
        if "impact_analysis" not in self.services:
            return {"success": False, "error": "Impact analysis service not available"}

        try:
            service = self.services["impact_analysis"]
            result = service.analyze(change_data)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit("analyze_impact", {"change_type": change_data.get("type")}, result)

            return result

        except Exception as e:
            self.logger.error(f"Impact analysis failed: {e}")
            return {"success": False, "error": str(e)}

    def analyze_vendor(self, vendor_id: int, criteria: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Analyze vendor

        Args:
            vendor_id: Vendor ID
            criteria: Analysis criteria

        Returns:
            Vendor analysis
        """
        if "vendor_analysis" not in self.services:
            return {"success": False, "error": "Vendor analysis service not available"}

        try:
            service = self.services["vendor_analysis"]
            result = service.analyze(vendor_id, criteria)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit("analyze_vendor", {"vendor_id": vendor_id}, result)

            return result

        except Exception as e:
            self.logger.error(f"Vendor analysis failed: {e}")
            return {"success": False, "error": str(e)}

    # === UNIFIED INTERFACE METHODS ===

    def process_unified_request(
        self, category: ServiceCategory, operation: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process unified request across service categories

        Args:
            category: Service category
            operation: Operation name
            data: Operation data

        Returns:
            Processed result
        """
        try:
            # Route to appropriate service based on category and operation
            service_map = {
                ServiceCategory.GAP: {
                    "analyze_gap": ("capability_gap", "analyze_gap"),
                    "discover_gaps": ("gap_discovery", "discover_gaps"),
                    "gap_analysis": ("gap_analysis", "analyze"),
                },
                ServiceCategory.CAPABILITY: {
                    "catalog": ("capability_catalog", "get_catalog"),
                    "map_capabilities": ("capability_mapper", "map_capabilities"),
                    "assess_health": ("capability_health", "assess_health"),
                    "create_mapping": ("capability_mapping", "create_mapping"),
                },
                ServiceCategory.VALIDATION: {
                    "validate_archimate": ("archimate_validation", "validate_model"),
                    "validate_implementation": ("implementation_validation", "validate"),
                },
                ServiceCategory.TEMPLATE: {
                    "instantiate": ("template_instantiation", "instantiate"),
                    "optimize": ("template_optimizer", "optimize"),
                },
                ServiceCategory.ANALYSIS: {
                    "analyze_impact": ("impact_analysis", "analyze"),
                    "analyze_vendor": ("vendor_analysis", "analyze"),
                },
            }

            if category not in service_map:
                return {"success": False, "error": f"Unknown category: {category}"}

            if operation not in service_map[category]:
                return {
                    "success": False,
                    "error": f"Unknown operation: {operation} in category {category}",
                }

            service_name, method_name = service_map[category][operation]

            if service_name not in self.services:
                return {"success": False, "error": f"Service {service_name} not available"}

            service = self.services[service_name]
            method = getattr(service, method_name)

            # Call the method with provided data
            result = method(**data)

            return result

        except Exception as e:
            self.logger.error(f"Unified request processing failed: {e}")
            return {"success": False, "error": str(e)}

    def get_service_status(self) -> Dict[str, Any]:
        """
        Get status of all services

        Returns:
            Service status information
        """
        status = {
            "category": self.category.value if self.category else "all",
            "services": {},
            "config": self.config,
        }

        for name, service in self.services.items():
            try:
                # Basic health check
                status["services"][name] = {"available": True, "type": type(service).__name__}
            except Exception as e:
                status["services"][name] = {"available": False, "error": str(e)}

        return status

    # === UTILITY METHODS ===

    def _log_audit(self, action: str, data: Dict[str, Any], result: Dict[str, Any]):
        """Log audit information"""
        try:
            audit_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "user_id": self.user_id,
                "action": action,
                "data": data,
                "result_success": result.get("success", False),
                "category": self.category.value if self.category else "all",
            }

            # In a real implementation, this would be saved to database
            self.logger.info(f"Audit: {audit_entry}")

        except Exception as e:
            self.logger.error(f"Audit logging failed: {e}")

    def get_available_categories(self) -> List[str]:
        """Get list of available service categories"""
        categories = []

        if any("gap" in name for name in self.services.keys()):
            categories.append(ServiceCategory.GAP.value)
        if any("capability" in name for name in self.services.keys()):
            categories.append(ServiceCategory.CAPABILITY.value)
        if any("validation" in name for name in self.services.keys()):
            categories.append(ServiceCategory.VALIDATION.value)
        if any("template" in name for name in self.services.keys()):
            categories.append(ServiceCategory.TEMPLATE.value)
        if any("analysis" in name for name in self.services.keys()):
            categories.append(ServiceCategory.ANALYSIS.value)

        return categories


# === FACTORY FUNCTIONS ===


def create_unified_medium_service(
    category: Optional[ServiceCategory] = None, user_id: Optional[int] = None
) -> UnifiedMediumPriorityService:
    """
    Factory function to create unified medium priority service

    Args:
        category: Service category
        user_id: User ID

    Returns:
        Unified service instance
    """
    return UnifiedMediumPriorityService(category=category, user_id=user_id)


def get_available_medium_services() -> Dict[str, List[str]]:
    """
    Get available services by category

    Returns:
        Dictionary of services by category
    """
    services = {"gap": [], "capability": [], "validation": [], "template": [], "analysis": []}

    if GAP_AVAILABLE:
        services["gap"].append("capability_gap")
    if GAP_ANALYSIS_AVAILABLE:
        services["gap"].append("gap_analysis")
    if GAP_DISCOVERY_AVAILABLE:
        services["gap"].append("gap_discovery")

    if CAPABILITY_CATALOG_AVAILABLE:
        services["capability"].append("capability_catalog")
    if CAPABILITY_MAPPER_AVAILABLE:
        services["capability"].append("capability_mapper")
    if CAPABILITY_MAPPING_AVAILABLE:
        services["capability"].append("capability_mapping")
    if CAPABILITY_HEALTH_AVAILABLE:
        services["capability"].append("capability_health")

    if ARCHIMATE_VALIDATION_AVAILABLE:
        services["validation"].append("archimate_validation")
    if IMPLEMENTATION_VALIDATION_AVAILABLE:
        services["validation"].append("implementation_validation")

    if TEMPLATE_INSTANTIATION_AVAILABLE:
        services["template"].append("template_instantiation")
    if TEMPLATE_OPTIMIZER_AVAILABLE:
        services["template"].append("template_optimizer")

    if IMPACT_ANALYSIS_AVAILABLE:
        services["analysis"].append("impact_analysis")
    if VENDOR_ANALYSIS_AVAILABLE:
        services["analysis"].append("vendor_analysis")

    return services


# === BACKWARD COMPATIBILITY ===

# Provide backward compatibility aliases
CapabilityGapServiceProxy = UnifiedMediumPriorityService
GapAnalysisServiceProxy = UnifiedMediumPriorityService
ApplicationCapabilityCatalogServiceProxy = UnifiedMediumPriorityService
ArchiMateValidationServiceProxy = UnifiedMediumPriorityService
TemplateInstantiationServiceProxy = UnifiedMediumPriorityService
