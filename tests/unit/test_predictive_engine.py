"""Unit tests for the Predictive Engine — five heuristic checks.

Each check has a positive test (alert fires) and a negative test (link present: no alert).
Tests use the seeded demo organisation plus fixture rows added in the test's own organisation.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.predictive_engine import (
    check_unfunded_objectives,
    check_zombie_projects,
    check_bus_factor,
    check_domino_effect,
    check_cost_to_serve,
    run_all_checks,
    BUS_FACTOR_THRESHOLD,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_user(db_session, org, email, role_name="Administrator"):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name=role_name).first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name=role_name).first()

    user = User(
        email=email,
        first_name="Test",
        last_name=email.split("@")[0].capitalize(),
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
    )
    user.password = "test"
    db_session.add(user)
    db_session.flush()
    return user


def _make_element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=name, type=type_, layer=layer, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_relationship(db_session, org_id, source_id, target_id, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source_id,
        target_id=target_id,
        type=type_,
        organization_id=org_id,
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _make_business_case(db_session, org_id, title, capability_id=None, strategic_initiative_id=None,
                         solution_id=None, financial_benefit_annual=None):
    from app.models.business_case import BusinessCase

    bc = BusinessCase(
        title=title,
        organization_id=org_id,
        capability_id=capability_id,
        strategic_initiative_id=strategic_initiative_id,
        solution_id=solution_id,
        financial_benefit_annual=financial_benefit_annual,
    )
    db_session.add(bc)
    db_session.flush()
    return bc


def _make_cost_fact(db_session, org_id, element_id, element_type, amount, period_start, period_end, kind="actual", currency="EUR", source="test"):
    from app.models.cost_fact import CostFact

    cf = CostFact(
        organization_id=org_id,
        element_id=element_id,
        element_type=element_type,
        amount=amount,
        period_start=period_start,
        period_end=period_end,
        kind=kind,
        currency=currency,
        source=f"{source}-{period_start.isoformat()}-{element_id}",
    )
    db_session.add(cf)
    db_session.flush()
    return cf


def _make_application_owner(db_session, org_id, user_id, element_type, element_id, ownership_type="primary"):
    from app.models.application_owner import ApplicationOwner

    owner = ApplicationOwner(
        organization_id=org_id,
        user_id=user_id,
        element_type=element_type,
        element_id=element_id,
        ownership_type=ownership_type,
    )
    db_session.add(owner)
    db_session.flush()
    return owner


class TestPredictiveEngine:
    """Predictive engine tests."""

    def test_unfunded_objective(self, db_session, make_org):
        """Test unfunded objective."""
        org = make_org("unfunded")
        goal = _make_element(db_session, org.id, "Goal", type_="Goal", layer="motivation")
        db_session.commit()

        alerts = check_unfunded_objectives(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "UNFUNDED_OBJECTIVE"

    def test_zombie_project(self, db_session, make_org):
        """Test zombie project."""
        org = make_org("zombie")
        from app.models.business_capabilities import BusinessCapability
        from app.models import ArchiMateElement

        cap_element = ArchiMateElement(name="Cap", type="Capability", layer="strategy", organization_id=org.id)
        db_session.add(cap_element)
        db_session.flush()

        capability = BusinessCapability(name="Cap", organization_id=org.id, archimate_element_id=cap_element.id)
        db_session.add(capability)
        db_session.flush()

        wp = _make_element(db_session, org.id, "WP", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org.id, wp.id, cap_element.id, type_="Realization")

        from app.models.strategic import StrategicInitiative
        initiative = StrategicInitiative(
            name="Zombie Initiative",
            organization_id=org.id,
            target_completion_date=date.today() - timedelta(days=200),
            status="in_progress",
        )
        db_session.add(initiative)
        db_session.flush()

        bc = _make_business_case(db_session, org.id, "Zombie BC", capability_id=capability.id, strategic_initiative_id=initiative.id, financial_benefit_annual=1000)
        app = _make_element(db_session, org.id, "App", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org.id, app.id, "application", 2000, date.today() - timedelta(days=365), date.today())
        db_session.commit()

        alerts = check_zombie_projects(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "ZOMBIE_PROJECT"

    def test_bus_factor(self, db_session, make_org):
        """Test bus factor."""
        org = make_org("busfactor")
        user = _make_user(db_session, org, "busfactor@example.com")

        for i in range(6):
            app = _make_element(db_session, org.id, f"App {i}", type_="ApplicationComponent", layer="application")
            from app.models.application_portfolio import ApplicationComponent
            ac = ApplicationComponent.query.get(app.id)
            if ac:
                ac.criticality = "critical"
            _make_application_owner(db_session, org.id, user.id, "application", app.id, "primary")

        db_session.commit()

        alerts = check_bus_factor(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "BUS_FACTOR"

    def test_domino_effect(self, db_session, make_org):
        """Test domino effect."""
        org = make_org("domino")
        internet_iface = _make_element(db_session, org.id, "API", type_="ApplicationInterface", layer="application")
        internet_iface.acm_properties = {"exposure": "internet"}
        db_session.add(internet_iface)
        data_obj = _make_element(db_session, org.id, "PII Data", type_="DataObject", layer="application")
        data_obj.acm_properties = {"classification": "personal data"}
        db_session.add(data_obj)
        _make_relationship(db_session, org.id, internet_iface.id, data_obj.id, type_="Flow")
        db_session.commit()

        alerts = check_domino_effect(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "DOMINO_EFFECT"

    def test_cost_to_serve(self, db_session, make_org):
        """Test cost to serve."""
        org = make_org("costtoserve")
        svc = _make_element(db_session, org.id, "Svc", type_="ApplicationService", layer="application")
        app2 = _make_element(db_session, org.id, "App2", type_="ApplicationComponent", layer="application")
        _make_relationship(db_session, org.id, app2.id, svc.id, type_="Realization")
        _make_cost_fact(db_session, org.id, app2.id, "application", 100000, date.today() - timedelta(days=730), date.today() - timedelta(days=365))
        _make_cost_fact(db_session, org.id, app2.id, "application", 120000, date.today() - timedelta(days=365), date.today())
        db_session.commit()

        alerts = check_cost_to_serve(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "COST_TO_SERVE"

    def test_organisation_isolation(self, db_session, make_org):
        """Test organisation isolation."""
        org_a = make_org("iso-a")
        org_b = make_org("iso-b")

        # Create zombie project in org B
        from app.models.business_capabilities import BusinessCapability
        from app.models import ArchiMateElement

        cap_element_b = ArchiMateElement(name="Cap B", type="Capability", layer="strategy", organization_id=org_b.id)
        db_session.add(cap_element_b)
        db_session.flush()

        cap_b = BusinessCapability(name="Cap B", organization_id=org_b.id, archimate_element_id=cap_element_b.id)
        db_session.add(cap_b)
        db_session.flush()

        wp_b = _make_element(db_session, org_b.id, "WP B", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org_b.id, wp_b.id, cap_element_b.id, type_="Realization")

        from app.models.strategic import StrategicInitiative
        initiative_b = StrategicInitiative(
            name="Zombie Initiative B",
            organization_id=org_b.id,
            target_completion_date=date.today() - timedelta(days=200),
            status="in_progress",
        )
        db_session.add(initiative_b)
        db_session.flush()

        bc_b = _make_business_case(db_session, org_b.id, "Zombie B", capability_id=cap_b.id, strategic_initiative_id=initiative_b.id, financial_benefit_annual=1000)
        app_b = _make_element(db_session, org_b.id, "App B", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org_b.id, app_b.id, "application", 2000, date.today() - timedelta(days=365), date.today())

        # Create healthy project in org A
        cap_element_a = ArchiMateElement(name="Cap A", type="Capability", layer="strategy", organization_id=org_a.id)
        db_session.add(cap_element_a)
        db_session.flush()

        cap_a = BusinessCapability(name="Cap A", organization_id=org_a.id, archimate_element_id=cap_element_a.id)
        db_session.add(cap_a)
        db_session.flush()

        wp_a = _make_element(db_session, org_a.id, "WP A", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org_a.id, wp_a.id, cap_element_a.id, type_="Realization")

        initiative_a = StrategicInitiative(
            name="Healthy Initiative A",
            organization_id=org_a.id,
            target_completion_date=date.today() + timedelta(days=180),
            status="in_progress",
        )
        db_session.add(initiative_a)
        db_session.flush()

        bc_a = _make_business_case(db_session, org_a.id, "Healthy A", capability_id=cap_a.id, strategic_initiative_id=initiative_a.id, financial_benefit_annual=5000)
        app_a = _make_element(db_session, org_a.id, "App A", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org_a.id, app_a.id, "application", 1000, date.today() - timedelta(days=365), date.today())

        db_session.commit()

        # Run checks for org A
        alerts_a = run_all_checks(org_a.id, topic="financial")

        # Should only see org A's elements (healthy -> no zombie alert)
        zombie_alerts = [a for a in alerts_a if a.prediction_type == "ZOMBIE_PROJECT"]
        assert len(zombie_alerts) == 0

        # Run checks for org B
        alerts_b = run_all_checks(org_b.id, topic="financial")

        # Should see org B's zombie project
        zombie_alerts_b = [a for a in alerts_b if a.prediction_type == "ZOMBIE_PROJECT"]
        assert len(zombie_alerts_b) == 1


# --------------------------------------------------------------------------- #
# Check 1: Unfunded Objective
# --------------------------------------------------------------------------- #


class TestUnfundedObjectives:
    """Check 1: Unfunded Objective — goal/outcome/objective with no delivery mechanism."""

    def test_alert_when_no_capability_link(self, db_session, make_org):
        """Objective with no capability link -> UNFUNDED_OBJECTIVE."""
        org = make_org("unfunded-1")
        # Create a Goal (motivation layer) with no relationships
        goal = _make_element(db_session, org.id, "Revenue Growth Goal", type_="Goal", layer="motivation")
        db_session.commit()

        alerts = check_unfunded_objectives(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "UNFUNDED_OBJECTIVE"
        assert alert.severity in ("LOW", "MEDIUM")  # non-critical, missing link
        assert "Revenue Growth Goal" in alert.target_element
        assert len(alert.evidence) >= 1
        assert "has no capability link mapped" in alert.evidence[0]
        assert "Link objective" in alert.recommendation

    def test_alert_when_capability_has_no_work_package_or_app(self, db_session, make_org):
        """Objective -> capability, but capability has no work package or application -> UNFUNDED_OBJECTIVE."""
        org = make_org("unfunded-2")
        goal = _make_element(db_session, org.id, "Customer Satisfaction Goal", type_="Goal", layer="motivation")
        capability = _make_element(db_session, org.id, "Customer Service Capability", type_="Capability", layer="strategy")
        _make_relationship(db_session, org.id, goal.id, capability.id, type_="Realization")
        db_session.commit()

        alerts = check_unfunded_objectives(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "UNFUNDED_OBJECTIVE"
        assert "Customer Satisfaction Goal" in alert.target_element
        assert len(alert.evidence) >= 2
        assert "Realization" in alert.evidence[0]
        assert "has no work package or application link mapped" in alert.evidence[1]
        assert "Map work packages or applications" in alert.recommendation

    def test_no_alert_when_delivery_mechanism_exists(self, db_session, make_org):
        """Objective -> capability -> work package: no alert."""
        org = make_org("unfunded-3")
        goal = _make_element(db_session, org.id, "Digital Transformation Goal", type_="Goal", layer="motivation")
        capability = _make_element(db_session, org.id, "Digital Capability", type_="Capability", layer="strategy")
        wp = _make_element(db_session, org.id, "Digital WP", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org.id, goal.id, capability.id, type_="Realization")
        _make_relationship(db_session, org.id, wp.id, capability.id, type_="Realization")
        db_session.commit()

        alerts = check_unfunded_objectives(org.id)

        # Should not alert for this goal
        goal_alerts = [a for a in alerts if "Digital Transformation Goal" in a.target_element]
        assert len(goal_alerts) == 0


# --------------------------------------------------------------------------- #
# Check 2: Zombie Project
# --------------------------------------------------------------------------- #


class TestZombieProjects:
    """Check 2: Zombie Project — cost > benefit or >6 months past end date."""

    def test_alert_when_cost_exceeds_benefit(self, db_session, make_org):
        """Business case with actual cost > forecast benefit -> ZOMBIE_PROJECT."""
        org = make_org("zombie-1")
        from app.models.business_capabilities import BusinessCapability
        from app.models import ArchiMateElement
        
        # Create ArchiMateElement for the capability
        cap_element = ArchiMateElement(
            name="Core Banking Capability", type="Capability", layer="strategy", organization_id=org.id
        )
        db_session.add(cap_element)
        db_session.flush()
        
        # Create BusinessCapability linked to the ArchiMateElement
        capability = BusinessCapability(name="Core Banking Capability", organization_id=org.id, archimate_element_id=cap_element.id)
        db_session.add(capability)
        db_session.flush()
        
        wp = _make_element(db_session, org.id, "Core Banking WP", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org.id, wp.id, cap_element.id, type_="Realization")

        bc = _make_business_case(
            db_session, org.id, "Core Banking Modernisation",
            capability_id=capability.id,
            financial_benefit_annual=100000,  # 100k benefit
        )
        # Add cost fact for the work package (mapped to application)
        app = _make_element(db_session, org.id, "Core Banking App", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org.id, app.id, "application", 150000,  # 150k cost > 100k benefit
                        date.today() - timedelta(days=365), date.today())
        db_session.commit()

        alerts = check_zombie_projects(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "ZOMBIE_PROJECT"
        assert alert.severity == "HIGH"
        assert "Core Banking Modernisation" in alert.target_element
        assert len(alert.evidence) >= 2
        assert "exceeds forecast benefit" in alert.evidence[0] or "exceeds forecast benefit" in alert.evidence[1]
        assert "Review business case" in alert.recommendation

    def test_alert_when_past_end_date(self, db_session, make_org):
        """Business case >6 months past end date -> ZOMBIE_PROJECT."""
        org = make_org("zombie-2")
        past_date = date.today() - timedelta(days=200)  # > 180 days
        
        from app.models.business_capabilities import BusinessCapability
        from app.models import ArchiMateElement
        
        # Create ArchiMateElement for the capability
        cap_element = ArchiMateElement(
            name="Legacy Migration Capability", type="Capability", layer="strategy", organization_id=org.id
        )
        db_session.add(cap_element)
        db_session.flush()
        
        # Create BusinessCapability linked to the ArchiMateElement
        capability = BusinessCapability(name="Legacy Migration Capability", organization_id=org.id, archimate_element_id=cap_element.id)
        db_session.add(capability)
        db_session.flush()
        
        # Create a StrategicInitiative with past end date
        from app.models.strategic import StrategicInitiative
        initiative = StrategicInitiative(
            name="Legacy Migration Initiative",
            organization_id=org.id,
            target_completion_date=past_date,
            status="in_progress",
        )
        db_session.add(initiative)
        db_session.flush()
        
        bc = _make_business_case(
            db_session, org.id, "Legacy Migration",
            capability_id=capability.id,
            strategic_initiative_id=initiative.id,
            financial_benefit_annual=500000,
        )
        db_session.commit()

        alerts = check_zombie_projects(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "ZOMBIE_PROJECT"
        assert alert.severity == "HIGH"
        assert "Legacy Migration" in alert.target_element
        assert "more than 6 months past" in alert.recommendation or "past end date" in alert.evidence[0]

    def test_no_alert_when_healthy(self, db_session, make_org):
        """Business case with cost < benefit and not past end date -> no alert."""
        org = make_org("zombie-3")
        from app.models.business_capabilities import BusinessCapability
        from app.models import ArchiMateElement
        
        # Create ArchiMateElement for the capability
        cap_element = ArchiMateElement(
            name="CRM Capability", type="Capability", layer="strategy", organization_id=org.id
        )
        db_session.add(cap_element)
        db_session.flush()
        
        # Create BusinessCapability linked to the ArchiMateElement
        capability = BusinessCapability(name="CRM Capability", organization_id=org.id, archimate_element_id=cap_element.id)
        db_session.add(capability)
        db_session.flush()
        
        wp = _make_element(db_session, org.id, "CRM WP", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org.id, wp.id, cap_element.id, type_="Realization")

        future_date = date.today() + timedelta(days=180)
        from app.models.strategic import StrategicInitiative
        initiative = StrategicInitiative(
            name="CRM Upgrade Initiative",
            organization_id=org.id,
            target_completion_date=future_date,
            status="in_progress",
        )
        db_session.add(initiative)
        db_session.flush()
        
        bc = _make_business_case(
            db_session, org.id, "CRM Upgrade",
            capability_id=capability.id,
            strategic_initiative_id=initiative.id,
            financial_benefit_annual=200000,
        )
        app = _make_element(db_session, org.id, "CRM App", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org.id, app.id, "application", 50000,  # 50k cost < 200k benefit
                        date.today() - timedelta(days=365), date.today())
        db_session.commit()

        alerts = check_zombie_projects(org.id)

        bc_alerts = [a for a in alerts if "CRM Upgrade" in a.target_element]
        assert len(bc_alerts) == 0


# --------------------------------------------------------------------------- #
# Check 3: Bus Factor
# --------------------------------------------------------------------------- #


class TestBusFactor:
    """Check 3: Bus Factor — one person owning >X critical elements."""

    def test_alert_when_person_owns_many_critical_elements(self, db_session, make_org):
        """Person owning >5 critical elements -> BUS_FACTOR."""
        org = make_org("busfactor-1")
        user = _make_user(db_session, org, "busfactor1@example.com")

        # Create 6 critical applications
        for i in range(6):
            app = _make_element(db_session, org.id, f"Critical App {i}", type_="ApplicationComponent", layer="application")
            # Set criticality to critical
            from app.models.application_portfolio import ApplicationComponent
            ac = ApplicationComponent.query.get(app.id)
            if ac:
                ac.criticality = "critical"
            _make_application_owner(db_session, org.id, user.id, "application", app.id, "primary")

        # Create 1 critical capability
        from app.models.unified_capability import UnifiedCapability
        cap = UnifiedCapability(name="Critical Capability", organization_id=org.id)
        db_session.add(cap)
        db_session.flush()
        _make_application_owner(db_session, org.id, user.id, "capability", cap.id, "primary")

        db_session.commit()

        alerts = check_bus_factor(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "BUS_FACTOR"
        assert alert.severity == "MEDIUM"
        assert user.last_name in alert.target_element
        assert "Single point of failure" in alert.target_element
        assert len(alert.evidence) >= 2
        assert f"owns {BUS_FACTOR_THRESHOLD + 1}" in alert.evidence[0] or f"owns 7" in alert.evidence[0]
        assert "Distribute ownership" in alert.recommendation

    def test_no_alert_when_below_threshold(self, db_session, make_org):
        """Person owning <=5 critical elements -> no alert."""
        org = make_org("busfactor-2")
        user = _make_user(db_session, org, "busfactor2@example.com")

        # Create 5 critical applications (at threshold)
        for i in range(5):
            app = _make_element(db_session, org.id, f"App {i}", type_="ApplicationComponent", layer="application")
            from app.models.application_portfolio import ApplicationComponent
            ac = ApplicationComponent.query.get(app.id)
            if ac:
                ac.criticality = "critical"
            _make_application_owner(db_session, org.id, user.id, "application", app.id, "primary")

        db_session.commit()

        alerts = check_bus_factor(org.id)

        user_alerts = [a for a in alerts if user.last_name in a.target_element]
        assert len(user_alerts) == 0


# --------------------------------------------------------------------------- #
# Check 4: Domino Effect
# --------------------------------------------------------------------------- #


class TestDominoEffect:
    """Check 4: Domino Effect — internet-facing path to PII/secret data with no security control."""

    def test_alert_when_path_to_pii_without_security_control(self, db_session, make_org):
        """Internet-facing node -> ... -> PII data object, no security control -> DOMINO_EFFECT."""
        org = make_org("domino-1")

        # Internet-facing application interface
        internet_iface = _make_element(db_session, org.id, "Public API Gateway", type_="ApplicationInterface", layer="application")
        internet_iface.acm_properties = {"exposure": "internet"}
        db_session.add(internet_iface)

        # Application service serving the interface
        app_service = _make_element(db_session, org.id, "Customer Service", type_="ApplicationService", layer="application")
        _make_relationship(db_session, org.id, internet_iface.id, app_service.id, type_="Serving")

        # Data object with PII classification
        data_obj = _make_element(db_session, org.id, "Customer PII Data", type_="DataObject", layer="application")
        data_obj.acm_properties = {"classification": "personal data"}
        db_session.add(data_obj)

        # Flow from service to data object
        _make_relationship(db_session, org.id, app_service.id, data_obj.id, type_="Flow")

        db_session.commit()

        alerts = check_domino_effect(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "DOMINO_EFFECT"
        assert alert.severity == "HIGH"
        assert "Customer PII Data" in alert.target_element
        assert len(alert.evidence) >= 3
        assert "Serving" in alert.evidence[0] or "Flow" in alert.evidence[1]
        assert "classification: personal data" in alert.evidence[-2]
        assert "No security control requirement found on path" in alert.evidence[-1]
        assert "Add security control requirements" in alert.recommendation

    def test_no_alert_when_security_control_on_path(self, db_session, make_org):
        """Path with security control requirement -> no alert."""
        org = make_org("domino-2")

        internet_iface = _make_element(db_session, org.id, "Public API", type_="ApplicationInterface", layer="application")
        internet_iface.acm_properties = {"exposure": "internet"}
        db_session.add(internet_iface)

        app_service = _make_element(db_session, org.id, "Secure Service", type_="ApplicationService", layer="application")
        _make_relationship(db_session, org.id, internet_iface.id, app_service.id, type_="Serving")

        data_obj = _make_element(db_session, org.id, "Secret Data", type_="DataObject", layer="application")
        data_obj.acm_properties = {"classification": "secret"}
        db_session.add(data_obj)

        _make_relationship(db_session, org.id, app_service.id, data_obj.id, type_="Flow")

        # Security control requirement associated with the flow
        sec_req = _make_element(db_session, org.id, "Encryption Control", type_="Requirement", layer="motivation")
        sec_req.acm_properties = {"specialisation": "security control"}
        db_session.add(sec_req)
        _make_relationship(db_session, org.id, data_obj.id, sec_req.id, type_="Association")

        db_session.commit()

        alerts = check_domino_effect(org.id)

        data_alerts = [a for a in alerts if "Secret Data" in a.target_element]
        assert len(data_alerts) == 0


# --------------------------------------------------------------------------- #
# Check 5: Cost to Serve
# --------------------------------------------------------------------------- #


class TestCostToServe:
    """Check 5: Cost to Serve — cost up >10% over two periods with flat WP count and revenue."""

    def test_alert_when_cost_increases_over_10_percent(self, db_session, make_org):
        """Service cost up >10% over two periods with flat WP count -> COST_TO_SERVE."""
        org = make_org("costtoserve-1")

        svc = _make_element(db_session, org.id, "Payment Service", type_="ApplicationService", layer="application")
        app1 = _make_element(db_session, org.id, "Payment App 1", type_="ApplicationComponent", layer="application")
        app2 = _make_element(db_session, org.id, "Payment App 2", type_="ApplicationComponent", layer="application")
        _make_relationship(db_session, org.id, app1.id, svc.id, type_="Realization")
        _make_relationship(db_session, org.id, app2.id, svc.id, type_="Realization")

        # Period 1 (older): 100k total
        period1_start = date.today() - timedelta(days=730)
        period1_end = date.today() - timedelta(days=365)
        _make_cost_fact(db_session, org.id, app1.id, "application", 60000, period1_start, period1_end)
        _make_cost_fact(db_session, org.id, app2.id, "application", 40000, period1_start, period1_end)

        # Period 2 (newer): 120k total (20% increase)
        period2_start = date.today() - timedelta(days=365)
        period2_end = date.today()
        _make_cost_fact(db_session, org.id, app1.id, "application", 72000, period2_start, period2_end)
        _make_cost_fact(db_session, org.id, app2.id, "application", 48000, period2_start, period2_end)

        # Work packages (flat count)
        wp1 = _make_element(db_session, org.id, "Payment WP 1", type_="WorkPackage", layer="implementation")
        wp2 = _make_element(db_session, org.id, "Payment WP 2", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org.id, wp1.id, app1.id, type_="Realization")
        _make_relationship(db_session, org.id, wp2.id, app2.id, type_="Realization")

        db_session.commit()

        alerts = check_cost_to_serve(org.id)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.prediction_type == "COST_TO_SERVE"
        assert alert.severity == "MEDIUM"
        assert "Payment Service" in alert.target_element
        assert len(alert.evidence) >= 4
        assert "Cost increase:" in alert.evidence[3]
        assert "20.0%" in alert.evidence[3] or "20%" in alert.evidence[3]
        assert "Investigate cost drivers" in alert.recommendation

    def test_no_alert_when_less_than_two_periods(self, db_session, make_org):
        """Only one period of cost data -> no alert (history missing)."""
        org = make_org("costtoserve-2")

        svc = _make_element(db_session, org.id, "Billing Service", type_="ApplicationService", layer="application")
        app = _make_element(db_session, org.id, "Billing App", type_="ApplicationComponent", layer="application")
        _make_relationship(db_session, org.id, app.id, svc.id, type_="Realization")

        # Only one period
        period_start = date.today() - timedelta(days=365)
        period_end = date.today()
        _make_cost_fact(db_session, org.id, app.id, "application", 100000, period_start, period_end)

        db_session.commit()

        alerts = check_cost_to_serve(org.id)

        svc_alerts = [a for a in alerts if "Billing Service" in a.target_element]
        assert len(svc_alerts) == 0

    def test_no_alert_when_cost_flat(self, db_session, make_org):
        """Cost increase <=10% -> no alert."""
        org = make_org("costtoserve-3")

        svc = _make_element(db_session, org.id, "Auth Service", type_="ApplicationService", layer="application")
        app = _make_element(db_session, org.id, "Auth App", type_="ApplicationComponent", layer="application")
        _make_relationship(db_session, org.id, app.id, svc.id, type_="Realization")

        period1_start = date.today() - timedelta(days=730)
        period1_end = date.today() - timedelta(days=365)
        _make_cost_fact(db_session, org.id, app.id, "application", 100000, period1_start, period1_end)

        period2_start = date.today() - timedelta(days=365)
        period2_end = date.today()
        _make_cost_fact(db_session, org.id, app.id, "application", 105000, period2_start, period2_end)  # 5% increase

        db_session.commit()

        alerts = check_cost_to_serve(org.id)

        svc_alerts = [a for a in alerts if "Auth Service" in a.target_element]
        assert len(svc_alerts) == 0


# --------------------------------------------------------------------------- #
# Topic routing and combined run
# --------------------------------------------------------------------------- #


class TestTopicRouting:
    """Topic routing for get_predictive_insights / run_all_checks."""

    def test_topic_financial_returns_zombie_and_cost_to_serve(self, db_session, make_org):
        """topic='financial' returns checks 2 and 5 only."""
        org = make_org("topic-financial")

        # Zombie project setup
        from app.models.business_capabilities import BusinessCapability
        from app.models import ArchiMateElement
        
        cap_element = ArchiMateElement(name="Cap", type="Capability", layer="strategy", organization_id=org.id)
        db_session.add(cap_element)
        db_session.flush()
        
        capability = BusinessCapability(name="Cap", organization_id=org.id, archimate_element_id=cap_element.id)
        db_session.add(capability)
        db_session.flush()
        
        wp = _make_element(db_session, org.id, "WP", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org.id, wp.id, cap_element.id, type_="Realization")
        
        from app.models.strategic import StrategicInitiative
        initiative = StrategicInitiative(
            name="Zombie Initiative",
            organization_id=org.id,
            target_completion_date=date.today() - timedelta(days=200),
            status="in_progress",
        )
        db_session.add(initiative)
        db_session.flush()
        
        bc = _make_business_case(db_session, org.id, "Zombie BC", capability_id=capability.id, strategic_initiative_id=initiative.id, financial_benefit_annual=1000)
        app = _make_element(db_session, org.id, "App", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org.id, app.id, "application", 2000, date.today() - timedelta(days=365), date.today())

        # Cost to serve setup
        svc = _make_element(db_session, org.id, "Svc", type_="ApplicationService", layer="application")
        app2 = _make_element(db_session, org.id, "App2", type_="ApplicationComponent", layer="application")
        _make_relationship(db_session, org.id, app2.id, svc.id, type_="Realization")
        _make_cost_fact(db_session, org.id, app2.id, "application", 100000, date.today() - timedelta(days=730), date.today() - timedelta(days=365))
        _make_cost_fact(db_session, org.id, app2.id, "application", 120000, date.today() - timedelta(days=365), date.today())

        db_session.commit()

        alerts = run_all_checks(org.id, topic="financial")

        types = {a.prediction_type for a in alerts}
        assert types == {"ZOMBIE_PROJECT", "COST_TO_SERVE"}
        assert all(a.prediction_type in ("ZOMBIE_PROJECT", "COST_TO_SERVE") for a in alerts)

    def test_topic_hr_returns_bus_factor(self, db_session, make_org):
        """topic='hr' returns check 3 only."""
        org = make_org("topic-hr")
        user = _make_user(db_session, org, "hr@example.com")
        for i in range(6):
            app = _make_element(db_session, org.id, f"App {i}", type_="ApplicationComponent", layer="application")
            from app.models.application_portfolio import ApplicationComponent
            ac = ApplicationComponent.query.get(app.id)
            if ac:
                ac.criticality = "critical"
            _make_application_owner(db_session, org.id, user.id, "application", app.id, "primary")
        db_session.commit()

        alerts = run_all_checks(org.id, topic="hr")

        assert len(alerts) == 1
        assert alerts[0].prediction_type == "BUS_FACTOR"
    def test_topic_security_returns_domino_effect(self, db_session, make_org):
        """topic='security' returns check 4 only."""
        org = make_org("topic-security")
        internet_iface = _make_element(db_session, org.id, "API", type_="ApplicationInterface", layer="application")
        internet_iface.acm_properties = {"exposure": "internet"}
        db_session.add(internet_iface)
        data_obj = _make_element(db_session, org.id, "PII Data", type_="DataObject", layer="application")
        data_obj.acm_properties = {"classification": "personal data"}
        db_session.add(data_obj)
        _make_relationship(db_session, org.id, internet_iface.id, data_obj.id, type_="Flow")
        db_session.commit()

        alerts = run_all_checks(org.id, topic="security")

        assert len(alerts) == 1
        assert alerts[0].prediction_type == "DOMINO_EFFECT"

    def test_topic_none_returns_top_five_by_severity(self, db_session, make_org):
        """topic=None returns top 5 alerts across all checks ordered by severity then evidence count."""
        org = make_org("topic-none")

        # Create one of each check type
        # 1. Unfunded objective
        goal = _make_element(db_session, org.id, "Goal", type_="Goal", layer="motivation")

        # 2. Zombie project
        from app.models.business_capabilities import BusinessCapability
        from app.models import ArchiMateElement
        
        cap_element = ArchiMateElement(name="Cap", type="Capability", layer="strategy", organization_id=org.id)
        db_session.add(cap_element)
        db_session.flush()
        
        cap = BusinessCapability(name="Cap", organization_id=org.id, archimate_element_id=cap_element.id)
        db_session.add(cap)
        db_session.flush()
        
        wp = _make_element(db_session, org.id, "WP", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org.id, wp.id, cap_element.id, type_="Realization")
        
        from app.models.strategic import StrategicInitiative
        initiative = StrategicInitiative(
            name="Zombie Initiative",
            organization_id=org.id,
            target_completion_date=date.today() - timedelta(days=200),
            status="in_progress",
        )
        db_session.add(initiative)
        db_session.flush()
        
        bc = _make_business_case(db_session, org.id, "Zombie", capability_id=cap.id, strategic_initiative_id=initiative.id, financial_benefit_annual=1000)
        app = _make_element(db_session, org.id, "App", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org.id, app.id, "application", 2000, date.today() - timedelta(days=365), date.today())

        # 3. Bus factor
        user = _make_user(db_session, org, "bf@example.com")
        for i in range(6):
            a = _make_element(db_session, org.id, f"App{i}", type_="ApplicationComponent", layer="application")
            from app.models.application_portfolio import ApplicationComponent
            ac = ApplicationComponent.query.get(a.id)
            if ac:
                ac.criticality = "critical"
            _make_application_owner(db_session, org.id, user.id, "application", a.id, "primary")

        # 4. Domino effect
        iface = _make_element(db_session, org.id, "Iface", type_="ApplicationInterface", layer="application")
        iface.acm_properties = {"exposure": "internet"}
        db_session.add(iface)
        data = _make_element(db_session, org.id, "Data", type_="DataObject", layer="application")
        data.acm_properties = {"classification": "personal data"}
        db_session.add(data)
        _make_relationship(db_session, org.id, iface.id, data.id, type_="Flow")

        # 5. Cost to serve
        svc = _make_element(db_session, org.id, "Svc", type_="ApplicationService", layer="application")
        app2 = _make_element(db_session, org.id, "App2", type_="ApplicationComponent", layer="application")
        _make_relationship(db_session, org.id, app2.id, svc.id, type_="Realization")
        _make_cost_fact(db_session, org.id, app2.id, "application", 100000, date.today() - timedelta(days=730), date.today() - timedelta(days=365))
        _make_cost_fact(db_session, org.id, app2.id, "application", 120000, date.today() - timedelta(days=365), date.today())

        db_session.commit()

        alerts = run_all_checks(org.id, topic=None)

        assert len(alerts) <= 5
        # Should have HIGH severity first (ZOMBIE_PROJECT, DOMINO_EFFECT)
        high_alerts = [a for a in alerts if a.severity == "HIGH"]
        assert len(high_alerts) >= 2
        # Check ordering: HIGH before MEDIUM before LOW
        severities = [a.severity for a in alerts]
        assert severities == sorted(severities, key=lambda s: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(s, 3))

    def test_unknown_topic_raises_error(self, db_session, make_org):
        """Unknown topic raises ValueError naming allowed values."""
        org = make_org("topic-unknown")

        with pytest.raises(ValueError) as exc:
            run_all_checks(org.id, topic="invalid")

        assert "Unknown topic 'invalid'" in str(exc.value)
        assert "financial" in str(exc.value)
        assert "hr" in str(exc.value)
        assert "security" in str(exc.value)


# --------------------------------------------------------------------------- #
# Organisation isolation
# --------------------------------------------------------------------------- #


class TestOrganisationIsolation:
    """Every query is filtered by the signed-in user's organisation."""

    def test_org_a_does_not_see_org_b_elements(self, db_session, make_org):
        """Organisation A's alerts never include organisation B's elements."""
        org_a = make_org("iso-a")
        org_b = make_org("iso-b")

        # Create zombie project in org B
        from app.models.business_capabilities import BusinessCapability
        from app.models import ArchiMateElement
        
        cap_element_b = ArchiMateElement(name="Cap B", type="Capability", layer="strategy", organization_id=org_b.id)
        db_session.add(cap_element_b)
        db_session.flush()
        
        cap_b = BusinessCapability(name="Cap B", organization_id=org_b.id, archimate_element_id=cap_element_b.id)
        db_session.add(cap_b)
        db_session.flush()
        
        wp_b = _make_element(db_session, org_b.id, "WP B", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org_b.id, wp_b.id, cap_element_b.id, type_="Realization")
        
        from app.models.strategic import StrategicInitiative
        initiative_b = StrategicInitiative(
            name="Zombie Initiative B",
            organization_id=org_b.id,
            target_completion_date=date.today() - timedelta(days=200),
            status="in_progress",
        )
        db_session.add(initiative_b)
        db_session.flush()
        
        bc_b = _make_business_case(db_session, org_b.id, "Zombie B", capability_id=cap_b.id, strategic_initiative_id=initiative_b.id, financial_benefit_annual=1000)
        app_b = _make_element(db_session, org_b.id, "App B", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org_b.id, app_b.id, "application", 2000, date.today() - timedelta(days=365), date.today())

        # Create healthy project in org A
        cap_element_a = ArchiMateElement(name="Cap A", type="Capability", layer="strategy", organization_id=org_a.id)
        db_session.add(cap_element_a)
        db_session.flush()
        
        cap_a = BusinessCapability(name="Cap A", organization_id=org_a.id, archimate_element_id=cap_element_a.id)
        db_session.add(cap_a)
        db_session.flush()
        
        wp_a = _make_element(db_session, org_a.id, "WP A", type_="WorkPackage", layer="implementation")
        _make_relationship(db_session, org_a.id, wp_a.id, cap_element_a.id, type_="Realization")
        
        initiative_a = StrategicInitiative(
            name="Healthy Initiative A",
            organization_id=org_a.id,
            target_completion_date=date.today() + timedelta(days=180),
            status="in_progress",
        )
        db_session.add(initiative_a)
        db_session.flush()
        
        bc_a = _make_business_case(db_session, org_a.id, "Healthy A", capability_id=cap_a.id, strategic_initiative_id=initiative_a.id, financial_benefit_annual=5000)
        app_a = _make_element(db_session, org_a.id, "App A", type_="ApplicationComponent", layer="application")
        _make_cost_fact(db_session, org_a.id, app_a.id, "application", 1000, date.today() - timedelta(days=365), date.today())

        db_session.commit()

        # Run checks for org A
        alerts_a = run_all_checks(org_a.id, topic="financial")

        # Should only see org A's elements (healthy -> no zombie alert)
        zombie_alerts = [a for a in alerts_a if a.prediction_type == "ZOMBIE_PROJECT"]
        assert len(zombie_alerts) == 0

        # Run checks for org B
        alerts_b = run_all_checks(org_b.id, topic="financial")

        # Should see org B's zombie project
        zombie_alerts_b = [a for a in alerts_b if a.prediction_type == "ZOMBIE_PROJECT"]
        assert len(zombie_alerts_b) == 1
        assert "Zombie B" in zombie_alerts_b[0].target_element