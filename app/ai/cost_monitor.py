"""
AI Cost Monitor

Provides comprehensive cost monitoring and budget controls for AI features.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import threading

from app.models import APISettings

logger = logging.getLogger(__name__)

class CostUnit(Enum):
    """Cost measurement units."""
    REQUEST = "request"
    TOKEN = "token"
    CHARACTER = "character"
    MINUTE = "minute"

@dataclass
class CostRecord:
    """Represents a cost record."""
    id: str
    feature: str
    user_id: Optional[str]
    cost_amount: float
    cost_unit: CostUnit
    timestamp: datetime
    metadata: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert cost record to dictionary."""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['cost_unit'] = self.cost_unit.value
        return data

@dataclass
class BudgetAlert:
    """Represents a budget alert."""
    id: str
    user_id: Optional[str]
    budget_type: str
    current_usage: float
    budget_limit: float
    percentage_used: float
    timestamp: datetime
    message: str

class AICostMonitor:
    """
    Monitors AI feature costs and enforces budget controls.
    """
    
    def __init__(self):
        """Initialize the AI cost monitor."""
        self._cost_records = []  # In-memory storage (in production, use database)
        self._budgets = {}  # user_id -> budget_info
        self._usage_tracking = {}  # user_id -> feature -> usage_count
        self._lock = threading.Lock()
        
        # Initialize default budgets
        self._initialize_default_budgets()
        
        # Load budgets from database
        self._load_budgets_from_database()
    
    def _initialize_default_budgets(self):
        """Initialize default budget configurations."""
        self._budgets = {
            'global': {
                'daily_limit': 100.0,  # $100 per day
                'monthly_limit': 1000.0,  # $1000 per month
                'per_user_daily': 10.0,  # $10 per user per day
                'per_user_monthly': 100.0,  # $100 per user per month
                'alert_thresholds': [0.5, 0.8, 0.95]  # 50%, 80%, 95%
            }
        }
        
        # Feature-specific costs
        self._feature_costs = {
            'chat_interface': 0.01,
            'document_analysis': 0.05,
            'archimate_generation': 0.10,
            'gap_detection': 0.03,
            'duplicate_detection': 0.02,
            'vendor_discovery': 0.04,
            'suggestion_engine': 0.01,
            'workflow_automation': 0.08,
            'consolidation_analysis': 0.02,
            'capability_mapping': 0.03
        }
    
    def _load_budgets_from_database(self):
        """Load budget configurations from database."""
        try:
            # Get budget settings from database
            settings = APISettings.query.filter_by(category='ai_budgets').all()
            
            for setting in settings:
                try:
                    if setting.key.startswith('budget_'):
                        budget_type = setting.key[7:]  # Remove 'budget_' prefix
                        
                        if budget_type in self._budgets.get('global', {}):
                            self._budgets['global'][budget_type] = float(setting.value)
                            
                except Exception as e:
                    logger.warning(f"Failed to parse budget setting {setting.key}: {e}")
                    
        except Exception as e:
            logger.warning(f"Failed to load budgets from database: {e}")
    
    def record_cost(self, feature: str, cost_amount: float, cost_unit: CostUnit = CostUnit.REQUEST,
                   user_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
        """
        Record a cost for an AI feature.
        
        Args:
            feature: AI feature name
            cost_amount: Cost amount
            cost_unit: Cost measurement unit
            user_id: Optional user ID
            metadata: Additional metadata
        """
        cost_record = CostRecord(
            id=self._generate_cost_id(),
            feature=feature,
            user_id=user_id,
            cost_amount=cost_amount,
            cost_unit=cost_unit,
            timestamp=datetime.utcnow(),
            metadata=metadata or {}
        )
        
        with self._lock:
            self._cost_records.append(cost_record)
            
            # Update usage tracking
            if user_id:
                if user_id not in self._usage_tracking:
                    self._usage_tracking[user_id] = {}
                if feature not in self._usage_tracking[user_id]:
                    self._usage_tracking[user_id][feature] = 0
                self._usage_tracking[user_id][feature] += 1
            
            # Clean up old records (keep last 30 days)
            cutoff_time = datetime.utcnow() - timedelta(days=30)
            self._cost_records = [r for r in self._cost_records if r.timestamp > cutoff_time]
        
        # Check budget alerts
        self._check_budget_alerts(user_id)
        
        logger.debug(f"Recorded cost: ${cost_amount:.4f} for {feature} (user: {user_id})")
    
    def check_feature_budget(self, feature: str, user_id: Optional[str] = None) -> bool:
        """
        Check if a feature is within budget constraints.
        
        Args:
            feature: AI feature name
            user_id: Optional user ID
            
        Returns:
            True if within budget, False otherwise
        """
        try:
            # Get feature cost
            feature_cost = self._feature_costs.get(feature, 0.01)
            
            # Check global budgets
            if not self._check_global_budget(feature_cost):
                return False
            
            # Check user-specific budgets
            if user_id and not self._check_user_budget(user_id, feature_cost):
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to check feature budget: {e}")
            return True  # Allow if check fails
    
    def _check_global_budget(self, cost_amount: float) -> bool:
        """Check global budget constraints."""
        global_budget = self._budgets.get('global', {})
        
        # Check daily limit
        daily_limit = global_budget.get('daily_limit', 100.0)
        daily_usage = self._get_usage_since('global', timedelta(days=1))
        
        if daily_usage + cost_amount > daily_limit:
            logger.warning(f"Global daily budget exceeded: ${daily_usage:.2f} + ${cost_amount:.2f} > ${daily_limit:.2f}")
            return False
        
        # Check monthly limit
        monthly_limit = global_budget.get('monthly_limit', 1000.0)
        monthly_usage = self._get_usage_since('global', timedelta(days=30))
        
        if monthly_usage + cost_amount > monthly_limit:
            logger.warning(f"Global monthly budget exceeded: ${monthly_usage:.2f} + ${cost_amount:.2f} > ${monthly_limit:.2f}")
            return False
        
        return True
    
    def _check_user_budget(self, user_id: str, cost_amount: float) -> bool:
        """Check user-specific budget constraints."""
        global_budget = self._budgets.get('global', {})
        
        # Check per-user daily limit
        daily_limit = global_budget.get('per_user_daily', 10.0)
        daily_usage = self._get_usage_since(user_id, timedelta(days=1))
        
        if daily_usage + cost_amount > daily_limit:
            logger.warning(f"User {user_id} daily budget exceeded: ${daily_usage:.2f} + ${cost_amount:.2f} > ${daily_limit:.2f}")
            return False
        
        # Check per-user monthly limit
        monthly_limit = global_budget.get('per_user_monthly', 100.0)
        monthly_usage = self._get_usage_since(user_id, timedelta(days=30))
        
        if monthly_usage + cost_amount > monthly_limit:
            logger.warning(f"User {user_id} monthly budget exceeded: ${monthly_usage:.2f} + ${cost_amount:.2f} > ${monthly_limit:.2f}")
            return False
        
        return True
    
    def _get_usage_since(self, entity_id: str, time_delta: timedelta) -> float:
        """Get usage for an entity since a given time."""
        cutoff_time = datetime.utcnow() - time_delta
        
        with self._lock:
            if entity_id == 'global':
                # Global usage
                relevant_records = [r for r in self._cost_records if r.timestamp > cutoff_time]
            else:
                # User-specific usage
                relevant_records = [r for r in self._cost_records 
                                 if r.user_id == entity_id and r.timestamp > cutoff_time]
            
            return sum(r.cost_amount for r in relevant_records)
    
    def _check_budget_alerts(self, user_id: Optional[str] = None):
        """Check and trigger budget alerts."""
        try:
            global_budget = self._budgets.get('global', {})
            alert_thresholds = global_budget.get('alert_thresholds', [0.5, 0.8, 0.95])
            
            # Check global alerts
            self._check_entity_alerts('global', alert_thresholds)
            
            # Check user alerts
            if user_id:
                self._check_entity_alerts(user_id, alert_thresholds)
                
        except Exception as e:
            logger.error(f"Failed to check budget alerts: {e}")
    
    def _check_entity_alerts(self, entity_id: str, alert_thresholds: List[float]):
        """Check alerts for a specific entity."""
        global_budget = self._budgets.get('global', {})
        
        # Check daily usage
        daily_limit = global_budget.get('per_user_daily' if entity_id != 'global' else 'daily_limit', 10.0)
        daily_usage = self._get_usage_since(entity_id, timedelta(days=1))
        daily_percentage = daily_usage / daily_limit if daily_limit > 0 else 0
        
        # Check monthly usage
        monthly_limit = global_budget.get('per_user_monthly' if entity_id != 'global' else 'monthly_limit', 100.0)
        monthly_usage = self._get_usage_since(entity_id, timedelta(days=30))
        monthly_percentage = monthly_usage / monthly_limit if monthly_limit > 0 else 0
        
        # Trigger alerts for thresholds
        for threshold in alert_thresholds:
            if daily_percentage >= threshold or monthly_percentage >= threshold:
                self._trigger_budget_alert(entity_id, 'daily' if daily_percentage >= threshold else 'monthly',
                                        daily_usage if daily_percentage >= threshold else monthly_usage,
                                        daily_limit if daily_percentage >= threshold else monthly_limit,
                                        max(daily_percentage, monthly_percentage))
    
    def _trigger_budget_alert(self, entity_id: str, budget_type: str, current_usage: float, 
                            budget_limit: float, percentage_used: float):
        """Trigger a budget alert."""
        try:
            from app.monitoring.alerting_service import alerting_service, AlertSeverity
            
            (alerting_service.create_manual_alert(
                name=f"budget_alert_{budget_type}",
                severity=AlertSeverity.WARNING if percentage_used < 0.95 else AlertSeverity.CRITICAL,
                message=f"Budget alert: {entity_id} has used {percentage_used:.1%} of {budget_type} budget (${current_usage:.2f}/${budget_limit:.2f})",
                source='ai_cost_monitor',
                metadata={
                    'entity_id': entity_id,
                    'budget_type': budget_type,
                    'current_usage': current_usage,
                    'budget_limit': budget_limit,
                    'percentage_used': percentage_used
                }
            ))
            
            logger.warning(f"Budget alert triggered for {entity_id}: {percentage_used:.1%} of {budget_type} budget used")
            
        except Exception as e:
            logger.error(f"Failed to trigger budget alert: {e}")
    
    def get_cost_summary(self, time_delta: timedelta = timedelta(days=1)) -> Dict[str, Any]:
        """
        Get cost summary for a time period.
        
        Args:
            time_delta: Time period to analyze
            
        Returns:
            Cost summary statistics
        """
        cutoff_time = datetime.utcnow() - time_delta
        
        with self._lock:
            relevant_records = [r for r in self._cost_records if r.timestamp > cutoff_time]
        
        # Calculate totals
        total_cost = sum(r.cost_amount for r in relevant_records)
        total_requests = len(relevant_records)
        
        # Cost by feature
        feature_costs = {}
        for record in relevant_records:
            feature_costs[record.feature] = feature_costs.get(record.feature, 0) + record.cost_amount
        
        # Cost by user
        user_costs = {}
        for record in relevant_records:
            if record.user_id:
                user_costs[record.user_id] = user_costs.get(record.user_id, 0) + record.cost_amount
        
        # Top users
        top_users = sorted(user_costs.items(), key=lambda x: x[1], reverse=True)[:10]
        
        return {
            'time_period': f"{time_delta.days} days",
            'total_cost': total_cost,
            'total_requests': total_requests,
            'average_cost_per_request': total_cost / total_requests if total_requests > 0 else 0,
            'cost_by_feature': feature_costs,
            'cost_by_user': user_costs,
            'top_users': [{'user': user, 'cost': cost} for user, cost in top_users],
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def get_user_usage(self, user_id: str, time_delta: timedelta = timedelta(days=30)) -> Dict[str, Any]:
        """
        Get usage statistics for a specific user.
        
        Args:
            user_id: User ID to analyze
            time_delta: Time period to analyze
            
        Returns:
            User usage statistics
        """
        cutoff_time = datetime.utcnow() - time_delta
        
        with self._lock:
            relevant_records = [r for r in self._cost_records 
                             if r.user_id == user_id and r.timestamp > cutoff_time]
        
        # Calculate totals
        total_cost = sum(r.cost_amount for r in relevant_records)
        total_requests = len(relevant_records)
        
        # Usage by feature
        feature_usage = {}
        for record in relevant_records:
            feature_usage[record.feature] = feature_usage.get(record.feature, 0) + 1
        
        return {
            'user_id': user_id,
            'time_period': f"{time_delta.days} days",
            'total_cost': total_cost,
            'total_requests': total_requests,
            'average_cost_per_request': total_cost / total_requests if total_requests > 0 else 0,
            'usage_by_feature': feature_usage,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def _generate_cost_id(self) -> str:
        """Generate unique cost record ID."""
        timestamp = str(int(datetime.utcnow().timestamp()))
        data = f"cost_{timestamp}_{threading.get_ident()}"
        return data
    
    def set_budget_limit(self, budget_type: str, limit: float):
        """
        Set a budget limit.
        
        Args:
            budget_type: Budget type (daily_limit, monthly_limit, etc.)
            limit: Budget limit amount
        """
        with self._lock:
            if 'global' not in self._budgets:
                self._budgets['global'] = {}
            
            self._budgets['global'][budget_type] = limit
            
            logger.info(f"Budget limit set: {budget_type} = ${limit:.2f}")
    
    def get_budget_status(self, entity_id: str = 'global') -> Dict[str, Any]:
        """
        Get current budget status for an entity.
        
        Args:
            entity_id: Entity ID (user ID or 'global')
            
        Returns:
            Budget status information
        """
        global_budget = self._budgets.get('global', {})
        
        # Get current usage
        daily_usage = self._get_usage_since(entity_id, timedelta(days=1))
        monthly_usage = self._get_usage_since(entity_id, timedelta(days=30))
        
        # Get limits
        daily_limit = global_budget.get('per_user_daily' if entity_id != 'global' else 'daily_limit', 10.0)
        monthly_limit = global_budget.get('per_user_monthly' if entity_id != 'global' else 'monthly_limit', 100.0)
        
        return {
            'entity_id': entity_id,
            'daily_usage': daily_usage,
            'daily_limit': daily_limit,
            'daily_percentage': daily_usage / daily_limit if daily_limit > 0 else 0,
            'monthly_usage': monthly_usage,
            'monthly_limit': monthly_limit,
            'monthly_percentage': monthly_usage / monthly_limit if monthly_limit > 0 else 0,
            'timestamp': datetime.utcnow().isoformat()
        }

# Global AI cost monitor instance
ai_cost_monitor = AICostMonitor()
