"""The five-screen onboarding flow.

Screen 1 Welcome -> Screen 2 Bring your company (P0, the only mandatory step)
-> Screen 3 First question -> Screen 4 Fill the gaps -> Screen 5 Your twin.

Replaces the old PLT-040 first-login modal (app/templates/layouts/admin_base.html)
as the onboarding UX, and calls the same completion endpoint
(dashboard.api_onboarding_complete) it did, so onboarding_completed_at stays the
one place "has this user finished onboarding" is recorded.

The website field on screen 2 is a real, honest stub: it saves the address and
answers, and says reading is not available yet -- the company-context reading
engine (the fetcher, the LLM clients, the run record) is a separate, not-yet-
landed piece of work. Nothing here fabricates a reading result.
"""
from __future__ import annotations

import datetime

from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models.organization import Organization
from app.utils.api_response import success_response

from .services import profile, stage_gaps, tell_us_more

onboarding_bp = Blueprint("onboarding", __name__, template_folder="templates")

_STAGES = ("pre_revenue", "early_revenue", "growing", "established")
_STAGE_LABELS = {
    "pre_revenue": "Pre-revenue",
    "early_revenue": "Early revenue",
    "growing": "Growing",
    "established": "Established",
}


def _current_org() -> Organization:
    org_id = getattr(g, "current_org_id", None) or getattr(current_user, "organization_id", None)
    return db.session.get(Organization, int(org_id))


@onboarding_bp.route("/")
@login_required
def index():
    """Entry point: route each onboarding-time persona to the right screen.

    Someone who has already finished only lands here by choice (the All modules
    directory lists "Getting started"), so show their saved company answers,
    editable, instead of bouncing them to the dashboard."""
    if current_user.onboarding_completed_at:
        return company()

    org = _current_org()
    org_profile = profile.read(org)
    if org_profile.get("stage"):
        # The org's own P0 already exists (someone else completed it) -- an
        # invited team member enters at Screen 3, per onboarding-prd-v1 S3.
        return redirect(url_for("onboarding.first_question"))
    return redirect(url_for("onboarding.welcome"))


@onboarding_bp.route("/welcome")
@login_required
def welcome():
    if current_user.onboarding_completed_at:
        return redirect(url_for("dashboard.overview"))
    return render_template("onboarding/screen1_welcome.html")


@onboarding_bp.route("/company", methods=["GET", "POST"])
@login_required
def company():
    org = _current_org()
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        stage = data.get("stage")
        if stage not in _STAGES:
            return jsonify({"success": False, "error": "invalid_stage"}), 400
        profile.write(
            org,
            stage=stage,
            company_size=(data.get("company_size") or "").strip()[:100] or None,
            industry=(data.get("industry") or "").strip()[:200] or None,
            source_url=(data.get("source_url") or "").strip()[:500] or None,
            region_europe_or_eu_customers=bool(data.get("region_europe_or_eu_customers")),
            handles_card_data_directly=bool(data.get("handles_card_data_directly")),
        )
        if request.is_json:
            return success_response({"next": url_for("onboarding.first_question")})
        return redirect(url_for("onboarding.first_question"))

    return render_template(
        "onboarding/screen2_company.html",
        stages=[{"key": k, "label": v} for k, v in _STAGE_LABELS.items()],
        current=profile.read(org),
    )


@onboarding_bp.route("/api/website", methods=["POST"])
@login_required
def api_website_read():
    """Screen 2's website field. Saves the address; the reading itself is a
    separate, not-yet-available capability -- this is an honest stub, not a
    fabricated result."""
    org = _current_org()
    data = request.get_json(silent=True) or {}
    source_url = (data.get("source_url") or "").strip()[:500]
    if not source_url:
        return jsonify({"success": False, "error": "no_address"}), 400
    profile.write(org, source_url=source_url)
    return success_response({
        "status": "not_available_yet",
        "message": "Reading your site isn't available yet -- we've saved the address "
                    "and will use it as soon as this is ready.",
    })


@onboarding_bp.route("/first-question", methods=["GET", "POST"])
@login_required
def first_question():
    org = _current_org()
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        answer = (data.get("answer") or "").strip()[:1000]
        profile.write(org, first_question_answer=answer or None)
        if request.is_json:
            return success_response({"next": url_for("onboarding.gaps")})
        return redirect(url_for("onboarding.gaps"))
    return render_template("onboarding/screen3_first_question.html", current=profile.read(org))


def _recorded_for_org(org: Organization) -> dict:
    """What the organisation actually has recorded, by category. Only an
    *assigned* gap counts as recorded (someone or "founder does this" now
    owns it) -- an *accepted* gap is deliberately deferred and must stay
    visible as a gap, per onboarding-redesign-v3 §4/§5: accepted items are
    "never counted as filled". Only the onboarding-answered facts exist yet
    (the derivation engine that would read real roles/functions/capabilities/
    systems off the estate is a separate, not-yet-landed piece of work) --
    so today this is deliberately small, and every remaining gap reads
    "expected at your stage", never a fabricated fact."""
    org_profile = profile.read(org)
    recorded = {"roles": [], "functions": [], "capabilities": [], "systems": [], "controls": []}
    assigned = org_profile.get("assigned_gaps", {})
    # assigned gap keys are stored as "<category>:<key>"
    for gap_id in assigned:
        if ":" in gap_id:
            cat, key = gap_id.split(":", 1)
            if cat in recorded:
                recorded[cat].append(key)
    return recorded


