"""
Agentic Gap Implementation Models

Tracks agent execution history, configurations, and results for the agentic-gaps system.

The agentic-gaps page, its route module and its script were removed as a
retired surface with no buyer question behind it. These three tables stay:
each still exists in the database and this module is still imported by
app/modules/architecture/services/agentic_gap_implementation_service.py, so
dropping the tables is a separate, evidence-backed data decision, not a
navigation change.
"""

import json
from datetime import datetime
from typing import Any, Dict, Optional

from app import db
from app.models.user import User


class AgentExecutionHistory(db.Model):
    """
    Tracks execution history of agentic gap implementation agents.

    Provides audit trail, metrics, and enables rollback capabilities.
    """

    __tablename__ = "agent_execution_history"

    id = db.Column(db.Integer, primary_key=True)

    # Execution context
    architecture_id = db.Column(db.Integer, nullable=False, index=True)
    agent_name = db.Column(db.String(100), nullable=False, index=True)
    execution_type = db.Column(
        db.String(50), nullable=False
    )  # 'single', 'all', 'scheduled', 'auto'

    # Execution details
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)

    # Status and results
    status = db.Column(
        db.String(50), nullable=False, index=True
    )  # 'running', 'success', 'failed', 'partial'
    success = db.Column(db.Boolean, default=False, index=True)

    # Results data
    result_data = db.Column(db.Text)  # JSON: Full result from agent
    models_created = db.Column(db.Text)  # JSON: List of model names created
    services_created = db.Column(db.Text)  # JSON: List of service names created
    errors = db.Column(db.Text)  # JSON: List of error messages

    # Code generation (if applicable)
    code_generated = db.Column(db.Text)  # Generated code that needs review
    requires_review = db.Column(db.Boolean, default=False)
    reviewed = db.Column(db.Boolean, default=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Rollback support
    rollback_available = db.Column(db.Boolean, default=False)
    rolled_back = db.Column(db.Boolean, default=False)
    rolled_back_at = db.Column(db.DateTime, nullable=True)
    rolled_back_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # User tracking
    executed_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    executed_by = db.relationship("User", foreign_keys=[executed_by_id], backref="agent_executions")

    # Configuration used
    configuration = db.Column(db.Text)  # JSON: Agent configuration at time of execution

    # Metadata (renamed to avoid SQLAlchemy reserved name conflict)
    execution_metadata = db.Column(db.Text)  # JSON: Additional metadata
    notes = db.Column(db.Text)  # User notes/comments

    # Relationships
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_id])
    rollback_user = db.relationship("User", foreign_keys=[rolled_back_by_id])

    def get_result_data(self) -> Dict[str, Any]:
        """Parse result data JSON."""
        if self.result_data:
            try:
                return json.loads(self.result_data)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def get_models_created(self) -> list:
        """Parse models created JSON."""
        if self.models_created:
            try:
                return json.loads(self.models_created)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def get_errors(self) -> list:
        """Parse errors JSON."""
        if self.errors:
            try:
                return json.loads(self.errors)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def get_configuration(self) -> Dict[str, Any]:
        """Parse configuration JSON."""
        if self.configuration:
            try:
                return json.loads(self.configuration)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "architecture_id": self.architecture_id,
            "agent_name": self.agent_name,
            "execution_type": self.execution_type,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "success": self.success,
            "result_data": self.get_result_data(),
            "models_created": self.get_models_created(),
            "services_created": json.loads(self.services_created) if self.services_created else [],
            "errors": self.get_errors(),
            "requires_review": self.requires_review,
            "reviewed": self.reviewed,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewed_by_id": self.reviewed_by_id,
            "rollback_available": self.rollback_available,
            "rolled_back": self.rolled_back,
            "executed_by_id": self.executed_by_id,
            "executed_by_name": self.executed_by.username if self.executed_by else None,
            "configuration": self.get_configuration(),
            "notes": self.notes,
        }

    def __repr__(self):
        return f"<AgentExecutionHistory {self.id}: {self.agent_name} - {self.status}>"


