"""Rate limits an administrator can see and set.

GET  /admin/rate-limits                    the allow and deny lists (platform administrator) and the
                                           violation log (platform administrator: every refusal; an
                                           organisation's administrator: its own people's only)
POST /admin/rate-limits/rules              add an entry
POST /admin/rate-limits/rules/<id>/delete  remove an entry

The limiter, the lists and the log are in app.services.rate_limiter; this module only reads and
writes them for a person.
"""

from datetime import datetime, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

from app.middleware.tenant_decorators import (
    is_platform_admin,
    org_admin_required,
    platform_admin_required,
)
from app.services import rate_limiter

rate_limits_bp = Blueprint("rate_limits", __name__)

PER_PAGE = 25


def _day(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None


@rate_limits_bp.route("/rate-limits", methods=["GET"])
@org_admin_required
def index():
    platform = is_platform_admin(current_user)
    filters = {
        "address": (request.args.get("address") or "").strip(),
        "user": (request.args.get("user") or "").strip(),
        "since": (request.args.get("since") or "").strip(),
        "until": (request.args.get("until") or "").strip(),
    }
    until = _day(filters["until"])
    violations = rate_limiter.violation_page(
        organization_id=None if platform else g.current_org_id,
        address=filters["address"] or None,
        user=filters["user"] or None,
        since=_day(filters["since"]),
        until=(until + timedelta(days=1)) if until else None,
        page=request.args.get("page", 1, type=int),
        per_page=PER_PAGE,
    )
    return render_template(
        "admin/rate_limits.html",
        platform=platform,
        rules=rate_limiter.list_rules() if platform else [],
        violations=violations,
        filters=filters,
        filtered=any(filters.values()),
        your_address=request.remote_addr,
    )


@rate_limits_bp.route("/rate-limits/rules", methods=["POST"])
@platform_admin_required
def add_rule():
    try:
        rate_limiter.add_rule(
            request.form.get("action", ""),
            request.form.get("kind", "address"),
            request.form.get("value", ""),
            note=request.form.get("note"),
            created_by_id=current_user.id,
            own_address=request.remote_addr,
        )
        flash("Entry added. It applies to the next request on every worker.", "success")
    except rate_limiter.RuleError as exc:
        flash(str(exc), "error")
    return redirect(url_for("rate_limits.index"))


@rate_limits_bp.route("/rate-limits/rules/<int:rule_id>/delete", methods=["POST"])
@platform_admin_required
def delete_rule(rule_id):
    if rate_limiter.remove_rule(rule_id):
        flash("Entry removed. It no longer applies.", "success")
    else:
        flash("That entry was already removed.", "error")
    return redirect(url_for("rate_limits.index"))