@onboarding_bp.route("/gaps", methods=["GET"])
@login_required
def gaps():
    org = _current_org()
    org_profile = profile.read(org)
    stage = org_profile.get("stage") or "pre_revenue"
    recorded = _recorded_for_org(org)
    all_gaps = stage_gaps.compute_gaps(
        stage,
        recorded,
        region_europe_or_eu_customers=bool(org_profile.get("region_europe_or_eu_customers")),
        handles_card_data_directly=bool(org_profile.get("handles_card_data_directly")),
    )
    accepted = org_profile.get("accepted_gaps", {})
    assigned = org_profile.get("assigned_gaps", {})
    for gap in all_gaps:
        gap_id = f"{gap['category']}:{gap['key']}"
        gap["gap_id"] = gap_id
        gap["accepted_reason"] = accepted.get(gap_id)
        gap["assigned_to"] = assigned.get(gap_id)
    statuses = tell_us_more.section_statuses(org)
    tell_us_more_done = sum(1 for v in statuses.values() if v == "saved")
    return render_template(
        "onboarding/screen4_gaps.html",
        stage_label=_STAGE_LABELS.get(stage, stage),
        gaps=all_gaps,
        tell_us_more_done=tell_us_more_done,
        tell_us_more_total=len(statuses),
    )


@onboarding_bp.route("/gaps/<gap_id>/action", methods=["POST"])
@login_required
def gap_action(gap_id: str):
    """Accept-for-now or assign a stage gap. Accepted gaps stay visible under
    Coverage as accepted, with the reason and date -- they are never counted
    as filled, per onboarding-redesign-v3 §4."""
    org = _current_org()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    org_profile = profile.read(org)
    if action == "accept":
        reason = (data.get("reason") or "").strip()[:300]
        accepted = dict(org_profile.get("accepted_gaps", {}))
        accepted[gap_id] = {
            "reason": reason or "Not yet",
            "at": datetime.datetime.utcnow().isoformat(),
            "by_user_id": current_user.id,
        }
        profile.write(org, accepted_gaps=accepted)
    elif action == "assign":
        assignee = (data.get("assignee") or "").strip()[:200]
        if not assignee:
            return jsonify({"success": False, "error": "no_assignee"}), 400
        assigned = dict(org_profile.get("assigned_gaps", {}))
        assigned[gap_id] = {
            "assignee": assignee,
            "at": datetime.datetime.utcnow().isoformat(),
            "by_user_id": current_user.id,
        }
        profile.write(org, assigned_gaps=assigned)
    else:
        return jsonify({"success": False, "error": "invalid_action"}), 400
    return success_response({"gap_id": gap_id, "action": action})


@onboarding_bp.route("/twin")
@login_required
def twin():
    return render_template("onboarding/screen5_twin.html")


@onboarding_bp.route("/tell-us-more")
@login_required
def tell_us_more_hub():
    """The five short, optional sections (onboarding-redesign-v3 §3), reached
    from Screen 4's gaps, Screen 5 ("can't answer yet"), and the dashboard's
    module directory. Progress persists per organisation and can be resumed
    from here at any time."""
    org = _current_org()
    statuses = tell_us_more.section_statuses(org)
    sections = [
        {
            "key": s["key"],
            "title": s["title"],
            "unlock_line": s["unlock_line"],
            "status": statuses[s["key"]],
        }
        for s in tell_us_more.SECTIONS
    ]
    return render_template("onboarding/tell_us_more_hub.html", sections=sections)


@onboarding_bp.route("/tell-us-more/<section_key>", methods=["GET", "POST"])
@login_required
def tell_us_more_section(section_key: str):
    org = _current_org()
    sec = tell_us_more.section(section_key)
    if sec is None:
        return jsonify({"success": False, "error": "unknown_section"}), 404

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        action = data.get("action")
        if action == "save":
            tell_us_more.save_section(org, section_key, data.get("answers") or {})
        elif action == "skip":
            tell_us_more.skip_section(org, section_key)
        else:
            return jsonify({"success": False, "error": "invalid_action"}), 400
        return success_response({
            "next": url_for("onboarding.tell_us_more_hub"),
            "section": section_key,
            "action": action,
        })

    statuses = tell_us_more.section_statuses(org)
    return render_template(
        "onboarding/tell_us_more_section.html",
        section=sec,
        answers=tell_us_more.answers_for(org, section_key),
        status=statuses[section_key],
        section_index=[s["key"] for s in tell_us_more.SECTIONS].index(section_key) + 1,
        section_count=len(tell_us_more.SECTIONS),
    )


@onboarding_bp.route("/finish", methods=["POST"])
@login_required
def finish():
    """Record completion. Writes the same User.onboarding_completed_at (and
    optional enterprise_role) as dashboard.api_onboarding_complete -- one
    column, so no second "onboarding done" flag exists, but this route holds
    its own copy of that small write rather than calling the other endpoint."""
    data = request.get_json(silent=True) or {}
    new_role = data.get("enterprise_role")
    valid_roles = {
        "solution_architect", "enterprise_architect", "business_architect",
        "arb_member", "portfolio_manager", "platform_admin",
        "cto", "application_manager", "procurement",
    }
    if new_role and new_role in valid_roles:
        current_user.enterprise_role = new_role
    current_user.onboarding_completed_at = datetime.datetime.utcnow()
    db.session.commit()
    return success_response({"next": url_for("dashboard.overview")})
