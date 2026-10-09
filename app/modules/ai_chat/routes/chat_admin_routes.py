"""
AI Chat Admin Routes — Persona prompt management + feedback analytics.

Provides CRUD-style admin UI for viewing and overriding AI chat persona
configurations stored in PERSONA_CONFIGS (hardcoded defaults) with
database-backed overrides via AIPromptTemplate.

Also provides the AI Chat feedback analytics dashboard and data API.
"""

import json
import logging
from datetime import datetime, timedelta

from flask import abort, g, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db
from app.middleware.tenant_decorators import platform_admin_required
from app.models.ai_service import AIPromptTemplate
from app.models.user import User
from app.modules.ai_chat.services.multi_domain_chat_service import PERSONA_CONFIGS

from . import unified_ai_chat_bp

logger = logging.getLogger(__name__)


def _require_admin():
    """Abort 403 if current user is not an admin of the ACTIVE organisation.

    R2-2 (PR 428 round 3): ``current_user.is_admin`` (no call) is a bound
    method reference, always truthy, so this never refused anyone -- a
    Viewer of the active organisation could read and write every persona
    prompt override and read the organisation's AI-chat analytics. Judged
    the same way ``org_admin_required``/``admin_required`` do elsewhere in
    this PR, against ``g.current_org_id`` rather than a global flag.
    """
    from app.middleware.tenant_decorators import is_active_org_admin

    if not is_active_org_admin():
        abort(403)


def _override_key(persona_key):
    """Return the AIPromptTemplate.name used to store a persona override."""
    return f"persona_override_{persona_key}"


def _get_override(persona_key):
    """Fetch the DB override for a persona, or None."""
    try:
        return AIPromptTemplate.query.filter_by(
            name=_override_key(persona_key)
        ).first()
    except Exception:
        logger.warning("Failed to query AIPromptTemplate for %s", persona_key)
        return None


def _build_persona_data(persona_key, config, override=None):
    """Merge hardcoded config with optional DB override into a response dict."""
    data = {
        "key": persona_key,
        "name": config.get("name", persona_key),
        "icon": config.get("icon", ""),
        "color": config.get("color", ""),
        "description": config.get("description", ""),
        "expertise": config.get("expertise", []),
        "focus_areas": config.get("focus_areas", []),
        "default_domain": config.get("default_domain", ""),
        "context_priority": config.get("context_priority", []),
        "sample_prompts": config.get("sample_prompts", []),
        "has_override": False,
        "system_prompt_override": "",
    }
    if override:
        data["has_override"] = True
        data["description"] = override.description or data["description"]
        data["system_prompt_override"] = override.system_prompt or ""
        # Sample prompts stored as JSON in user_prompt_template
        if override.user_prompt_template:
            try:
                stored = json.loads(override.user_prompt_template)
                if isinstance(stored, list):
                    data["sample_prompts"] = stored
            except (json.JSONDecodeError, TypeError):
                logger.exception("Failed to JSON parsing")
                pass
    return data


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@unified_ai_chat_bp.route("/admin/prompts")
@login_required
@platform_admin_required
def admin_prompts_page():
    """Render the admin persona prompt management page."""
    return render_template("ai_chat/admin_prompts.html")


@unified_ai_chat_bp.route("/admin/prompts/data")
@login_required
@platform_admin_required
def admin_prompts_data():
    """JSON API: return all persona configs merged with DB overrides."""

    personas = []
    for key, config in PERSONA_CONFIGS.items():
        override = _get_override(key)
        personas.append(_build_persona_data(key, config, override))

    return jsonify({"personas": personas})


@unified_ai_chat_bp.route("/admin/prompts/<persona_key>/update", methods=["POST"])
@login_required
@platform_admin_required
def admin_prompt_update(persona_key):
    """Update (or create) a DB override for a persona's prompt config.

    R3-3 (PR 428 round 4, settling with review-pr430-v3.md's DEF-6/PR 430
    ruling): ``_require_admin()`` (active-org) is not the right authority
    for ``AIPromptTemplate`` -- a table with no organisation column, whose
    rows are shown to every tenant. ``platform_admin_required`` is the
    operative check here; ``_require_admin()`` is kept as well (a no-op
    once platform_admin_required has already passed) so the function stays
    safe to call on its own from any future caller that doesn't stack the
    decorator.
    """
    _require_admin()

    if persona_key not in PERSONA_CONFIGS:
        return jsonify({"error": f"Unknown persona: {persona_key}"}), 404

    payload = request.get_json(silent=True) or {}
    description = (payload.get("description") or "").strip()
    system_prompt = (payload.get("system_prompt") or "").strip()
    sample_prompts = payload.get("sample_prompts")

    if not description and not system_prompt and not sample_prompts:
        return jsonify({"error": "No fields provided to update"}), 400

    override_name = _override_key(persona_key)
    override = AIPromptTemplate.query.filter_by(name=override_name).first()

    if not override:
        override = AIPromptTemplate(
            name=override_name,
            description=description or PERSONA_CONFIGS[persona_key].get("description", ""),
            system_prompt=system_prompt or "",
            user_prompt_template=json.dumps(
                sample_prompts
                if isinstance(sample_prompts, list)
                else PERSONA_CONFIGS[persona_key].get("sample_prompts", [])
            ),
            category="persona_override",
        )
        db.session.add(override)
    else:
        if description:
            override.description = description
        if system_prompt:
            override.system_prompt = system_prompt
        if isinstance(sample_prompts, list):
            override.user_prompt_template = json.dumps(sample_prompts)
        override.updated_at = datetime.utcnow()

    try:
        db.session.commit()
        logger.info("Persona override saved for %s by user %s", persona_key, current_user.id)
    except Exception:
        db.session.rollback()
        logger.exception("Failed to save persona override for %s", persona_key)
        return jsonify({"error": "Database error saving override"}), 500

    config = PERSONA_CONFIGS[persona_key]
    updated = _build_persona_data(persona_key, config, override)
    return jsonify({"success": True, "persona": updated})


