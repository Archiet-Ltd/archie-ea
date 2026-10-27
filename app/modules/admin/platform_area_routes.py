"""Platform admin area: the cross-organisation tools, out of the customer sidebar.

Blueprint: platform_area_bp
URL prefix: /admin  (registered in app/_bootstrap/blueprints.py, like billing and team)

Routes:
  GET /admin/platform — one page listing every platform administration tool
                        (organisations, AI prompts, errors, feature flags, platform
                        health, agent registry, framework management), read from the
                        navigation registry. Only the platform_admin flag opens it; the
                        user menu shows its one "Platform admin" entry to nobody else.
"""

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.middleware.tenant_decorators import platform_admin_required

platform_area_bp = Blueprint("platform_area", __name__)

# What each tool is for, in one line, keyed by its registry key.
PURPOSES = {
    "organisations": "Every organisation on the platform, its plan and its people.",
    "ai_prompts": "The prompts the AI uses for every organisation.",
    "errors": "Server errors across the platform, newest first, and which are resolved.",
    "signups": "Who created an account themselves, where each came from, and how far each got.",
    "feature_flags": "Turn features on or off for the platform or one organisation.",
    "platform_health": "Whether each platform service is up, and past incidents.",
    "agent_registry": "Registered AI agents, their owners, charters and limits.",
    "framework_management": "The framework library every organisation builds on.",
    "framework_configuration": "How each framework is set up and extended.",
}


@platform_area_bp.route("/signups")
@login_required
@platform_admin_required
def signups():
    from app.services import signup_attribution

    return render_template("admin/signups.html", signups=signup_attribution.rows())


@platform_area_bp.route("/platform")
@login_required
@platform_admin_required
def index():
    from app.navigation.registry import platform_admin_tools

    return render_template(
        "admin/platform_area.html",
        tools=platform_admin_tools(current_user),
        purposes=PURPOSES,
    )
