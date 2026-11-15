"""Minimal ApplicationComponent model for fast-init / test contexts.

The full Application Layer model module is very large and includes relationship
mappings that require many other models to be imported before SQLAlchemy mapper
configuration succeeds.

For `APP_FAST_INIT=1` endpoint tests, we provide a lightweight model
with the core fields needed for CRUD testing.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy.orm import relationship, validates

from app import db
from app.models.mixins import TenantMixin

# Check if we're in fast-init mode
_FAST_INIT = os.getenv("APP_FAST_INIT", "0") == "1"

# Only define the fast-init model if we're not using the full model
if _FAST_INIT:

    class ApplicationComponent(TenantMixin, db.Model):
        # Fast-init twin of application_portfolio.py:80 -- that class carries
        # TenantMixin (no nullable override; application_components already
        # requires organization_id NOT NULL in production). Scope here is
        # tenancy only: OptimisticLockMixin's locking-behavior divergence
        # (investigation.md Task 3 note) is a separate, larger concern and is
        # deliberately not addressed by this change.
        __tablename__ = "application_components"
        __table_args__ = {"extend_existing": True}  # Allow table reuse

        # Core identification
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(255), nullable=False, index=True)
        description = db.Column(db.Text)
        component_type = db.Column(db.String(100))

        # Technology attributes
        primary_technology = db.Column(db.String(200))
        technology_stack = db.Column(db.Text)
        programming_languages = db.Column(db.Text)
        frameworks = db.Column(db.Text)
        database_technology = db.Column(db.String(200))
        integration_protocols = db.Column(db.Text)

        # Organizational attributes
        owner_team = db.Column(db.String(255))
        business_unit = db.Column(db.String(255))
        cost_center = db.Column(db.String(100))

        # Deployment attributes
        deployment_status = db.Column(db.String(50))
        deployment_environment = db.Column(db.String(100))
        hosting_model = db.Column(db.String(100))
        cloud_provider = db.Column(db.String(100))
        data_center_location = db.Column(db.String(255))

        # Compliance & Security
        compliance_requirements = db.Column(db.Text)
        security_classification = db.Column(db.String(50))
        data_classification = db.Column(db.String(50))
        encryption_at_rest = db.Column(db.Boolean, default=False)
        encryption_in_transit = db.Column(db.Boolean, default=False)

        # Lifecycle
        lifecycle_stage = db.Column(db.String(50))
        planned_retirement_date = db.Column(db.Date)
        last_major_update = db.Column(db.Date)

        # Performance & Scale
        transaction_volume = db.Column(db.String(50))
        user_count = db.Column(db.Integer)
        availability_requirement = db.Column(db.String(50))
        performance_sla = db.Column(db.String(255))

        # Financial
        annual_cost = db.Column(db.Numeric(15, 2))
        license_cost = db.Column(db.Numeric(15, 2))

        # Documentation
        documentation_url = db.Column(db.String(500))
        repository_url = db.Column(db.String(500))
        support_url = db.Column(db.String(500))

        # Additional attributes
        business_criticality = db.Column(db.String(50))
        disaster_recovery_tier = db.Column(db.String(50))
        backup_frequency = db.Column(db.String(50))
        monitoring_tools = db.Column(db.Text)
        notes = db.Column(db.Text)

        # Timestamps
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

        @validates("deployment_status")
        def validate_deployment_status(self, key, value):
            """Store only a status on the one list, as the full model does."""
            from app.services.application_status import status_to_store

            return status_to_store(self.deployment_status, value)

else:
    # Import the full model from the main module
    from .application_portfolio import ApplicationComponent
