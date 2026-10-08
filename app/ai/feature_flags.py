"""
AI Feature Flags

Provides comprehensive feature flag system for AI capabilities with graceful degradation.
"""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum
import threading

from app.models import APISettings

logger = logging.getLogger(__name__)

class AIFeature(Enum):
    """AI feature types."""
    CHAT_INTERFACE = "chat_interface"
    DOCUMENT_ANALYSIS = "document_analysis"
    ARCHIMATE_GENERATION = "archimate_generation"
    GAP_DETECTION = "gap_detection"
    DUPLICATE_DETECTION = "duplicate_detection"
    VENDOR_DISCOVERY = "vendor_discovery"
    SUGGESTION_ENGINE = "suggestion_engine"
    WORKFLOW_AUTOMATION = "workflow_automation"
    CONSOLIDATION_ANALYSIS = "consolidation_analysis"
    CAPABILITY_MAPPING = "capability_mapping"

class FlagStatus(Enum):
    """Feature flag status."""
    ENABLED = "enabled"
    DISABLED = "disabled"
    DEGRADED = "degraded"
    BUDGET_EXCEEDED = "budget_exceeded"
    RATE_LIMITED = "rate_limited"

@dataclass
class FeatureFlag:
    """Represents an AI feature flag."""
    feature: AIFeature
    status: FlagStatus
    enabled: bool
    degraded: bool
    fallback_available: bool
    last_updated: datetime
    metadata: Dict[str, Any]

