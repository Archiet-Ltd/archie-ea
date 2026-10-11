"""
Enterprise Architecture API Routes
Unified endpoints for all roadmap systems
"""

from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request  # dead-code-ok
from flask_login import login_required
from sqlalchemy import text

from app.decorators import audit_log

# Create blueprint
enterprise_bp = Blueprint("enterprise_architecture", __name__, url_prefix="/api/enterprise")


@enterprise_bp.route("/canvas", methods=["GET"])
@login_required
def get_enterprise_canvas():
    """Unified enterprise canvas with all roadmap data"""
    try:
        db = current_app.extensions["sqlalchemy"].db

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: enterprise_initiatives is a system/planning table
        # Get all enterprise initiatives
        initiatives_query = text(
            """
            SELECT ei.*, bd.name as domain_name, bd.code as domain_code,
                   uc.name as primary_capability_name
            FROM enterprise_initiatives ei
            LEFT JOIN business_domains bd ON ei.domain_id = bd.id
            LEFT JOIN capability_initiative_mapping cim ON ei.id = cim.initiative_id AND cim.impact_level = 'primary'
            LEFT JOIN unified_capabilities uc ON cim.capability_id = uc.id
            ORDER BY ei.start_date, ei.strategic_weight DESC
        """
        )
        initiatives_result = db.session.execute(initiatives_query)
        initiatives = [dict(row) for row in initiatives_result]

        # tenant-exempt: unified_capabilities + capability_initiative_mapping are planning tables
        # Get capability mappings
        capabilities_query = text(
            """
            SELECT uc.*, bd.name as domain_name, bd.code as domain_code,
                   COUNT(cim.id) as mapped_initiatives,
                   AVG(cim.relevance_score) as avg_relevance
            FROM unified_capabilities uc
            LEFT JOIN business_domains bd ON uc.domain_id = bd.id
            LEFT JOIN capability_initiative_mapping cim ON uc.id = cim.capability_id
            GROUP BY uc.id
            ORDER BY uc.strategic_importance DESC, uc.name
        """
        )
        capabilities_result = db.session.execute(capabilities_query)
        capabilities = [dict(row) for row in capabilities_result]

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: archimate_components is a planning table
        # Get ArchiMate components
        archimate_query = text(
            """
            SELECT ac.*, ei.name as initiative_name
            FROM archimate_components ac
            JOIN enterprise_initiatives ei ON ac.initiative_id = ei.id
            ORDER BY ac.layer, ac.component_type
        """
        )
        archimate_result = db.session.execute(archimate_query)
        archimate_components = [dict(row) for row in archimate_result]

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: initiative_dependencies is a planning table
        # Get dependencies
        dependencies_query = text(
            """
            SELECT id.*,
                   source.name as source_name,
                   target.name as target_name
            FROM initiative_dependencies id
            JOIN enterprise_initiatives source ON id.source_id = source.id
            JOIN enterprise_initiatives target ON id.target_id = target.id
            ORDER BY id.dependency_type, id.strength DESC
        """
        )
        dependencies_result = db.session.execute(dependencies_query)
        dependencies = [dict(row) for row in dependencies_result]

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: initiative_resources is a planning table
        # Get resource allocation
        resources_query = text(
            """
            SELECT ir.*, ei.name as initiative_name
            FROM initiative_resources ir
            JOIN enterprise_initiatives ei ON ir.initiative_id = ei.id
            ORDER BY ir.resource_type, ir.allocation_percentage DESC
        """
        )
        resources_result = db.session.execute(resources_query)  # tenant-exempt
        resources = [dict(row) for row in resources_result]

        # Calculate enterprise metrics
        total_investment = sum(i["total_investment"] or 0 for i in initiatives)
        expected_roi = sum(i["expected_roi"] or 0 for i in initiatives)
        avg_progress = (
            sum(i["progress_percentage"] or 0 for i in initiatives) / len(initiatives)
            if initiatives
            else 0
        )

        # Identify cross-roadmap gaps
        gaps = identify_cross_roadmap_gaps(initiatives, capabilities, archimate_components)

        return jsonify(
            {
                "initiatives": initiatives,
                "capabilities": capabilities,
                "archimate_components": archimate_components,
                "dependencies": dependencies,
                "resources": resources,
                "metrics": {
                    "total_initiatives": len(initiatives),
                    "total_capabilities": len(capabilities),
                    "total_archimate_components": len(archimate_components),
                    "total_dependencies": len(dependencies),
                    "total_investment": total_investment,
                    "expected_roi": expected_roi,
                    "average_progress": round(avg_progress, 1),
                },
                "gaps": gaps,
                "timeline": {
                    "start_date": min(
                        i["start_date"] for i in initiatives if i["start_date"]
                    ).isoformat()
                    if initiatives
                    else None,
                    "end_date": max(i["end_date"] for i in initiatives if i["end_date"]).isoformat()
                    if initiatives
                    else None,
                },
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting enterprise canvas: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@enterprise_bp.route("/synchronize-timelines", methods=["POST"])
@login_required
@audit_log("synchronize_timelines")
def synchronize_timelines():
    """Synchronize timelines across all roadmap systems"""
    try:
        db = current_app.extensions["sqlalchemy"].db

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: enterprise_initiatives is a planning table
        # Get current timeline bounds
        timeline_query = text(
            """
            -- enterprise_initiatives names its planned dates planned_start_date /
            -- planned_end_date; there are no start_date / end_date columns, so the
            -- previous form raised UndefinedColumn on every call.
            SELECT MIN(planned_start_date) as min_start, MAX(planned_end_date) as max_end
            FROM enterprise_initiatives
            WHERE planned_start_date IS NOT NULL AND planned_end_date IS NOT NULL
        """
        )
        result = db.session.execute(timeline_query)  # tenant-exempt
        timeline = result.fetchone()

        if not timeline.min_start or not timeline.max_end:
            return jsonify({"error": "No valid timeline found"}), 400

        # Generate synchronized timeline periods
        timeline_periods = generate_timeline_periods(timeline.min_start, timeline.max_end, "months")

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: enterprise_initiatives is a planning table
        # Update all roadmap systems with synchronized timeline
        update_query = text(
            """
            -- planned_start_date / planned_end_date are the real column names;
            -- start_date / end_date do not exist on this table.
            UPDATE enterprise_initiatives
            SET updated_at = :now
            WHERE planned_start_date >= :start_date AND planned_end_date <= :end_date
        """
        )
        db.session.execute(  # tenant-exempt
            update_query,
            {
                "now": datetime.utcnow(),
                "start_date": timeline.min_start,
                "end_date": timeline.max_end,
            },
        )
        db.session.commit()

        return jsonify(
            {
                "timeline_periods": timeline_periods,
                "synchronization_date": datetime.utcnow().isoformat(),
                "message": "Timeline synchronization completed",
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error synchronizing timelines: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@enterprise_bp.route("/dependencies", methods=["GET", "POST"])
@login_required
@audit_log("manage_dependencies")
def manage_dependencies():
    """Get or create initiative dependencies"""
    try:
        db = current_app.extensions["sqlalchemy"].db

        if request.method == "GET":
            # tenancy-ok: enterprise planning table, cross-organizational by design
            # tenant-exempt: initiative_dependencies is a planning table
            # Get all dependencies with initiative details
            query = text(
                """
                -- program_type ('strategic' | 'operational' | 'compliance' |
                -- 'innovation') is the initiative's portfolio classification;
                -- there is no portfolio_type column on enterprise_initiatives.
                SELECT id.*,
                       source.name as source_name, source.program_type as source_type,
                       target.name as target_name, target.program_type as target_type
                FROM initiative_dependencies id
                JOIN enterprise_initiatives source ON id.source_id = source.id
                JOIN enterprise_initiatives target ON id.target_id = target.id
                ORDER BY id.dependency_type, id.strength DESC
            """
            )
            result = db.session.execute(query)  # tenant-exempt
            dependencies = [dict(row) for row in result]

            return jsonify({"dependencies": dependencies})

        elif request.method == "POST":
            # tenant-exempt: initiative_dependencies is a planning table
            # Create new dependency
            data = request.get_json()

            insert_query = text(
                """
                INSERT INTO initiative_dependencies
                (source_id, target_id, dependency_type, strength, description, lag_days)
                VALUES (:source_id, :target_id, :dependency_type, :strength, :description, :lag_days)
            """
            )
            db.session.execute(insert_query, data)  # tenant-exempt
            db.session.commit()

            return jsonify(
                {
                    "message": "Dependency created successfully",
                    "dependency_id": db.session.execute(  # tenant-exempt
                        text("SELECT last_insert_rowid()")  # tenant-exempt
                    ).scalar(),
                }
            )

    except Exception as e:
        current_app.logger.error(f"Error managing dependencies: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@enterprise_bp.route("/analytics", methods=["GET"])
@login_required
def get_analytics():
    """Get enterprise analytics and insights"""
    try:
        db = current_app.extensions["sqlalchemy"].db

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: enterprise_initiatives is a planning table
        # Portfolio analytics
        portfolio_query = text(
            """
            -- Real column names on enterprise_initiatives: the portfolio
            -- classification is program_type (aliased so the API key is
            -- unchanged), the budget figure is approved_budget, and progress is
            -- completion_percentage. portfolio_type / total_investment /
            -- progress_percentage do not exist. There is no ROI column at all,
            -- so total_roi is dropped rather than substituted with a different
            -- measure (see CLAUDE.md: never invent data).
            SELECT program_type as portfolio_type,
                   COUNT(*) as count,
                   SUM(approved_budget) as total_investment,
                   AVG(completion_percentage) as avg_progress
            FROM enterprise_initiatives
            GROUP BY program_type
        """
        )
        portfolio_result = db.session.execute(portfolio_query)
        portfolio_analytics = [dict(row) for row in portfolio_result]

        # tenant-exempt: unified_capabilities + capability_initiative_mapping are planning tables
        # Capability coverage
        capability_query = text(
            """
            SELECT
                COUNT(*) as total_capabilities,
                COUNT(DISTINCT cim.capability_id) as mapped_capabilities,
                ROUND(COUNT(DISTINCT cim.capability_id) * 100.0 / COUNT(*), 1) as coverage_percentage
            FROM unified_capabilities uc
            LEFT JOIN capability_initiative_mapping cim ON uc.id = cim.capability_id
        """
        )
        capability_result = db.session.execute(capability_query)
        capability_analytics = dict(capability_result.fetchone())

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: enterprise_initiatives is a planning table
        # Risk assessment
        risk_query = text(
            """
            -- approved_budget is the investment column; total_investment does not exist.
            SELECT risk_level, COUNT(*) as count, SUM(approved_budget) as investment_at_risk
            FROM enterprise_initiatives
            WHERE risk_level IS NOT NULL
            GROUP BY risk_level
            ORDER BY
                CASE risk_level
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END
        """
        )
        risk_result = db.session.execute(risk_query)
        risk_analytics = [dict(row) for row in risk_result]

        # tenancy-ok: enterprise planning table, cross-organizational by design
        # tenant-exempt: enterprise_initiatives is a planning table
        # Timeline analysis
        timeline_query = text(
            """
            SELECT
                -- planned_start_date / approved_budget are the real columns;
                -- start_date / total_investment do not exist on this table.
                DATE(planned_start_date, 'start of month') as month,
                COUNT(*) as initiatives_starting,
                SUM(approved_budget) as monthly_investment
            FROM enterprise_initiatives
            WHERE planned_start_date IS NOT NULL
            GROUP BY DATE(planned_start_date, 'start of month')
            ORDER BY month
        """
        )
        timeline_result = db.session.execute(timeline_query)
        timeline_analytics = [dict(row) for row in timeline_result]

        # tenant-exempt: initiative_resources is a planning table
        # Resource utilization
        resource_query = text(
            """
            SELECT resource_type,
                   COUNT(*) as allocations,
                   SUM(cost) as total_cost,
                   AVG(allocation_percentage) as avg_allocation
            FROM initiative_resources
            GROUP BY resource_type
        """
        )
        resource_result = db.session.execute(resource_query)  # tenant-exempt
        resource_analytics = [dict(row) for row in resource_result]

        return jsonify(
            {
                "portfolio_analytics": portfolio_analytics,
                "capability_coverage": capability_analytics,
                "risk_assessment": risk_analytics,
                "timeline_analysis": timeline_analytics,
                "resource_utilization": resource_analytics,
                "generated_at": datetime.utcnow().isoformat(),
            }
        )

    except Exception as e:
        current_app.logger.error(f"Error getting analytics: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


@enterprise_bp.route("/change-requests", methods=["GET", "POST"])
@login_required
@audit_log("manage_change_requests")
def manage_change_requests():
    """Manage change requests for governance"""
    try:
        db = current_app.extensions["sqlalchemy"].db

        if request.method == "GET":
            # tenant-exempt: change_requests is a governance table
            # Get all change requests
            query = text(
                """
                SELECT cr.*,
                       requester.username as requester_name,
                       approver.username as approver_name
                FROM change_requests cr
                LEFT JOIN users requester ON cr.requested_by = requester.id
                LEFT JOIN users approver ON cr.approved_by = approver.id
                ORDER BY cr.created_at DESC
            """
            )
            result = db.session.execute(query)  # tenant-exempt
            change_requests = [dict(row) for row in result]

            return jsonify({"change_requests": change_requests})

        elif request.method == "POST":
            # tenant-exempt: change_requests is a governance table
            # Create new change request
            data = request.get_json()

            insert_query = text(
                """
                INSERT INTO change_requests
                (title, description, change_type, roadmap_type, entity_type, entity_id,
                 old_values, new_values, priority, requested_by)
                VALUES (:title, :description, :change_type, :roadmap_type, :entity_type, :entity_id,
                        :old_values, :new_values, :priority, :requested_by)
            """
            )
            db.session.execute(insert_query, data)  # tenant-exempt
            db.session.commit()

            return jsonify(
                {
                    "message": "Change request created successfully",
                    "request_id": db.session.execute(text("SELECT last_insert_rowid()")).scalar(),  # raw-sql-ok: system function
                }
            )

    except Exception as e:
        current_app.logger.error(f"Error managing change requests: {str(e)}")
        return jsonify({"error": "An internal error occurred"}), 500


# Helper functions
def identify_cross_roadmap_gaps(initiatives, capabilities, archimate_components):
    """Identify gaps and inconsistencies across roadmaps"""
    gaps = []

    # Check for initiatives without capability mapping
    initiative_ids = {i["id"] for i in initiatives}
    mapped_initiatives = {
        c["initiative_id"]
        for c in [
            dict(row)
            for row in current_app.extensions["sqlalchemy"].db.session.execute(
                text("SELECT DISTINCT initiative_id FROM capability_initiative_mapping")  # tenant-exempt: planning table
            )
        ]
    }

    unmapped_initiatives = initiative_ids - mapped_initiatives
    if unmapped_initiatives:
        gaps.append(
            {
                "type": "unmapped_initiatives",
                "severity": "high",
                "count": len(unmapped_initiatives),
                "description": f"{len(unmapped_initiatives)} initiatives have no capability mapping",
            }
        )

    # Check for capabilities without initiatives
    capability_ids = {c["id"] for c in capabilities}
    mapped_capabilities = {
        c["capability_id"]
        for c in [
            dict(row)
            for row in current_app.extensions["sqlalchemy"].db.session.execute(
                text("SELECT DISTINCT capability_id FROM capability_initiative_mapping")  # tenant-exempt: planning table
            )
        ]
    }

    orphaned_capabilities = capability_ids - mapped_capabilities
    if orphaned_capabilities:
        gaps.append(
            {
                "type": "orphaned_capabilities",
                "severity": "medium",
                "count": len(orphaned_capabilities),
                "description": f"{len(orphaned_capabilities)} capabilities have no supporting initiatives",
            }
        )

    # Check for missing ArchiMate components
    initiatives_with_archimate = {ac["initiative_id"] for ac in archimate_components}
    missing_archimate = initiative_ids - initiatives_with_archimate
    if missing_archimate:
        gaps.append(
            {
                "type": "missing_archimate",
                "severity": "medium",
                "count": len(missing_archimate),
                "description": f"{len(missing_archimate)} initiatives lack ArchiMate components",
            }
        )

    return gaps


def generate_timeline_periods(start_date, end_date, display_type):
    """Generate timeline periods for synchronized view"""
    periods = []
    current = datetime(start_date)
    end = datetime(end_date)

    if display_type == "months":
        while current <= end:
            periods.append(
                {
                    "label": current.strftime("%b %Y"),
                    "start": current,
                    "end": (current.replace(day=1) + timedelta(days=32)).replace(day=1)
                    - timedelta(days=1),
                }
            )
            current = (current.replace(day=1) + timedelta(days=32)).replace(day=1)

    elif display_type == "quarters":
        while current <= end:
            quarter = (current.month - 1) // 3 + 1
            periods.append(
                {
                    "label": f"Q{quarter} {current.year}",
                    "start": current,
                    "end": datetime(current.year, quarter * 3 + 1, 1) - timedelta(days=1),
                }
            )
            current = (current.replace(month=quarter * 3 + 1, day=1) + timedelta(days=32)).replace(
                day=1
            )

    elif display_type == "years":
        while current <= end:
            periods.append(
                {
                    "label": str(current.year),
                    "start": current,
                    "end": datetime(current.year, 12, 31),
                }
            )
            current = datetime(current.year + 1, 1, 1)

    return periods
