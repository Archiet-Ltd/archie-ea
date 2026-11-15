"""
Routes for the page-aware AI guide.
"""

from flask import jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from app.modules.ai_chat.services.page_guide_registry import get_entry_for_page_key
from app.modules.ai_chat.services.page_guide_service import PageGuideService
from app.schemas.api_schemas import (
    PageGuideContextSchema,
    PageGuideMessageSchema,
    _load_and_validate,
)
from app.services.feature_flag_service import FeatureFlagService
from app.services.rate_limiter import rate_limit
from app.utils.validators import sanitize_html, validation_error_response

from . import unified_ai_chat_bp


def _feature_guard(require_llm=True):
    if not PageGuideService.is_enabled():
        return (
            jsonify(
                {
                    "success": False,
                    "error": "service_unavailable",
                    "message": "The page guide is not enabled.",
                }
            ),
            503,
        )
    # Saved history is a record operation, not inference. Do not resolve a
    # provider for read/clear. Generation must still check configuration even
    # when AI_CHAT_ENABLED=True overrides automatic feature detection.
    if require_llm and not FeatureFlagService._is_llm_configured():
        from app.modules.ai_chat.services.failure_messages import (
            ai_unavailable_message,
        )

        is_admin = bool(getattr(current_user, "is_org_admin", False))
        return jsonify({
            "error": "service_unavailable",
            "message": ai_unavailable_message(FeatureFlagService.FEATURE_CHAT, is_admin),
            "feature": FeatureFlagService.FEATURE_CHAT,
        }), 503
    return None


def _guide_mode_for_page_key(page_key: str) -> str:
    return "generic" if page_key == "admin.generic" else "specialized"


@unified_ai_chat_bp.route("/guide/history", methods=["GET"])
@login_required
def get_page_guide_history():
    feature_guard = _feature_guard(require_llm=False)
    if feature_guard:
        return feature_guard

    payload = {
        "page_key": request.args.get("page_key"),
        "scope_key": request.args.get("scope_key"),
    }
    validated, err = _load_and_validate(PageGuideContextSchema(), payload)
    if err is not None:
        return err
    if get_entry_for_page_key(validated["page_key"]) is None:
        return validation_error_response("Unsupported page guide context")

    service = PageGuideService(current_user.id)
    history = service.get_history(validated["page_key"], validated["scope_key"])
    return jsonify(
        {
            "success": True,
            "messages": history,
            "page_key": validated["page_key"],
            "scope_key": validated["scope_key"],
            "guide_mode": _guide_mode_for_page_key(validated["page_key"]),
        }
    )


@unified_ai_chat_bp.route("/guide/history/clear", methods=["POST"])
@login_required
@audit_log("clear_page_guide_history")
def clear_page_guide_history():
    feature_guard = _feature_guard(require_llm=False)
    if feature_guard:
        return feature_guard

    data = request.get_json(silent=True)
    if data is None:
        return validation_error_response("Request body is required")

    validated, err = _load_and_validate(PageGuideContextSchema(), data)
    if err is not None:
        return err
    if get_entry_for_page_key(validated["page_key"]) is None:
        return validation_error_response("Unsupported page guide context")

    service = PageGuideService(current_user.id)
    result = service.clear_history(validated["page_key"], validated["scope_key"])
    result.update(
        {
            "page_key": validated["page_key"],
            "scope_key": validated["scope_key"],
            "guide_mode": _guide_mode_for_page_key(validated["page_key"]),
        }
    )
    return jsonify(result)


@unified_ai_chat_bp.route("/guide/message", methods=["POST"])
@login_required
@rate_limit(120, "1h")
@audit_log("page_guide_message")
def send_page_guide_message():
    feature_guard = _feature_guard()
    if feature_guard:
        return feature_guard

    data = request.get_json(silent=True)
    if data is None:
        return validation_error_response("Request body is required")

    validated, err = _load_and_validate(PageGuideMessageSchema(), data)
    if err is not None:
        return err
    if get_entry_for_page_key(validated["page_key"]) is None:
        return validation_error_response("Unsupported page guide context")

    message = sanitize_html(validated["message"].strip())
    service = PageGuideService(current_user.id)
    result = service.answer_message(
        page_key=validated["page_key"],
        scope_key=validated["scope_key"],
        message=message,
        role_name=getattr(current_user, "role_name", "user"),
        page_title=validated.get("page_title"),
    )
    result.update(
        {
            "page_key": validated["page_key"],
            "scope_key": validated["scope_key"],
            "guide_mode": _guide_mode_for_page_key(validated["page_key"]),
        }
    )
    return jsonify(result)
