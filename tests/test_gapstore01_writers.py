"""Unit tests for gapstore01: each writer creates a gap in the register, not in legacy tables."""

import uuid
import pytest


class TestCapabilityRoadmapDashboardWriter:
    """Writer 1: capability_roadmap_dashboard_service.generate_work_packages_from_gaps"""

    def test_creates_gap_in_register_not_roadmap_gaps(
        self, app, db_session, make_org
    ):
        from app.models.capability_gap_analysis import (
            CapabilityGapAnalysis,
            CapabilityGapDetail,
        )
        from app.models.unified_capability import UnifiedCapability
        from app.models.roadmap_models import RoadmapGap
        from app.models.implementation_migration import Gap
        from app.models.user import User
        from app.modules.capabilities.services.capability_roadmap_dashboard_service import (
            CapabilityRoadmapDashboardService,
        )

        org = make_org("writer1")
        capability = UnifiedCapability(name="Test Capability", organization_id=org.id)
        db_session.add(capability)
        db_session.flush()

        user = User(email="test@example.com", first_name="Test", last_name="User",
                    organization_id=org.id, confirmed=True)
        user.password = "testpass"
        db_session.add(user)
        db_session.flush()

        gap_analysis = CapabilityGapAnalysis(
            analysis_name="Test Analysis", organization_id=org.id
        )
        db_session.add(gap_analysis)
        db_session.flush()

        gap_detail = CapabilityGapDetail(
            analysis_id=gap_analysis.id,
            capability_id=capability.id,
            gap_description="Test gap description",
            gap_severity="high",
            priority_score=80,
            business_impact_score=90,
            estimated_cost=10000,
            coverage_status="gap",
        )
        db_session.add(gap_detail)
        db_session.commit()

        # Call the writer
        service = CapabilityRoadmapDashboardService()
        result = service.generate_work_packages_from_gaps(
            gap_analysis_id=gap_analysis.id, created_by=user.id, auto_approve=True
        )

        # Assert: gap created in register (gaps table) with caller's org
        register_gaps = Gap.query.filter_by(organization_id=org.id).all()
        assert len(register_gaps) == 1
        assert register_gaps[0].name == "Close Gap: Test Capability"
        assert register_gaps[0].organization_id == org.id
        assert register_gaps[0].gap_type == "capability"
        assert register_gaps[0].generation_source == "capability_roadmap_dashboard"

        # Assert: NO row in legacy roadmap_gaps table
        legacy_gaps = RoadmapGap.query.all()
        assert len(legacy_gaps) == 0, f"Expected 0 legacy RoadmapGap rows, got {len(legacy_gaps)}"

        # Assert: work package was created
        assert result["success"] is True
        assert result["work_packages_created"] == 1


