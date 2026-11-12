"""
Analytics, natural language query, and recommendation routes.

Routes: analytics/usage, analytics/domains, analytics/quality,
        extensions/analytics/portfolio-health, nl-query, nl-query/examples,
        recommendations, recommendations/quick-stats, recommendations/alerts.
"""

import logging

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from . import unified_ai_chat_bp
from .chat_views import get_chat_service

logger = logging.getLogger(__name__)


def _require_chat_service():
    """Return the chat service, or raise so the caller answers with an error.

    ``get_chat_service()`` returns ``None`` when construction fails. Answering
    that with an all-zero analytics payload and ``success: true`` presented a
    failure as a measurement — see "Never invent data" in CLAUDE.md.
    """
    chat_service = get_chat_service()
    if chat_service is None:
        raise RuntimeError("chat service unavailable")
    return chat_service


@unified_ai_chat_bp.route("/analytics/usage", methods=["GET"])
@login_required
def get_usage_analytics():
    """Get AI chat usage analytics for the current user."""
    try:
        analytics = _require_chat_service().get_usage_analytics(current_user.id)
        return jsonify({"success": True, "analytics": analytics})

    except Exception as e:
        logger.error(f"Usage analytics failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Could not load usage analytics"}), 500


@unified_ai_chat_bp.route("/analytics/domains", methods=["GET"])
@login_required
def get_domain_analytics():
    """Get usage analytics by domain."""
    try:
        analytics = _require_chat_service().get_domain_analytics()
        return jsonify({"success": True, "analytics": analytics})

    except Exception as e:
        logger.error(f"Domain analytics failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Could not load domain analytics"}), 500


@unified_ai_chat_bp.route("/analytics/quality", methods=["GET"])
@login_required
def get_quality_metrics():
    """Get AI response quality metrics."""
    try:
        metrics = _require_chat_service().get_quality_metrics()
        return jsonify({"success": True, "metrics": metrics})

    except Exception as e:
        logger.error(f"Quality metrics failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Could not load quality metrics"}), 500


@unified_ai_chat_bp.route("/extensions/analytics/portfolio-health", methods=["GET"])
@login_required
def get_portfolio_health():
    """Get portfolio health score and metrics."""
    try:
        scope = request.args.get("scope", "all")
        ALLOWED_SCOPES = {"all", "active", "retiring", "planning", "critical"}
        if scope not in ALLOWED_SCOPES:
            scope = "all"

        chat_service = get_chat_service()
        result = chat_service.get_advanced_analytics(
            "portfolio_health", {"scope": scope}
        )

        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"Error getting portfolio health: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# NATURAL LANGUAGE QUERY ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/nl-query", methods=["POST"])
@login_required
@audit_log("natural_language_query")
def natural_language_query():
    """
    Process a natural language query against the database.

    Expected JSON body:
    {
        "query": "Show me all applications without business owner",
        "persona": "enterprise_architect" (optional)
    }
    """
    try:
        from app.services.natural_language_query_service import (
            NaturalLanguageQueryService,
        )

        data = request.get_json()
        if not data or not data.get("query"):
            return jsonify({"success": False, "error": "Query is required"}), 400

        query = data.get("query")
        persona = data.get("persona")

        nl_service = NaturalLanguageQueryService()
        result = nl_service.process_query(query, persona)

        return jsonify(result)

    except Exception as e:
        current_app.logger.error(f"Error processing NL query: {e}", exc_info=True)
        return (
            jsonify(
                {
                    "success": False,
                    "error": "An internal error occurred",
                    "suggestions": [
                        "Try: 'Show all applications without business owner'",
                        "Try: 'List capabilities with maturity below 3'",
                        "Try: 'Which vendors expire in 90 days?'",
                    ],
                }
            ),
            500,
        )


@unified_ai_chat_bp.route("/nl-query/examples")
@login_required
def get_query_examples():
    """Get example queries for the NL Query interface."""
    try:
        from app.services.natural_language_query_service import (
            NaturalLanguageQueryService,
        )

        nl_service = NaturalLanguageQueryService()
        examples = nl_service.get_supported_queries()
        return jsonify(examples)

    except Exception as e:
        current_app.logger.error(f"Error getting query examples: {e}", exc_info=True)
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


# ============================================================================
# RECOMMENDATIONS ENGINE ROUTES
# ============================================================================


