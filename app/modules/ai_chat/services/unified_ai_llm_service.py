"""
-> app.modules.ai_chat.services.llm_service

Unified AI/LLM Service

Consolidates 7 AI/LLM services into a single, modular architecture:
1. llm_service.py (core LLM integration)
2. archimate_llm_service.py (ArchiMate-specific LLM)
3. ai_data_interaction_service.py (data modifications)
4. ai_architecture_service.py (architecture generation)
5. ai_impact_analysis_service.py (impact analysis)
6. multi_domain_chat_service.py (chat functionality)
7. business_output_service.py (stakeholder outputs)

Phase 4: AI/LLM services consolidation (7 → 1) with modular architecture
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional



# Import individual services for consolidation
try:
    from .llm_cache import get_cache
    from .llm_cost_tracker import LLMCostTracker
    from .llm_model_router import LLMModelRouter, TaskComplexity
    from .llm_service import LLMService

    CORE_LLM_AVAILABLE = True
except ImportError:
    CORE_LLM_AVAILABLE = False

try:
    from .archimate.archimate_llm_service import ArchiMateLLMService

    ARCHIMATE_AVAILABLE = True
except ImportError:
    ARCHIMATE_AVAILABLE = False

try:
    from .ai_data_interaction_service import AIDataInteractionService

    DATA_INTERACTION_AVAILABLE = True
except ImportError:
    DATA_INTERACTION_AVAILABLE = False

try:
    from .ai_architecture_service import AIArchitectureService

    AI_ARCHITECTURE_AVAILABLE = True
except ImportError:
    AI_ARCHITECTURE_AVAILABLE = False

try:
    from .ai_impact_analysis_service import AIImpactAnalysisService

    IMPACT_ANALYSIS_AVAILABLE = True
except ImportError:
    IMPACT_ANALYSIS_AVAILABLE = False

try:
    from .multi_domain_chat_service import MultiDomainChatService

    CHAT_AVAILABLE = True
except ImportError:
    CHAT_AVAILABLE = False

try:
    from .business_output_service import BusinessOutputService

    BUSINESS_OUTPUT_AVAILABLE = True
except ImportError:
    BUSINESS_OUTPUT_AVAILABLE = False


class ServiceMode(Enum):
    """Service operation modes"""

    CORE = "core"
    ARCHIMATE = "archimate"
    DATA_INTERACTION = "data_interaction"
    AI_ARCHITECTURE = "ai_architecture"
    IMPACT_ANALYSIS = "impact_analysis"
    CHAT = "chat"
    BUSINESS_OUTPUT = "business_output"
    UNIFIED = "unified"


class UnifiedAILLMService:
    """
    Unified AI/LLM Service

    Provides a single interface to all AI/LLM capabilities while maintaining
    modular architecture and preserving all existing functionality.

    Features:
    - Core LLM integration with multiple providers
    - ArchiMate-specific generation and analysis
    - Safe data interaction with guardrails
    - Architecture generation capabilities
    - Impact analysis functionality
    - Multi-domain chat interface
    - Business output transformation
    """

    def __init__(self, mode: ServiceMode = ServiceMode.UNIFIED, user_id: Optional[int] = None):
        """
        Initialize unified service

        Args:
            mode: Service operation mode
            user_id: User ID for audit logging
        """
        self.mode = mode
        self.user_id = user_id
        self.logger = logging.getLogger(__name__)

        # Initialize service modules
        self._init_services()

        # Unified configuration
        self.config = self._load_unified_config()

    def _init_services(self):
        """Initialize all service modules"""
        self.services = {}

        # Core LLM service
        if CORE_LLM_AVAILABLE:
            try:
                self.services["core"] = LLMService()
                self.services["cost_tracker"] = LLMCostTracker()
                self.services["model_router"] = LLMModelRouter()
                self.logger.info("Core LLM service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize core LLM service: {e}")

        # ArchiMate service
        if ARCHIMATE_AVAILABLE:
            try:
                self.services["archimate"] = ArchiMateLLMService()
                self.logger.info("ArchiMate service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize ArchiMate service: {e}")

        # Data interaction service
        if DATA_INTERACTION_AVAILABLE:
            try:
                self.services["data_interaction"] = AIDataInteractionService(user_id=self.user_id)
                self.logger.info("Data interaction service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize data interaction service: {e}")

        # AI architecture service
        if AI_ARCHITECTURE_AVAILABLE:
            try:
                self.services["ai_architecture"] = AIArchitectureService()
                self.logger.info("AI architecture service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize AI architecture service: {e}")

        # Impact analysis service
        if IMPACT_ANALYSIS_AVAILABLE:
            try:
                self.services["impact_analysis"] = AIImpactAnalysisService()
                self.logger.info("Impact analysis service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize impact analysis service: {e}")

        # Chat service
        if CHAT_AVAILABLE:
            try:
                self.services["chat"] = MultiDomainChatService()
                self.logger.info("Chat service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize chat service: {e}")

        # Business output service
        if BUSINESS_OUTPUT_AVAILABLE:
            try:
                self.services["business_output"] = BusinessOutputService()
                self.logger.info("Business output service initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize business output service: {e}")

    def _load_unified_config(self) -> Dict[str, Any]:
        """Load unified configuration"""
        config = {
            "cache_enabled": True,
            "cost_tracking": True,
            "audit_logging": True,
            "guardrails_enabled": True,
            "default_provider": None,
            "timeout": 30,
            "max_retries": 3,
        }

        # Load from database if available
        try:
            # Implementation would load from APISettings table
            pass
        except Exception as e:
            self.logger.warning(f"Failed to load config from database: {e}")

        return config

    # === CORE LLM METHODS ===

    def generate_text(
        self, prompt: str, provider: Optional[str] = None, model: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Generate text using core LLM service

        Args:
            prompt: Input prompt
            provider: LLM provider (optional)
            model: Specific model (optional)
            **kwargs: Additional parameters

        Returns:
            Generated text response
        """
        if "core" not in self.services:
            return {"success": False, "error": "Core LLM service not available"}

        try:
            service = self.services["core"]

            # Route to appropriate model if not specified
            if not provider and not model:
                model_router = self.services.get("model_router")
                if model_router:
                    model = model_router.select_model(prompt, TaskComplexity.MEDIUM)

            # Generate text
            result = service.generate_text(prompt, provider=provider, model=model, **kwargs)

            # Track cost if enabled
            if self.config["cost_tracking"] and "cost_tracker" in self.services:
                self.services["cost_tracker"].track_generation(result)

            return result

        except Exception as e:
            self.logger.error(f"Text generation failed: {e}")
            return {"success": False, "error": str(e)}

    # === ARCHIMATE METHODS ===

    def generate_archimate_element(
        self, element_type: str, requirements: str, context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Generate ArchiMate element using ArchiMate service

        Args:
            element_type: ArchiMate element type
            requirements: Requirements text
            context: Additional context

        Returns:
            Generated ArchiMate element
        """
        if "archimate" not in self.services:
            return {"success": False, "error": "ArchiMate service not available"}

        try:
            service = self.services["archimate"]
            result = service.generate_element(element_type, requirements, context)
            return result

        except Exception as e:
            self.logger.error(f"ArchiMate generation failed: {e}")
            return {"success": False, "error": str(e)}

    def validate_archimate_model(self, model_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate ArchiMate model

        Args:
            model_data: Model data to validate

        Returns:
            Validation results
        """
        if "archimate" not in self.services:
            return {"success": False, "error": "ArchiMate service not available"}

        try:
            service = self.services["archimate"]
            result = service.validate_model(model_data)
            return result

        except Exception as e:
            self.logger.error(f"ArchiMate validation failed: {e}")
            return {"success": False, "error": str(e)}

    # === DATA INTERACTION METHODS ===

    def create_capability(self, capability_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create business capability with validation

        Args:
            capability_data: Capability data

        Returns:
            Creation result
        """
        if "data_interaction" not in self.services:
            return {"success": False, "error": "Data interaction service not available"}

        try:
            service = self.services["data_interaction"]
            result = service.create_capability(capability_data)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit("create_capability", capability_data, result)

            return result

        except Exception as e:
            self.logger.error(f"Capability creation failed: {e}")
            return {"success": False, "error": str(e)}

    def modify_application(self, app_id: int, modifications: Dict[str, Any]) -> Dict[str, Any]:
        """
        Modify application with guardrails

        Args:
            app_id: Application ID
            modifications: Modifications to apply

        Returns:
            Modification result
        """
        if "data_interaction" not in self.services:
            return {"success": False, "error": "Data interaction service not available"}

        try:
            service = self.services["data_interaction"]
            result = service.modify_application(app_id, modifications)

            # Log audit if enabled
            if self.config["audit_logging"]:
                self._log_audit(
                    "modify_application", {"app_id": app_id, "modifications": modifications}, result
                )

            return result

        except Exception as e:
            self.logger.error(f"Application modification failed: {e}")
            return {"success": False, "error": str(e)}

    # === AI ARCHITECTURE METHODS ===

    def generate_architecture_diagram(
        self, requirements: str, diagram_type: str = "overview"
    ) -> Dict[str, Any]:
        """
        Generate architecture diagram

        Args:
            requirements: Architecture requirements
            diagram_type: Type of diagram

        Returns:
            Generated diagram
        """
        if "ai_architecture" not in self.services:
            return {"success": False, "error": "AI architecture service not available"}

        try:
            service = self.services["ai_architecture"]
            result = service.generate_diagram(requirements, diagram_type)
            return result

        except Exception as e:
            self.logger.error(f"Architecture diagram generation failed: {e}")
            return {"success": False, "error": str(e)}

    # === IMPACT ANALYSIS METHODS ===

    def analyze_change_impact(
        self, change_description: str, scope: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze impact of proposed changes.

        Args:
            change_description: Description of changes
            scope: Analysis scope

        Returns:
            Impact analysis results
        """
        if "impact_analysis" not in self.services:
            return {"success": False, "error": "Impact analysis service not available"}

        # AIImpactAnalysisService requires an application ID and scenario.
        # This unified entrypoint cannot satisfy that contract from a free-text
        # description alone. Callers must use analyze_application_impact directly
        # with a concrete app_id.
        return {
            "success": False,
            "error": (
                "analyze_change_impact requires a concrete application ID and scenario. "
                "Use AIImpactAnalysisService.analyze_application_impact(app_id, scenario) directly."
            ),
        }

    # === CHAT METHODS ===

    def process_chat_message(self, message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process chat message

        Args:
            message: Chat message
            context: Chat context

        Returns:
            Chat response
        """
        if "chat" not in self.services:
            return {"success": False, "error": "Chat service not available"}

        try:
            service = self.services["chat"]
            result = service.process_message(message, context)
            return result

        except Exception as e:
            self.logger.error(f"Chat processing failed: {e}")
            return {"success": False, "error": str(e)}

    # === BUSINESS OUTPUT METHODS ===

    def transform_for_stakeholder(
        self, data: Dict[str, Any], stakeholder_type: str
    ) -> Dict[str, Any]:
        """
        Transform data for specific stakeholder

        Args:
            data: Data to transform
            stakeholder_type: Type of stakeholder

        Returns:
            Transformed data
        """
        if "business_output" not in self.services:
            return {"success": False, "error": "Business output service not available"}

        try:
            service = self.services["business_output"]
            result = service.transform_for_stakeholder(data, stakeholder_type)
            return result

        except Exception as e:
            self.logger.error(f"Stakeholder transformation failed: {e}")
            return {"success": False, "error": str(e)}

    # === UNIFIED INTERFACE METHODS ===

    def process_unified_request(
        self, request_type: str, data: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        """
        Process unified request across all services

        Args:
            request_type: Type of request
            data: Request data
            **kwargs: Additional parameters

        Returns:
            Processed result
        """
        try:
            # Route to appropriate service based on request type
            service_map = {
                "generate_text": ("core", "generate_text"),
                "archimate_element": ("archimate", "generate_archimate_element"),
                "validate_archimate": ("archimate", "validate_archimate_model"),
                "create_capability": ("data_interaction", "create_capability"),
                "modify_application": ("data_interaction", "modify_application"),
                "generate_diagram": ("ai_architecture", "generate_architecture_diagram"),
                "analyze_impact": ("impact_analysis", "analyze_change_impact"),
                "chat_message": ("chat", "process_chat_message"),
                "stakeholder_output": ("business_output", "transform_for_stakeholder"),
            }

            if request_type not in service_map:
                return {"success": False, "error": f"Unknown request type: {request_type}"}

            service_name, method_name = service_map[request_type]

            if service_name not in self.services:
                return {"success": False, "error": f"Service {service_name} not available"}

            service = self.services[service_name]
            method = getattr(service, method_name)

            # Call the method with provided data
            result = method(**data, **kwargs)

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
        status = {"mode": self.mode.value, "services": {}, "config": self.config}

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
                "mode": self.mode.value,
            }

            # In a real implementation, this would be saved to database
            self.logger.info(f"Audit: {audit_entry}")

        except Exception as e:
            self.logger.error(f"Audit logging failed: {e}")

    def get_cost_summary(self) -> Dict[str, Any]:
        """Get cost tracking summary"""
        if "cost_tracker" not in self.services:
            return {"success": False, "error": "Cost tracking not available"}

        try:
            tracker = self.services["cost_tracker"]
            return tracker.get_summary()

        except Exception as e:
            self.logger.error(f"Cost summary failed: {e}")
            return {"success": False, "error": str(e)}

    def clear_cache(self):
        """Clear all caches"""
        try:
            cache = get_cache()
            cache.clear()
            self.logger.info("Cache cleared successfully")
            return {"success": True, "message": "Cache cleared"}

        except Exception as e:
            self.logger.error(f"Cache clearing failed: {e}")
            return {"success": False, "error": str(e)}


# === FACTORY FUNCTIONS ===


def create_unified_service(
    mode: ServiceMode = ServiceMode.UNIFIED, user_id: Optional[int] = None
) -> UnifiedAILLMService:
    """
    Factory function to create unified service

    Args:
        mode: Service mode
        user_id: User ID

    Returns:
        Unified service instance
    """
    return UnifiedAILLMService(mode=mode, user_id=user_id)


def get_available_services() -> List[str]:
    """
    Get list of available services

    Returns:
        List of available service names
    """
    services = []

    if CORE_LLM_AVAILABLE:
        services.append("core")
    if ARCHIMATE_AVAILABLE:
        services.append("archimate")
    if DATA_INTERACTION_AVAILABLE:
        services.append("data_interaction")
    if AI_ARCHITECTURE_AVAILABLE:
        services.append("ai_architecture")
    if IMPACT_ANALYSIS_AVAILABLE:
        services.append("impact_analysis")
    if CHAT_AVAILABLE:
        services.append("chat")
    if BUSINESS_OUTPUT_AVAILABLE:
        services.append("business_output")

    return services


# === BACKWARD COMPATIBILITY ===

# Provide backward compatibility aliases
LLMServiceProxy = UnifiedAILLMService
ArchiMateLLMServiceProxy = UnifiedAILLMService
AIDataInteractionServiceProxy = UnifiedAILLMService