class TestCapabilityGapServiceWriter:
    """Writer 2: capability_gap_service.perform_compliance_rollup"""

    def test_creates_gap_in_register_not_compliance_gaps(
        self, app, db_session, make_org
    ):
        from app.models import BusinessCapability, ComplianceRequirement, ComplianceControl, RegulatoryFramework
        from app.models.application_portfolio import ApplicationComponent
        from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
        from app.models.unified_capability import UnifiedCapability
        from app.models.compliance_models import ComplianceGap
        from app.models.implementation_migration import Gap
        from app.modules.capabilities.services.capability_gap_service import (
            CapabilityGapAnalysisService,
        )

        org = make_org("writer2")
        
        # Create UnifiedCapability first (needed for compliance validation mapping)
        unified_cap = UnifiedCapability(name="Test Capability", organization_id=org.id)
        db_session.add(unified_cap)
        db_session.flush()
        
        # Create BusinessCapability with same ID (compliance validation uses BusinessCapability ID to query UnifiedApplicationCapabilityMapping)
        capability = BusinessCapability(
            id=unified_cap.id,
            name="Test Capability", organization_id=org.id, current_maturity_level=2, target_maturity_level=4,
            deprecated_in_favor_of_id=unified_cap.id
        )
        db_session.add(capability)
        db_session.flush()

        framework = RegulatoryFramework(code=f"TEST-{uuid.uuid4().hex[:6]}", name="Test Framework")
        db_session.add(framework)
        db_session.flush()

        control = ComplianceControl(
            control_code="CTRL-001", title="Test Control", priority="critical", framework_id=framework.id
        )
        db_session.add(control)
        db_session.flush()

        requirement = ComplianceRequirement(
            title="Test Requirement",
            description="Test requirement description",
            requirement_type="regulatory",
            control_id=control.id,
            applies_to_capability_id=capability.id,
            implementation_status="not_started",
            organization_id=org.id,
        )
        db_session.add(requirement)
        db_session.flush()

        # Link requirement to capability via many-to-many relationship (used by compliance validation)
        capability.compliance_requirements.append(requirement)
        db_session.flush()

        # Add an application that supports the capability (needed for compliance validation)
        application = ApplicationComponent(name="Test App", organization_id=org.id, component_type="application")
        db_session.add(application)
        db_session.flush()

        mapping = UnifiedApplicationCapabilityMapping(
            unified_capability_id=unified_cap.id,
            application_component_id=application.id,
            support_level="partial",
        )
        db_session.add(mapping)
        db_session.commit()

        # Call the writer
        service = CapabilityGapAnalysisService()
        result = service.perform_compliance_rollup(capability_id=capability.id)

        # Assert: gap created in register (gaps table) with caller's org
        register_gaps = Gap.query.filter_by(organization_id=org.id).all()
        assert len(register_gaps) == 1
        assert "Compliance Gap" in register_gaps[0].name
        assert register_gaps[0].organization_id == org.id
        assert register_gaps[0].gap_type == "compliance"
        assert register_gaps[0].generation_source == "capability_gap_service"

        # Assert: NO row in legacy compliance_gaps table
        legacy_gaps = ComplianceGap.query.all()
        assert len(legacy_gaps) == 0, f"Expected 0 legacy ComplianceGap rows, got {len(legacy_gaps)}"

        # Assert: result indicates gaps created
        assert result["status"] == "success"
        assert result["gaps_created"] == 1


class TestComplianceCheckerServiceWriter:
    """Writer 3: compliance_checker_service.save_gaps_to_database"""

    def test_creates_gap_in_register_not_compliance_gaps(
        self, app, db_session, make_org
    ):
        from app.models import ArchitectureModel
        from app.models.compliance_models import ComplianceGap
        from app.models.implementation_migration import Gap
        from app.services.compliance.compliance_checker_service import (
            ComplianceCheckerService,
        )

        org = make_org("writer3")
        architecture = ArchitectureModel(name="Test Architecture", organization_id=org.id)
        db_session.add(architecture)
        db_session.commit()

        # Create validation results with gaps
        gaps_data = [
            {
                "gap_type": "missing_requirement",
                "title": "Test Compliance Gap",
                "description": "Missing requirement for critical control",
                "risk_level": "critical",
                "impact_description": "Regulatory non-compliance risk",
                "remediation_action": "Create requirement",
                "status": "open",
            }
        ]

        # Call the writer
        service = ComplianceCheckerService()
        saved_count = service.save_gaps_to_database(architecture.id, gaps_data)

        # Assert: gap created in register (gaps table) with caller's org
        register_gaps = Gap.query.filter_by(organization_id=org.id).all()
        assert len(register_gaps) == 1
        assert register_gaps[0].name == "Test Compliance Gap"
        assert register_gaps[0].organization_id == org.id
        assert register_gaps[0].gap_type == "compliance"
        assert register_gaps[0].generation_source == "compliance_checker_service"

        # Assert: NO row in legacy compliance_gaps table
        legacy_gaps = ComplianceGap.query.all()
        assert len(legacy_gaps) == 0, f"Expected 0 legacy ComplianceGap rows, got {len(legacy_gaps)}"

        # Assert: saved count
        assert saved_count == 1