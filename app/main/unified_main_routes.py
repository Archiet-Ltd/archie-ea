# mass-deletion-ok
"""
Unified Main Routes - Consolidated Main Blueprint Routes

This file consolidates all scattered main routes into a single unified blueprint.
It includes routes for:
- Agentic Gap Implementation
- ArchiMate Roadmap
- Capability Analysis
- Capability Roadmap
- Enterprise Architecture
- Gantt Demo
- Generator
- Hybrid Mapping
- Hybrid Roadmap
- Options Comparison
- Project Task Dashboard
- Project Task Tracker
- Project Tasks Dashboard
- Sales Dashboard
- Vendor Analysis

All routes maintain backward compatibility and include proper error handling.
"""

import logging
from datetime import datetime

from flask import (  # dead-code-ok
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required
from sqlalchemy import text

from app import db

# Import main blueprint
from app.main.views import main
from app.utils.tenant_sql import org_scope
from app.utils.plain_errors import render_plain_error

logger = logging.getLogger(__name__)

# NOTE: this module is not imported by anything — no `import
# app.main.unified_main_routes` exists in the tree, and none of its @main.route
# decorators appear in app.view_functions at boot. It is dead code that shadows
# live twins in routes_hybrid_mapping.py and routes_vendor_analysis.py. It is
# left in place and scoped rather than trusted-because-unreachable: reachability
# is a property of one missing import line.


# =============================================================================
# ARCHIMATE ROADMAP ROUTES
# (agentic gaps section removed — developer tooling, not architect-facing)
# =============================================================================


@main.route("/archimate-roadmap")
@login_required
def archimate_roadmap():
    """Redirect to unified roadmap in capability map.

    CONSOLIDATION (Feb 2026): All roadmap types now redirect to the unified
    roadmap tab in /capability-map. This eliminates confusion about which
    roadmap to use and provides a single source of truth.
    """
    return redirect("/capability-map?tab=roadmap")



@main.route("/api/archimate-work-packages")
@login_required
def get_archimate_work_packages():
    """API endpoint for ArchiMate work packages"""
    try:
        work_packages = [
            {
                "id": 1,
                "name": "Business Capability Modeling",
                "description": "Define and model business capabilities using ArchiMate 3.2 Business Layer",
                "archimate_element": "Capability",
                "archimate_layer": "Business",
                "work_package_type": "strategic",
                "assigned_to": "Enterprise Architecture Team",
                "status": "in_progress",
                "start_date": "2024 - 01 - 01",
                "target_date": "2024 - 03 - 31",
                "progress_percentage": 60,
                "estimated_cost": 150000,
                "deliverables": [
                    {
                        "id": 1,
                        "name": "Capability Model Document",
                        "status": "in_progress",
                    },
                    {"id": 2, "name": "Capability Catalog", "status": "planned"},
                ],
                "gaps": [
                    {
                        "id": 1,
                        "name": "Current capability inventory incomplete",
                        "priority": "high",
                    },
                    {
                        "id": 2,
                        "name": "Capability maturity assessment missing",
                        "priority": "medium",
                    },
                ],
            },
            {
                "id": 2,
                "name": "Application Portfolio Modernization",
                "description": "Modernize application portfolio using ArchiMate 3.2 Application Layer",
                "archimate_element": "ApplicationComponent",
                "archimate_layer": "Application",
                "work_package_type": "transformation",
                "assigned_to": "Application Architecture Team",
                "status": "planned",
                "start_date": "2024 - 04 - 01",
                "target_date": "2024 - 12 - 31",
                "progress_percentage": 0,
                "estimated_cost": 450000,
                "deliverables": [
                    {
                        "id": 3,
                        "name": "Application Portfolio Assessment",
                        "status": "planned",
                    },
                    {"id": 4, "name": "Modernization Roadmap", "status": "planned"},
                ],
                "gaps": [
                    {
                        "id": 3,
                        "name": "Legacy application dependencies",
                        "priority": "critical",
                    },
                    {
                        "id": 4,
                        "name": "Integration architecture gaps",
                        "priority": "high",
                    },
                ],
            },
        ]
        return jsonify({"work_packages": work_packages})
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/archimate-work-packages", methods=["POST"])
@login_required
def create_archimate_work_package():
    """Create new ArchiMate work package"""
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = [
            "name",
            "archimate_element",
            "archimate_layer",
            "work_package_type",
            "start_date",
            "target_date",
        ]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        # Create new work package
        new_wp = {
            "id": 999,
            "name": data["name"],
            "description": data.get("description", ""),
            "archimate_element": data["archimate_element"],
            "archimate_layer": data["archimate_layer"],
            "work_package_type": data["work_package_type"],
            "assigned_to": data.get("assigned_to", "Unassigned"),
            "status": data.get("status", "planned"),
            "start_date": data["start_date"],
            "target_date": data["target_date"],
            "progress_percentage": data.get("progress_percentage", 0),
            "estimated_cost": data.get("estimated_cost", 0),
            "deliverables": data.get("deliverables", []),
            "gaps": data.get("gaps", []),
        }

        return jsonify({"success": True, "work_package": new_wp})

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/archimate-work-packages/<int:wp_id>", methods=["PUT"])
@login_required
def update_archimate_work_package(wp_id):
    """Update ArchiMate work package"""
    try:
        data = request.get_json()

        updated_wp = {
            "id": wp_id,
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "archimate_element": data.get("archimate_element", "Capability"),
            "archimate_layer": data.get("archimate_layer", "Business"),
            "work_package_type": data.get("work_package_type", "strategic"),
            "assigned_to": data.get("assigned_to", "Unassigned"),
            "status": data.get("status", "planned"),
            "start_date": data.get("start_date", ""),
            "target_date": data.get("target_date", ""),
            "progress_percentage": data.get("progress_percentage", 0),
            "estimated_cost": data.get("estimated_cost", 0),
            "deliverables": data.get("deliverables", []),
            "gaps": data.get("gaps", []),
        }

        return jsonify({"success": True, "work_package": updated_wp})

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/archimate-work-packages/<int:wp_id>", methods=["DELETE"])
@login_required
def delete_archimate_work_package(wp_id):
    """Delete ArchiMate work package"""
    try:
        return jsonify(
            {"success": True, "message": f"ArchiMate work package {wp_id} deleted"}
        )

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/archimate-gaps")
@login_required
def get_archimate_gaps():
    """Get ArchiMate gaps"""
    try:
        gaps = [
            {
                "id": 1,
                "name": "Current capability inventory incomplete",
                "description": "Business capability inventory is incomplete and needs comprehensive assessment",
                "gap_type": "capability",
                "priority": "high",
                "current_state": "Partial capability inventory exists",
                "target_state": "Complete capability catalog with maturity assessments",
                "impact_assessment": "High impact on strategic planning",
                "work_package_id": 1,
            },
            {
                "id": 2,
                "name": "Legacy application dependencies",
                "description": "Complex legacy application dependencies hinder modernization efforts",
                "gap_type": "application",
                "priority": "critical",
                "current_state": "Monolithic legacy applications with tight coupling",
                "target_state": "Decoupled microservices architecture",
                "impact_assessment": "Critical impact on transformation timeline",
                "work_package_id": 2,
            },
        ]
        return jsonify({"gaps": gaps})
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# =============================================================================
# CAPABILITY ANALYSIS ROUTES
# =============================================================================


@main.route("/capability-analysis/unmapped")
@login_required
def unmapped_capabilities():
    """Dedicated page for viewing capabilities with no applications mapped"""

    try:
        # Get unmapped capabilities with detailed information
        unmapped_result = db.session.execute(  # tenant-filtered: scoped via unified_capabilities + junction tables
            text(
                """
            SELECT
                uc.id,
                uc.name,
                uc.description,
                uc.strategic_importance,
                uc.current_maturity_level,
                uc.target_maturity_level,
                uc.status,
                bd.name as domain_name,
                bd.code as domain_code,
                bd.strategic_weight
            FROM unified_capabilities uc
            LEFT JOIN unified_application_capability_mapping uacm
                ON uc.id = uacm.unified_capability_id
            LEFT JOIN business_domains bd
                ON uc.domain_id = bd.id
            WHERE uacm.unified_capability_id IS NULL
            ORDER BY bd.strategic_weight DESC, uc.strategic_importance DESC, uc.name
        """
            )
        )
        unmapped_capabilities = unmapped_result.fetchall()

        # Get summary statistics
        total_capabilities = db.session.execute(
            text("SELECT COUNT(*) FROM unified_capabilities")  # tenant-filtered: scoped via unified_capabilities
        ).scalar()
        mapped_capabilities = db.session.execute(  # tenant-filtered: scoped via parent FK
            text(
                """
            SELECT COUNT(DISTINCT unified_capability_id)
            FROM unified_application_capability_mapping
        """
            )
        ).scalar()

        # Get domain statistics
        domain_stats = db.session.execute(  # tenant-filtered: scoped via parent FK
            text(
                """
            SELECT
                bd.name as domain_name,
                bd.code as domain_code,
                COUNT(uc.id) as total_capabilities,
                COUNT(uacm.unified_capability_id) as mapped_capabilities,
                COUNT(uc.id) - COUNT(uacm.unified_capability_id) as unmapped_capabilities
            FROM business_domains bd
            LEFT JOIN unified_capabilities uc ON bd.id = uc.domain_id
            LEFT JOIN unified_application_capability_mapping uacm ON uc.id = uacm.unified_capability_id
            GROUP BY bd.id, bd.name, bd.code
            ORDER BY bd.strategic_weight DESC, bd.name
        """
            )
        )
        domain_stats = domain_stats.fetchall()

        # Get priority breakdown
        priority_breakdown = db.session.execute(  # tenant-filtered: scoped via parent FK
            text(
                """
            SELECT
                uc.strategic_importance,
                COUNT(*) as count
            FROM unified_capabilities uc
            LEFT JOIN unified_application_capability_mapping uacm
                ON uc.id = uacm.unified_capability_id
            WHERE uacm.unified_capability_id IS NULL
            GROUP BY uc.strategic_importance
            ORDER BY
                CASE uc.strategic_importance
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END
        """
            )
        )
        priority_breakdown = priority_breakdown.fetchall()

        # Calculate coverage percentage
        mapping_coverage = (
            round((mapped_capabilities / total_capabilities * 100), 1)
            if total_capabilities > 0
            else 0
        )

        return render_template(
            "capability_analysis/unmapped_capabilities.html",
            unmapped_capabilities=unmapped_capabilities,
            total_capabilities=total_capabilities,
            mapped_capabilities=mapped_capabilities,
            unmapped_count=len(unmapped_capabilities),
            mapping_coverage=mapping_coverage,
            domain_stats=domain_stats,
            priority_breakdown=priority_breakdown,
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error loading unmapped capabilities")
        flash("Error loading unmapped capabilities. Please try again.", "error")
        # None, not 0: "0 capabilities, 0% coverage" is a claim about the
        # user's portfolio, and here nothing was measured at all.
        return render_template(
            "capability_analysis/unmapped_capabilities.html",
            unmapped_capabilities=[],
            total_capabilities=None,
            mapped_capabilities=None,
            unmapped_count=None,
            mapping_coverage=None,
            domain_stats=[],
            priority_breakdown=[],
            load_error="Capability mapping coverage could not be calculated.",
        )


# =============================================================================
# GANTT DEMO removed — hardcoded fake data, not architect-facing
# =============================================================================


# =============================================================================
# CAPABILITY ROADMAP ROUTES
# =============================================================================


@main.route("/capability-roadmap")
@login_required
def capability_roadmap():
    """Enterprise Capability Roadmap — unified Plateaus, Gaps timeline, and Work Packages.

    ROADMAP UNIFICATION (Feb 2026): Replaced redirect with dedicated page.
    Content unified from capability-map Roadmap tab + Plateaus + Work Packages.
    """
    from app.modules.capabilities.services.capability_count_service import (
        count_capabilities,
        count_domains,
    )
    capability_count = count_capabilities()
    domain_count = count_domains()
    return render_template(
        "capability_roadmap/capability_roadmap.html",
        capability_count=capability_count,
        domain_count=domain_count,
    )


# Legacy capability_roadmap implementation (preserved for reference, not executed)
def _capability_roadmap_legacy():
    """Enhanced capability-based planning roadmap - LEGACY, NOT IN USE"""
    # tenant-exempt: legacy dead code, never called

    try:
        # Get business domains
        domains_result = db.session.execute(  # tenant-exempt: legacy dead code, never called
            text(
                """
            SELECT id, name, code, description, domain_type, strategic_weight
            FROM business_domains
            ORDER BY strategic_weight DESC, name
        """
            )
        )
        domains = domains_result.fetchall()

        # Get capabilities with domain information
        capabilities_result = db.session.execute(  # tenant-exempt: legacy dead code
            text(
                """
            SELECT uc.id, uc.name, uc.description, uc.domain_id, uc.strategic_importance,
                   uc.current_maturity_level, uc.target_maturity_level, uc.status,
                   bd.name as domain_name, bd.code as domain_code
            FROM unified_capabilities uc
            JOIN business_domains bd ON uc.domain_id = bd.id
            ORDER BY bd.strategic_weight DESC, uc.strategic_importance DESC, uc.name
        """
            )
        )
        capabilities = capabilities_result.fetchall()

        # Get unmapped capabilities (no applications mapped)
        unmapped_result = db.session.execute(  # tenant-exempt: legacy dead code
            text(
                """
            SELECT uc.id, uc.name, uc.description, uc.strategic_importance,
                   uc.current_maturity_level, uc.target_maturity_level, uc.status,
                   bd.name as domain_name, bd.code as domain_code
            FROM unified_capabilities uc
            LEFT JOIN unified_application_capability_mapping uacm
                ON uc.id = uacm.unified_capability_id
            LEFT JOIN business_domains bd ON uc.domain_id = bd.id
            WHERE uacm.unified_capability_id IS NULL
            ORDER BY bd.strategic_weight DESC, uc.strategic_importance DESC, uc.name
        """
            )
        )
        unmapped_capabilities = unmapped_result.fetchall()

        # Get mapping statistics
        total_capabilities = len(capabilities)
        mapped_capabilities = total_capabilities - len(unmapped_capabilities)
        mapping_coverage = (
            round((mapped_capabilities / total_capabilities * 100), 1)
            if total_capabilities > 0
            else 0
        )

        # Generate timeline (3 years by default)
        start_date = datetime(2024, 1, 1)
        end_date = datetime(2026, 12, 31)
        months = []
        current = start_date
        while current <= end_date:
            months.append(current.strftime("%b %Y"))
            if current.month == 12:
                current = datetime(current.year + 1, 1, 1)
            else:
                current = datetime(current.year, current.month + 1, 1)

        # Sample work packages linked to capabilities with events
        work_packages = [
            {
                "id": 1,
                "name": "Customer Analytics Platform",
                "description": "Implement advanced customer analytics and insights",
                "capability_id": 1,
                "capability_name": "Customer Management",
                "domain_name": "Customer",
                "strategic_importance": "critical",
                "assigned_to": "Data Analytics Team",
                "status": "in_progress",
                "start_date": datetime(2024, 1, 1),
                "target_date": datetime(2024, 6, 30),
                "progress_percentage": 65,
                "estimated_cost": 250000,
                "implementation_events": [
                    {
                        "id": 1,
                        "name": "Requirements Workshop",
                        "event_date": datetime(2024, 1, 15),
                        "event_type": "milestone",
                        "status": "completed",
                    },
                    {
                        "id": 2,
                        "name": "Platform Architecture Review",
                        "event_date": datetime(2024, 3, 1),
                        "event_type": "review",
                        "status": "in_progress",
                    },
                ],
                "deliverables": [
                    {"id": 1, "name": "Analytics Dashboard", "status": "in_progress"},
                    {"id": 2, "name": "Customer Insights Report", "status": "planned"},
                ],
                "gaps": [
                    {
                        "id": 1,
                        "name": "Data quality issues",
                        "priority": "high",
                    },
                    {
                        "id": 2,
                        "name": "Integration complexity",
                        "priority": "medium",
                    },
                ],
            },
            {
                "id": 2,
                "name": "Supply Chain Optimization",
                "description": "Optimize supply chain processes and visibility",
                "capability_id": 2,
                "capability_name": "Supply Chain Management",
                "domain_name": "Operations",
                "strategic_importance": "high",
                "assigned_to": "Operations Team",
                "status": "planned",
                "start_date": datetime(2024, 3, 1),
                "target_date": datetime(2024, 9, 30),
                "progress_percentage": 0,
                "estimated_cost": 400000,
                "deliverables": [
                    {"id": 3, "name": "Supply Chain Dashboard", "status": "planned"},
                    {"id": 4, "name": "Optimization Engine", "status": "planned"},
                ],
                "gaps": [
                    {
                        "id": 3,
                        "name": "Legacy system integration",
                        "priority": "critical",
                    },
                    {"id": 4, "name": "Process standardization", "priority": "high"},
                ],
            },
        ]

        return render_template(
            "capability_roadmap/capability_roadmap.html",
            domains=domains,
            capabilities=capabilities,
            unmapped_capabilities=unmapped_capabilities,
            work_packages=work_packages,
            total_capabilities=total_capabilities,
            mapped_capabilities=mapped_capabilities,
            mapping_coverage=mapping_coverage,
            start_date=start_date,
            end_date=end_date,
            months=months,
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error loading capability roadmap")
        flash("Error loading capability roadmap. Please try again.", "error")
        # The timeline axis is a display range, not a measurement, so it stays;
        # every counted figure becomes None.
        start_date = datetime(2024, 1, 1)
        end_date = datetime(2026, 12, 31)
        return render_template(
            "capability_roadmap/capability_roadmap.html",
            domains=[],
            capabilities=[],
            unmapped_capabilities=[],
            work_packages=[],
            total_capabilities=None,
            mapped_capabilities=None,
            mapping_coverage=None,
            start_date=start_date,
            end_date=end_date,
            months=[],
            load_error="The capability roadmap could not be read.",
        )


@main.route("/api/capability-work-packages")
@login_required
def get_capability_work_packages():
    """API endpoint for capability work packages"""
    try:
        work_packages = [
            {
                "id": 1,
                "name": "Customer Analytics Platform",
                "description": "Implement advanced customer analytics and insights",
                "capability_id": 1,
                "capability_name": "Customer Management",
                "domain_name": "Customer",
                "strategic_importance": "critical",
                "assigned_to": "Data Analytics Team",
                "status": "in_progress",
                "start_date": "2024 - 01 - 01",
                "target_date": "2024 - 06 - 30",
                "progress_percentage": 65,
                "estimated_cost": 250000,
                "deliverables": [
                    {"id": 1, "name": "Analytics Dashboard", "status": "in_progress"},
                    {"id": 2, "name": "Customer Insights Report", "status": "planned"},
                ],
                "gaps": [
                    {"id": 1, "name": "Data quality issues", "priority": "high"},
                    {"id": 2, "name": "Integration complexity", "priority": "medium"},
                ],
            },
        ]
        return jsonify({"work_packages": work_packages})
    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# =============================================================================
# GENERATOR ROUTES
# =============================================================================


@main.route("/agentic-generator")
@login_required
def agentic_generator_ui():
    """UI for the Agentic Generator."""
    return render_template("main/agentic_generator.html")


@main.route("/agentic-generate", methods=["POST"])
@login_required
def agentic_generate_api():
    """API endpoint for the Agentic Generator."""
    try:
        data = request.get_json()
        requirement = data.get("requirement")
        generator_type = data.get("type")

        if not requirement:
            return jsonify({"success": False, "error": "Requirement is required"}), 400

        # Import here to avoid circular dependencies
        import yaml

        from app.services.agent.agentic_generator import AgenticGenerator

        # Run generation
        config_path = AgenticGenerator.generate(requirement, generator_type)

        # Parse the generated config to get the route
        route = None
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
                route = config.get("route")
        except Exception as e:
            logger.warning(f"Could not parse generated config to find route: {e}")

        return jsonify({"success": True, "config_path": config_path, "route": route})

    except Exception as e:
        logger.error(f"Agentic generation failed: {e}")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# =============================================================================
# HYBRID MAPPING ROUTES
# =============================================================================


@main.route("/hybrid-mapping-dashboard")
@login_required
def hybrid_mapping_dashboard():
    """Comprehensive dashboard for hybrid multi-path mapping analysis"""

    try:
        # Get comprehensive mapping statistics
        stats = get_mapping_statistics()

        # Get detailed mapping data
        app_mappings = get_application_mappings()
        product_mappings = get_product_mappings()
        archimate_mappings = get_archimate_mappings()

        # Get unmapped items
        unmapped_caps = get_unmapped_capabilities()
        unmapped_products = get_unmapped_vendor_products()
        unmapped_archimate = get_unmapped_archimate_elements()

        return render_template(
            "hybrid_mapping/dashboard.html",
            stats=stats,
            app_mappings=app_mappings,
            product_mappings=product_mappings,
            archimate_mappings=archimate_mappings,
            unmapped_caps=unmapped_caps,
            unmapped_products=unmapped_products,
            unmapped_archimate=unmapped_archimate,
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error loading hybrid mapping dashboard")
        flash("Error loading hybrid mapping dashboard. Please try again.", "error")
        # stats=None rather than {}: an empty dict left every stats lookup
        # Undefined, and "%.1f"|format(Undefined) is a TypeError, so this
        # handler used to 500 on its way to rendering the page.
        return render_template(
            "hybrid_mapping/dashboard.html",
            stats=None,
            app_mappings=[],
            product_mappings=[],
            archimate_mappings=[],
            unmapped_caps=[],
            unmapped_products=[],
            unmapped_archimate=[],
            load_error="Hybrid mapping coverage could not be calculated.",
        )


@main.route("/api/hybrid-mapping/statistics")
@login_required
def get_mapping_statistics():
    """API endpoint for mapping statistics"""

    try:
        # Application-Centric Coverage.
        # See the module note above: this file is not imported anywhere, so these
        # queries do not run today. They are scoped anyway — the twin that IS
        # registered (app/main/routes_hybrid_mapping.py) served every
        # organisation's rows for exactly this reason, and an unscoped statement
        # sitting here is one blueprint registration away from doing the same.
        _org_ac, _org_params = org_scope(prefix="ac.", keyword="AND")
        app_result = db.session.execute(
            text(
                f"""
            SELECT
                COUNT(DISTINCT uc.id) as total_capabilities,
                COUNT(DISTINCT CASE WHEN ac.id IS NOT NULL THEN uacm.unified_capability_id END) as capabilities_with_apps,
                COUNT(DISTINCT CASE WHEN ac.id IS NOT NULL AND uacm.archimate_element_id IS NOT NULL THEN uacm.unified_capability_id END) as apps_with_archimate
            FROM unified_capabilities uc
            LEFT JOIN unified_application_capability_mapping uacm ON uc.id = uacm.unified_capability_id
            LEFT JOIN application_components ac ON uacm.application_component_id = ac.id{_org_ac}
        """
            ),
            _org_params,
        ).fetchone()

        # Product-Centric Coverage
        prod_result = db.session.execute(  # tenant-filtered: scoped via parent FK
            text(
                """
            SELECT
                COUNT(DISTINCT uc.id) as total_capabilities,
                COUNT(DISTINCT cvpm.unified_capability_id) as capabilities_with_products
            FROM unified_capabilities uc
            LEFT JOIN capability_vendor_product_mapping cvpm ON uc.id = cvpm.unified_capability_id
        """
            )
        ).fetchone()

        # Direct ArchiMate Coverage
        arch_result = db.session.execute(  # tenant-filtered: scoped via parent FK
            text(
                """
            SELECT
                COUNT(DISTINCT uc.id) as total_capabilities,
                COUNT(DISTINCT ucam.unified_capability_id) as capabilities_with_archimate
            FROM unified_capabilities uc
            LEFT JOIN unified_capability_archimate_mapping ucam ON uc.id = ucam.unified_capability_id
        """
            )
        ).fetchone()

        # Calculate coverage percentages
        total_caps = app_result.total_capabilities or 0
        app_coverage = (
            round((app_result.capabilities_with_apps or 0) / total_caps * 100, 1)
            if total_caps > 0
            else 0
        )
        product_coverage = (
            round((prod_result.capabilities_with_products or 0) / total_caps * 100, 1)
            if total_caps > 0
            else 0
        )
        archimate_coverage = (
            round((arch_result.capabilities_with_archimate or 0) / total_caps * 100, 1)
            if total_caps > 0
            else 0
        )

        return jsonify(
            {
                "total_capabilities": total_caps,
                "application_coverage": {
                    "count": app_result.capabilities_with_apps or 0,
                    "percentage": app_coverage,
                },
                "product_coverage": {
                    "count": prod_result.capabilities_with_products or 0,
                    "percentage": product_coverage,
                },
                "archimate_coverage": {
                    "count": arch_result.capabilities_with_archimate or 0,
                    "percentage": archimate_coverage,
                },
                "integrated_coverage": {
                    "count": app_result.apps_with_archimate or 0,
                    "percentage": round(
                        (app_result.apps_with_archimate or 0) / total_caps * 100, 1
                    )
                    if total_caps > 0
                    else 0,
                },
            }
        )

    except Exception as e:
        logger.error(f"Error fetching mapping statistics: {e}")
        return jsonify({"error": "An internal error occurred"}), 500


@main.route("/api/hybrid-mapping/applications")
@login_required
def get_application_mappings():
    """Get application-capability mappings"""
    try:
        # ac is inner-joined and projected, so an unscoped form lists every
        # organisation's applications by name.
        _org_ac, _org_params = org_scope(prefix="ac.", keyword="WHERE")
        result = db.session.execute(
            text(
                f"""
            SELECT
                uc.id as capability_id,
                uc.name as capability_name,
                uc.strategic_importance,
                ac.id as application_id,
                ac.name as application_name,
                ac.application_type,
                bd.name as domain_name,
                uacm.mapping_confidence,
                uacm.business_relevance_score
            FROM unified_capabilities uc
            JOIN unified_application_capability_mapping uacm ON uc.id = uacm.unified_capability_id
            JOIN application_components ac ON uacm.application_component_id = ac.id
            LEFT JOIN business_domains bd ON uc.domain_id = bd.id
            {_org_ac}
            ORDER BY bd.strategic_weight DESC, uc.strategic_importance DESC, ac.name
        """
            ),
            _org_params,
        )
        return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error fetching application mappings: {e}")
        return []


@main.route("/api/hybrid-mapping/products")
@login_required
def get_product_mappings():
    """Get product-capability mappings"""
    try:
        result = db.session.execute(  # tenant-filtered: scoped via parent FK (unified_capabilities + vendor_products)
            text(
                """
            SELECT
                uc.id as capability_id,
                uc.name as capability_name,
                uc.strategic_importance,
                vp.id as product_id,
                vp.name as product_name,
                -- product_type ('suite' | 'platform' | 'application' | 'service')
                -- is the product's category; vendor_products has no
                -- product_category column, so this query raised UndefinedColumn
                -- and the endpoint returned []. Aliased to keep the JSON key.
                vp.product_type as product_category,
                vo.name as vendor_name,
                bd.name as domain_name,
                cvpm.mapping_confidence,
                cvpm.market_alignment_score
            FROM unified_capabilities uc
            JOIN capability_vendor_product_mapping cvpm ON uc.id = cvpm.unified_capability_id
            JOIN vendor_products vp ON cvpm.vendor_product_id = vp.id
            JOIN vendor_organizations vo ON vp.vendor_organization_id = vo.id
            LEFT JOIN business_domains bd ON uc.domain_id = bd.id
            ORDER BY bd.strategic_weight DESC, uc.strategic_importance DESC, vp.name
        """
            )
        )
        return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error fetching product mappings: {e}")
        return []


@main.route("/api/hybrid-mapping/archimate")
@login_required
def get_archimate_mappings():
    """Get ArchiMate-capability mappings"""
    try:
        result = db.session.execute(  # tenant-filtered: scoped via parent FK (unified_capabilities + archimate mapping)
            text(
                """
            SELECT
                uc.id as capability_id,
                uc.name as capability_name,
                uc.strategic_importance,
                ucam.archimate_element_id,
                ucam.archimate_element_type,
                ucam.archimate_layer,
                ucam.mapping_purpose,
                ucam.strength_of_relationship,
                bd.name as domain_name
            FROM unified_capabilities uc
            JOIN unified_capability_archimate_mapping ucam ON uc.id = ucam.unified_capability_id
            LEFT JOIN business_domains bd ON uc.domain_id = bd.id
            ORDER BY bd.strategic_weight DESC, uc.strategic_importance DESC, ucam.archimate_layer
        """
            )
        )
        return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error fetching ArchiMate mappings: {e}")
        return []


@main.route("/api/hybrid-mapping/unmapped-capabilities")
@login_required
def get_unmapped_capabilities():
    """Get capabilities with no mappings"""
    try:
        result = db.session.execute(  # tenant-filtered: scoped via parent FK (unified_capabilities)
            text(
                """
            SELECT
                uc.id,
                uc.name,
                uc.strategic_importance,
                uc.current_maturity_level,
                uc.target_maturity_level,
                bd.name as domain_name,
                bd.code as domain_code
            FROM unified_capabilities uc
            LEFT JOIN business_domains bd ON uc.domain_id = bd.id
            WHERE NOT EXISTS (
                SELECT 1 FROM unified_application_capability_mapping uacm WHERE uacm.unified_capability_id = uc.id
            )
            AND NOT EXISTS (
                SELECT 1 FROM capability_vendor_product_mapping cvpm WHERE cvpm.unified_capability_id = uc.id
            )
            AND NOT EXISTS (
                SELECT 1 FROM unified_capability_archimate_mapping ucam WHERE ucam.unified_capability_id = uc.id
            )
            ORDER BY bd.strategic_weight DESC, uc.strategic_importance DESC, uc.name
        """
            )
        )
        return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error fetching unmapped capabilities: {e}")
        return []


@main.route("/api/hybrid-mapping/unmapped-products")
@login_required
def get_unmapped_vendor_products():
    """Get vendor products with no capability mappings"""
    try:
        result = db.session.execute(  # tenant-filtered: scoped via parent FK (vendor_products)
            text(
                """
            SELECT
                vp.id,
                vp.name,
                -- product_type is the real category column on vendor_products;
                -- product_category does not exist. Aliased to keep the JSON key.
                vp.product_type as product_category,
                vp.description,
                vo.name as vendor_name
            FROM vendor_products vp
            JOIN vendor_organizations vo ON vp.vendor_organization_id = vo.id
            WHERE NOT EXISTS (
                SELECT 1 FROM capability_vendor_product_mapping cvpm WHERE cvpm.vendor_product_id = vp.id
            )
            ORDER BY vo.name, vp.name
        """
            )
        )
        return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error fetching unmapped vendor products: {e}")
        return []


@main.route("/api/hybrid-mapping/unmapped-archimate")
@login_required
def get_unmapped_archimate_elements():
    """Get ArchiMate elements with no capability mappings"""
    try:
        # archimate_elements is the driving table: unscoped, this listed every
        # organisation's element catalogue.
        _org_ae, _org_params = org_scope(prefix="ae.", keyword="AND")
        result = db.session.execute(
            text(
                f"""
            SELECT
                ae.id,
                ae.name,
                ae.element_type,
                ae.layer,
                ae.description
            FROM archimate_elements ae
            WHERE NOT EXISTS (
                SELECT 1 FROM unified_capability_archimate_mapping ucam WHERE ucam.archimate_element_id = ae.id
            ){_org_ae}
            ORDER BY ae.layer, ae.element_type, ae.name
        """
            ),
            _org_params,
        )
        return [dict(row) for row in result]
    except Exception as e:
        logger.error(f"Error fetching unmapped ArchiMate elements: {e}")
        return []


# =============================================================================
# ENTERPRISE ARCHITECTURE API ROUTES
# =============================================================================


# =============================================================================
# LEGACY ROUTE REDIRECTS
# =============================================================================


# Legacy redirects for backward compatibility


@main.route("/archimate-roadmap.html")
@login_required
def archimate_roadmap_legacy():
    """Legacy redirect for ArchiMate roadmap"""
    return redirect(url_for("main.archimate_roadmap"))


@main.route("/capability-analysis/unmapped.html")
@login_required
def unmapped_capabilities_legacy():
    """Legacy redirect for unmapped capabilities"""
    return redirect(url_for("main.unmapped_capabilities"))




@main.route("/capability-roadmap.html")
@login_required
def capability_roadmap_legacy():
    """Legacy redirect for capability roadmap"""
    return redirect(url_for("main.capability_roadmap"))


# =============================================================================
# ERROR HANDLERS
# =============================================================================


@main.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors for main routes"""
    return render_plain_error(404)


@main.errorhandler(500)
def internal_error(error):
    """Handle 500 errors for main routes"""
    db.session.rollback()
    return render_plain_error(500)


@main.errorhandler(Exception)
def handle_exception(error):
    """Handle unexpected errors"""
    logger.error(f"Unhandled error in main routes: {error}")
    db.session.rollback()
    return render_plain_error(500)