class AgentConfiguration(db.Model):
    """
    Stores configuration for each agent type.

    Allows per-agent customization of LLM providers, generation preferences, etc.
    """

    __tablename__ = "agent_configurations"

    id = db.Column(db.Integer, primary_key=True)

    # Agent identification
    agent_name = db.Column(db.String(100), unique=True, nullable=False, index=True)

    # LLM configuration
    llm_provider = db.Column(
        db.String(50), default="huggingface"
    )  # huggingface, openai, claude, gemini
    llm_model = db.Column(db.String(100))  # Model name/ID
    temperature = db.Column(db.Float, default=0.7)
    max_tokens = db.Column(db.Integer, default=4000)

    # Generation preferences
    auto_generate = db.Column(db.Boolean, default=False)  # Auto-generate if models don't exist
    require_review = db.Column(db.Boolean, default=True)  # Require review before committing
    validate_models = db.Column(db.Boolean, default=True)  # Validate against ArchiMate

    # Execution preferences
    enabled = db.Column(db.Boolean, default=True)
    priority = db.Column(db.Integer, default=5)  # 1 - 10, higher = more important
    timeout_seconds = db.Column(db.Integer, default=300)  # Max execution time

    # Dependencies
    depends_on = db.Column(db.Text)  # JSON: List of agent names that must run first

    # Custom settings
    custom_settings = db.Column(db.Text)  # JSON: Agent-specific settings

    # Metadata
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def get_depends_on(self) -> list:
        """Parse dependencies JSON."""
        if self.depends_on:
            try:
                return json.loads(self.depends_on)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def get_custom_settings(self) -> Dict[str, Any]:
        """Parse custom settings JSON."""
        if self.custom_settings:
            try:
                return json.loads(self.custom_settings)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "agent_name": self.agent_name,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "auto_generate": self.auto_generate,
            "require_review": self.require_review,
            "validate_models": self.validate_models,
            "enabled": self.enabled,
            "priority": self.priority,
            "timeout_seconds": self.timeout_seconds,
            "depends_on": self.get_depends_on(),
            "custom_settings": self.get_custom_settings(),
            "description": self.description,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<AgentConfiguration {self.agent_name}>"


class AgentSchedule(db.Model):
    """
    Schedules agent execution for automation.

    Supports daily, weekly, monthly schedules and event-driven triggers.
    """

    __tablename__ = "agent_schedules"

    id = db.Column(db.Integer, primary_key=True)

    # Schedule identification
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)

    # Agent selection
    agent_name = db.Column(db.String(100), nullable=True)  # None = all agents
    architecture_id = db.Column(db.Integer, nullable=False, index=True)

    # Schedule type
    schedule_type = db.Column(
        db.String(50), nullable=False
    )  # 'daily', 'weekly', 'monthly', 'event'
    schedule_config = db.Column(db.Text)  # JSON: Schedule-specific configuration

    # Event triggers (if schedule_type = 'event')
    trigger_event = db.Column(db.String(100))  # 'application_created', 'gap_discovered', etc.
    trigger_conditions = db.Column(db.Text)  # JSON: Conditions for triggering

    # Status
    enabled = db.Column(db.Boolean, default=True, index=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    next_run_at = db.Column(db.DateTime, nullable=True, index=True)

    # Notification
    notify_on_completion = db.Column(db.Boolean, default=False)
    notify_emails = db.Column(db.Text)  # JSON: List of email addresses

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    def get_schedule_config(self) -> Dict[str, Any]:
        """Parse schedule configuration JSON."""
        if self.schedule_config:
            try:
                return json.loads(self.schedule_config)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def get_trigger_conditions(self) -> Dict[str, Any]:
        """Parse trigger conditions JSON."""
        if self.trigger_conditions:
            try:
                return json.loads(self.trigger_conditions)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def get_notify_emails(self) -> list:
        """Parse notification emails JSON."""
        if self.notify_emails:
            try:
                return json.loads(self.notify_emails)
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_name": self.agent_name,
            "architecture_id": self.architecture_id,
            "schedule_type": self.schedule_type,
            "schedule_config": self.get_schedule_config(),
            "trigger_event": self.trigger_event,
            "trigger_conditions": self.get_trigger_conditions(),
            "enabled": self.enabled,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "notify_on_completion": self.notify_on_completion,
            "notify_emails": self.get_notify_emails(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<AgentSchedule {self.name} ({self.schedule_type})>"