@unified_ai_chat_bp.route("/admin/prompts/<persona_key>/reset", methods=["POST"])
@login_required
@platform_admin_required
def admin_prompt_reset(persona_key):
    """Remove the DB override for a persona, reverting to hardcoded defaults.

    R3-3: same global-resource reasoning as ``admin_prompt_update`` above.
    """
    _require_admin()

    if persona_key not in PERSONA_CONFIGS:
        return jsonify({"error": f"Unknown persona: {persona_key}"}), 404

    override_name = _override_key(persona_key)
    override = AIPromptTemplate.query.filter_by(name=override_name).first()

    if override:
        try:
            db.session.delete(override)
            db.session.commit()
            logger.info("Persona override reset for %s by user %s", persona_key, current_user.id)
        except Exception:
            db.session.rollback()
            logger.exception("Failed to reset persona override for %s", persona_key)
            return jsonify({"error": "Database error resetting override"}), 500

    config = PERSONA_CONFIGS[persona_key]
    default_data = _build_persona_data(persona_key, config)
    return jsonify({"success": True, "persona": default_data})


# ---------------------------------------------------------------------------
# Analytics dashboard
# ---------------------------------------------------------------------------


def _safe_import_analytics_models():
    """Import feedback / audit models; return None for missing ones."""
    AIChatFeedback = None
    AIChatAuditLog = None
    # Each returns None on failure and the dashboard degrades to the half it
    # can compute. That is the right behaviour, but the log has to say WHICH
    # half went missing: "Failed to operation" told an administrator staring at
    # a half-empty analytics page precisely nothing.
    try:
        from app.models.ai_chat_feedback import AIChatFeedback
    except Exception:
        logger.exception(
            "AIChatFeedback model unavailable; AI chat analytics will report "
            "no feedback data"
        )
    try:
        from app.models.ai_chat_audit_log import AIChatAuditLog
    except Exception:
        logger.exception(
            "AIChatAuditLog model unavailable; AI chat analytics will report "
            "no audit data"
        )
    return AIChatFeedback, AIChatAuditLog


@unified_ai_chat_bp.route("/admin/analytics")
@login_required
def admin_analytics_dashboard():
    """Render the AI Chat feedback analytics dashboard."""
    _require_admin()
    return render_template("ai_chat/analytics_dashboard.html")