class AIFeatureFlags:
    """
    Manages AI feature flags with graceful degradation capabilities.
    """
    
    def __init__(self):
        """Initialize the AI feature flags system."""
        self._flags = {}
        self._lock = threading.Lock()
        
        # Initialize default feature flags
        self._initialize_default_flags()
        
        # Load flags from database
        self._load_flags_from_database()
    
    def _initialize_default_flags(self):
        """Initialize default feature flag configurations."""
        default_flags = {
            AIFeature.CHAT_INTERFACE: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': True,
                'cost_per_request': 0.01,
                'rate_limit_per_hour': 100
            },
            AIFeature.DOCUMENT_ANALYSIS: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': True,
                'cost_per_request': 0.05,
                'rate_limit_per_hour': 50
            },
            AIFeature.ARCHIMATE_GENERATION: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': True,
                'cost_per_request': 0.10,
                'rate_limit_per_hour': 20
            },
            AIFeature.GAP_DETECTION: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': True,
                'cost_per_request': 0.03,
                'rate_limit_per_hour': 30
            },
            AIFeature.DUPLICATE_DETECTION: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': True,
                'cost_per_request': 0.02,
                'rate_limit_per_hour': 40
            },
            AIFeature.VENDOR_DISCOVERY: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': True,
                'cost_per_request': 0.04,
                'rate_limit_per_hour': 25
            },
            AIFeature.SUGGESTION_ENGINE: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': True,
                'cost_per_request': 0.01,
                'rate_limit_per_hour': 200
            },
            AIFeature.WORKFLOW_AUTOMATION: {
                'enabled': True,
                'degraded': False,
                'fallback_available': False,
                'requires_llm': True,
                'cost_per_request': 0.08,
                'rate_limit_per_hour': 15
            },
            AIFeature.CONSOLIDATION_ANALYSIS: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': False,
                'cost_per_request': 0.02,
                'rate_limit_per_hour': 60
            },
            AIFeature.CAPABILITY_MAPPING: {
                'enabled': True,
                'degraded': False,
                'fallback_available': True,
                'requires_llm': True,
                'cost_per_request': 0.03,
                'rate_limit_per_hour': 40
            }
        }
        
        for feature, config in default_flags.items():
            self._flags[feature] = FeatureFlag(
                feature=feature,
                status=FlagStatus.ENABLED if config['enabled'] else FlagStatus.DISABLED,
                enabled=config['enabled'],
                degraded=config['degraded'],
                fallback_available=config['fallback_available'],
                last_updated=datetime.utcnow(),
                metadata=config
            )
    
    def _load_flags_from_database(self):
        """Load feature flags from database configuration."""
        try:
            # Get AI feature flags from database
            settings = APISettings.query.filter_by(category='ai_features').all()
            
            for setting in settings:
                try:
                    feature_name = setting.key
                    feature_value = setting.value
                    
                    # Parse feature name and value
                    if feature_name.startswith('ai_feature_'):
                        feature_name = feature_name[11:]  # Remove 'ai_feature_' prefix
                        
                        try:
                            feature = AIFeature(feature_name)
                            if feature in self._flags:
                                # Update flag status based on database value
                                if isinstance(feature_value, bool):
                                    self._flags[feature].enabled = feature_value
                                    self._flags[feature].status = FlagStatus.ENABLED if feature_value else FlagStatus.DISABLED
                                elif isinstance(feature_value, str):
                                    if feature_value.lower() == 'degraded':
                                        self._flags[feature].degraded = True
                                        self._flags[feature].status = FlagStatus.DEGRADED
                                    elif feature_value.lower() == 'disabled':
                                        self._flags[feature].enabled = False
                                        self._flags[feature].status = FlagStatus.DISABLED
                                
                                self._flags[feature].last_updated = datetime.utcnow()
                        except ValueError:
                            logger.warning(f"Unknown AI feature: {feature_name}")
                            
                except Exception as e:
                    logger.warning(f"Failed to parse AI feature flag {setting.key}: {e}")
                    
        except Exception as e:
            logger.warning(f"Failed to load AI feature flags from database: {e}")
    
    def is_feature_enabled(self, feature: AIFeature, user_id: Optional[str] = None) -> bool:
        """
        Check if an AI feature is enabled for a user.
        
        Args:
            feature: AI feature to check
            user_id: Optional user ID for user-specific checks
            
        Returns:
            True if feature is enabled, False otherwise
        """
        with self._lock:
            if feature not in self._flags:
                logger.warning(f"Unknown AI feature: {feature}")
                return False
            
            flag = self._flags[feature]
            
            # Check if feature is explicitly disabled
            if not flag.enabled:
                return False
            
            # Check if feature requires LLM and LLM is available
            if flag.metadata.get('requires_llm', True) and not self._is_llm_available():
                if flag.fallback_available:
                    return True  # Allow with fallback
                else:
                    return False
            
            # Check budget constraints
            if not self._check_budget_constraints(feature, user_id):
                flag.status = FlagStatus.BUDGET_EXCEEDED
                return flag.fallback_available
            
            # Check rate limits
            if not self._check_rate_limits(feature, user_id):
                flag.status = FlagStatus.RATE_LIMITED
                return flag.fallback_available
            
            return True
    
    def get_feature_status(self, feature: AIFeature) -> FlagStatus:
        """
        Get the current status of an AI feature.
        
        Args:
            feature: AI feature to check
            
        Returns:
            Current status of the feature
        """
        with self._lock:
            if feature not in self._flags:
                return FlagStatus.DISABLED
            
            return self._flags[feature].status
    
    def set_feature_status(self, feature: AIFeature, status: FlagStatus, reason: Optional[str] = None):
        """
        Set the status of an AI feature.
        
        Args:
            feature: AI feature to update
            status: New status
            reason: Optional reason for the change
        """
        with self._lock:
            if feature not in self._flags:
                logger.warning(f"Cannot set status for unknown AI feature: {feature}")
                return
            
            flag = self._flags[feature]
            old_status = flag.status
            flag.status = status
            flag.last_updated = datetime.utcnow()
            
            # Update enabled/degraded flags based on status
            if status == FlagStatus.ENABLED:
                flag.enabled = True
                flag.degraded = False
            elif status == FlagStatus.DISABLED:
                flag.enabled = False
                flag.degraded = False
            elif status == FlagStatus.DEGRADED:
                flag.enabled = True
                flag.degraded = True
            elif status in [FlagStatus.BUDGET_EXCEEDED, FlagStatus.RATE_LIMITED]:
                # These are temporary states, don't change enabled/degraded
                pass
            
            logger.info(f"AI feature {feature.value} status changed from {old_status.value} to {status.value}" + 
                       (f" - {reason}" if reason else ""))
    
    def enable_feature(self, feature: AIFeature, reason: Optional[str] = None):
        """Enable an AI feature."""
        self.set_feature_status(feature, FlagStatus.ENABLED, reason)
    
    def disable_feature(self, feature: AIFeature, reason: Optional[str] = None):
        """Disable an AI feature."""
        self.set_feature_status(feature, FlagStatus.DISABLED, reason)
    
    def degrade_feature(self, feature: AIFeature, reason: Optional[str] = None):
        """Degrade an AI feature (use fallback only)."""
        self.set_feature_status(feature, FlagStatus.DEGRADED, reason)
    
    def get_all_flags(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all feature flags.
        
        Returns:
            Dictionary of all feature flags
        """
        with self._lock:
            result = {}
            for feature, flag in self._flags.items():
                result[feature.value] = {
                    'status': flag.status.value,
                    'enabled': flag.enabled,
                    'degraded': flag.degraded,
                    'fallback_available': flag.fallback_available,
                    'last_updated': flag.last_updated.isoformat(),
                    'metadata': flag.metadata
                }
            return result
    
    def get_enabled_features(self) -> List[AIFeature]:
        """
        Get list of enabled AI features.
        
        Returns:
            List of enabled features
        """
        with self._lock:
            return [feature for feature, flag in self._flags.items() if flag.enabled]
    
    def _is_llm_available(self) -> bool:
        """Check if LLM service is available."""
        try:
            from app.services.llm_service import LLMService
            llm_service = LLMService()
            providers = llm_service.get_available_providers()
            return len(providers) > 0
        except Exception as e:
            logger.warning(f"Failed to check LLM availability: {e}")
            return False
    
    def _check_budget_constraints(self, feature: AIFeature, user_id: Optional[str] = None) -> bool:
        """Check if feature usage is within budget constraints."""
        try:
            from app.ai.cost_monitor import ai_cost_monitor
            return ai_cost_monitor.check_feature_budget(feature, user_id)
        except Exception as e:
            logger.warning(f"Failed to check budget constraints: {e}")
            return True  # Allow if budget check fails
    
    def _check_rate_limits(self, feature: AIFeature, user_id: Optional[str] = None) -> bool:
        """Check if feature usage is within rate limits."""
        try:
            # Simple rate limit check based on feature metadata
            flag = self._flags[feature]
            flag.metadata.get('rate_limit_per_hour', 100)
            
            # In a real implementation, this would check actual usage
            # For now, just return True (rate limiting would be implemented with Redis)
            return True
        except Exception as e:
            logger.warning(f"Failed to check rate limits: {e}")
            return True  # Allow if rate limit check fails
    
    def update_flag_from_config(self, feature_name: str, config_value: Any):
        """
        Update a feature flag from configuration.
        
        Args:
            feature_name: Name of the feature
            config_value: Configuration value
        """
        try:
            feature = AIFeature(feature_name)
            if feature in self._flags:
                if isinstance(config_value, bool):
                    if config_value:
                        self.enable_feature(feature, "Configuration update")
                    else:
                        self.disable_feature(feature, "Configuration update")
                elif isinstance(config_value, str):
                    if config_value.lower() == 'degraded':
                        self.degrade_feature(feature, "Configuration update")
                    elif config_value.lower() == 'enabled':
                        self.enable_feature(feature, "Configuration update")
                    elif config_value.lower() == 'disabled':
                        self.disable_feature(feature, "Configuration update")
        except ValueError:
            logger.warning(f"Unknown AI feature in configuration: {feature_name}")

# Global AI feature flags instance
ai_feature_flags = AIFeatureFlags()