@unified_ai_chat_bp.route("/first-questions")
@login_required
def get_first_questions():
    """The applications the chat offers a first question about: those the organisation has
    recorded relationships for, most connected first, so each question has a cited answer.
    Counted by the same reader as the overview's "most connected" line."""
    from app import db
    from app.middleware.tenant_context import current_org_id
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.dashboard.services.overview_figures import element_each_name_means, relationship_ends

    org_id = current_org_id()
    if org_id is None:
        return jsonify({"questions": [], "applications": 0})
    # How many applications are recorded tells the page, when there is no card to offer, whether to
    # say that none are or that their connections are not.
    # Counted by name: an application and the element recorded for it are one application.
    from app.utils.duplicate_guard import folded_column, normalize_name

    recorded = max(
        db.session.execute(
            db.select(db.func.count(db.distinct(folded_column(ApplicationComponent.name)))).where(
                ApplicationComponent.organization_id == org_id
            )
        ).scalar_one(),
        db.session.execute(
            db.select(db.func.count(db.distinct(folded_column(ArchiMateElement.name)))).where(
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.type.in_(("ApplicationComponent", "Application")),
            )
        ).scalar_one(),
    )
    ends = relationship_ends(org_id)
    if not ends:
        return jsonify({"questions": [], "applications": recorded})
    named = db.session.execute(
        db.select(ArchiMateElement.id, ArchiMateElement.name).where(
            ArchiMateElement.organization_id == org_id,
            ArchiMateElement.type.in_(("ApplicationComponent", "Application")),
            ArchiMateElement.id.in_(list(ends)),
        )
    ).all()
    # An application and a second element with its name are one application seen twice, so it is
    # offered once: the element its name means by the rule every surface shares.
    # Every element carrying such a name is weighed, of any type, as a typed question weighs them.
    names = {normalize_name(row.name) for row in named if row.name}
    sharing = db.session.execute(
        db.select(ArchiMateElement.id, ArchiMateElement.name).where(
            ArchiMateElement.organization_id == org_id, folded_column(ArchiMateElement.name).in_(names)
        )
    ).all()
    meant = set(element_each_name_means(org_id, sharing, ends).values())
    chosen = sorted((row for row in sharing if row.id in meant), key=lambda row: (-ends[row.id], row.id))
    return jsonify(
        {
            "applications": recorded,
            "questions": [
                {
                    "element_id": row.id,
                    "name": row.name,
                    "question": "What breaks if %s fails?" % row.name,
                    "detail": "%d relationship%s recorded" % (ends[row.id], "" if ends[row.id] == 1 else "s"),
                }
                for row in chosen
            ][:4]
        }
    )


@unified_ai_chat_bp.route("/recommendations")
@login_required
def get_recommendations():
    """
    Get actionable recommendations and alerts.

    Query params:
    - persona: Filter by persona (optional)
    - refresh: Force refresh cache (optional)
    """
    try:
        from app.services.recommendations_engine_service import (
            RecommendationsEngineService,
        )

        persona = request.args.get("persona")
        refresh = request.args.get("refresh", "false").lower() == "true"

        rec_service = RecommendationsEngineService()
        recommendations = rec_service.get_all_recommendations(
            persona=persona, refresh=refresh
        )

        return jsonify(recommendations)
    except Exception as e:
        current_app.logger.error(f"Error getting recommendations: {e}", exc_info=True)
        return (
            jsonify(
                {
                    "error": "An internal error occurred",
                    "alerts": [],
                    "recommendations": [],
                    "summary": {"total": 0},
                }
            ),
            500,
        )


@unified_ai_chat_bp.route("/recommendations/quick-stats")
@login_required
def get_quick_stats():
    """Get quick statistics for dashboard display."""
    try:
        from app.services.recommendations_engine_service import (
            RecommendationsEngineService,
        )

        rec_service = RecommendationsEngineService()
        stats = rec_service.get_quick_stats()

        return jsonify(stats)
    except Exception as e:
        current_app.logger.error(f"Error getting quick stats: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred"}), 500


@unified_ai_chat_bp.route("/recommendations/alerts")
@login_required
def get_alerts_only():
    """Get only alerts (for notification badges, etc.)."""
    try:
        from app.services.recommendations_engine_service import (
            RecommendationsEngineService,
        )

        persona = request.args.get("persona")
        rec_service = RecommendationsEngineService()
        data = rec_service.get_all_recommendations(persona=persona)

        # Return only alerts with summary
        return jsonify(
            {
                "alerts": data.get("alerts", []),
                "summary": data.get("summary", {}),
                "health_score": data.get("health_score", 0),
            }
        )
    except Exception as e:
        current_app.logger.error(f"Error getting alerts: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred", "alerts": []}), 500