@unified_ai_chat_bp.route("/admin/analytics/data")
@login_required
def admin_analytics_data():
    """Return aggregated AI Chat analytics as JSON.

    Query params:
        days (int): Look-back window in days (default 30, max 365).
    """
    _require_admin()

    try:
        days = min(int(request.args.get("days", 30)), 365)
    except (TypeError, ValueError):
        days = 30
    cutoff = datetime.utcnow() - timedelta(days=days)

    AIChatFeedback, AIChatAuditLog = _safe_import_analytics_models()

    result = {
        "days": days,
        "feedback_summary": {
            "total_positive": 0,
            "total_negative": 0,
            "by_domain": {},
        },
        "usage_by_domain": [],
        "usage_by_persona": [],
        "provider_stats": [],
        "daily_usage": [],
        "top_templates": [],
        "total_messages": 0,
        "active_users": 0,
        "avg_response_time_ms": 0,
        "positive_pct": 0,
    }

    # --- Feedback summary ---
    # AIChatFeedback now carries TenantMixin, so do_orm_execute scopes this
    # SELECT to g.current_org_id. Before that it did not: message_text holds the
    # assistant's reply — portfolio content — and this aggregation, filtered on
    # created_at alone, counted every organisation's feedback together.
    # The explicit predicate is belt-and-braces: this view is admin-facing, and
    # a future caller running it outside a request context would be unfiltered.
    if AIChatFeedback is not None:
        try:
            fb_rows = (
                db.session.query(
                    AIChatFeedback.domain,
                    AIChatFeedback.rating,
                    func.count(AIChatFeedback.id),
                )
                .filter(AIChatFeedback.created_at >= cutoff)
                .filter(AIChatFeedback.organization_id == g.current_org_id)
                .group_by(AIChatFeedback.domain, AIChatFeedback.rating)
                .all()
            )
            total_pos = 0
            total_neg = 0
            by_domain = {}
            for domain_val, rating, cnt in fb_rows:
                d = domain_val or "unknown"
                if d not in by_domain:
                    by_domain[d] = {"positive": 0, "negative": 0}
                if rating == "up":
                    by_domain[d]["positive"] += cnt
                    total_pos += cnt
                else:
                    by_domain[d]["negative"] += cnt
                    total_neg += cnt
            result["feedback_summary"] = {
                "total_positive": total_pos,
                "total_negative": total_neg,
                "by_domain": by_domain,
            }
            total_fb = total_pos + total_neg
            result["positive_pct"] = (
                round(total_pos / total_fb * 100, 1) if total_fb else 0
            )
        except Exception as exc:
            logger.warning("Feedback query failed: %s", exc)

    # --- Audit-log based metrics ---
    # Neither AIChatAuditLog nor AIInteractionLog carries an organization_id
    # of its own -- ownership is only reachable via their user_id FK to
    # User (TenantMixin). Every query below joins User and filters on
    # User.organization_id == g.current_org_id, the same fix already
    # applied to the feedback summary above; without it this admin-facing
    # view showed every organisation's message counts, active-user counts,
    # usage-by-domain/persona/provider, daily usage, and top templates.
    if AIChatAuditLog is not None:
        try:
            audit_base = db.session.query(AIChatAuditLog).join(
                User, AIChatAuditLog.user_id == User.id
            ).filter(
                AIChatAuditLog.created_at >= cutoff,
                User.organization_id == g.current_org_id,
            )

            # Total messages
            total_msg = audit_base.with_entities(func.count(AIChatAuditLog.id)).scalar() or 0
            result["total_messages"] = total_msg

            # Active users (distinct user_id)
            active = audit_base.with_entities(
                func.count(func.distinct(AIChatAuditLog.user_id))
            ).scalar() or 0
            result["active_users"] = active

            # Average response time
            avg_rt = audit_base.filter(
                AIChatAuditLog.processing_time_ms.isnot(None)
            ).with_entities(func.avg(AIChatAuditLog.processing_time_ms)).scalar()
            result["avg_response_time_ms"] = round(avg_rt, 1) if avg_rt else 0

            # Usage by domain
            domain_rows = (
                audit_base.with_entities(
                    AIChatAuditLog.domain,
                    func.count(AIChatAuditLog.id),
                )
                .group_by(AIChatAuditLog.domain)
                .order_by(func.count(AIChatAuditLog.id).desc())
                .all()
            )
            result["usage_by_domain"] = [
                {"domain": d or "unknown", "count": c} for d, c in domain_rows
            ]

            # Usage by persona
            persona_rows = (
                audit_base.with_entities(
                    AIChatAuditLog.persona,
                    func.count(AIChatAuditLog.id),
                )
                .group_by(AIChatAuditLog.persona)
                .order_by(func.count(AIChatAuditLog.id).desc())
                .all()
            )
            result["usage_by_persona"] = [
                {"persona": p or "unknown", "count": c}
                for p, c in persona_rows
            ]

            # Provider stats
            provider_rows = (
                audit_base.with_entities(
                    AIChatAuditLog.provider_used,
                    func.count(AIChatAuditLog.id),
                    func.sum(
                        db.case(
                            (AIChatAuditLog.error_message.isnot(None), 1),
                            else_=0,
                        )
                    ),
                )
                .group_by(AIChatAuditLog.provider_used)
                .order_by(func.count(AIChatAuditLog.id).desc())
                .all()
            )
            result["provider_stats"] = [
                {
                    "provider": p or "unknown",
                    "count": c,
                    "errors": int(e or 0),
                }
                for p, c, e in provider_rows
            ]

            # Daily usage (last N days)
            daily_rows = (
                audit_base.with_entities(
                    func.date(AIChatAuditLog.created_at).label("day"),
                    func.count(AIChatAuditLog.id),
                )
                .group_by(func.date(AIChatAuditLog.created_at))
                .order_by(func.date(AIChatAuditLog.created_at))
                .all()
            )
            result["daily_usage"] = [
                {"date": str(day), "count": c} for day, c in daily_rows
            ]
        except Exception as exc:
            logger.warning("Audit log query failed: %s", exc)

    # --- Top templates ---
    try:
        from app.models.ai_service import AIInteractionLog

        tpl_rows = (
            db.session.query(
                AIPromptTemplate.name,
                func.count(AIInteractionLog.id),
            )
            .join(
                AIInteractionLog,
                AIInteractionLog.prompt_template_id == AIPromptTemplate.id,
            )
            .join(User, AIInteractionLog.user_id == User.id)
            .filter(
                AIInteractionLog.timestamp >= cutoff,
                User.organization_id == g.current_org_id,
            )
            .group_by(AIPromptTemplate.name)
            .order_by(func.count(AIInteractionLog.id).desc())
            .limit(10)
            .all()
        )
        result["top_templates"] = [
            {"name": name, "count": cnt} for name, cnt in tpl_rows
        ]
    except Exception as exc:
        logger.warning("Template query failed: %s", exc)

    return jsonify(result)
