"""Routes for the Tech Radar module (ARCH-124).

Blueprint: tech_radar_bp, url_prefix="/technology/radar".
"""

from __future__ import annotations

import logging
from datetime import date

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.decorators import require_roles
from app.models.tech_radar import RADAR_RING_LABELS, RADAR_RINGS

from . import service

logger = logging.getLogger(__name__)

tech_radar_bp = Blueprint("tech_radar", __name__, url_prefix="/technology/radar")


@tech_radar_bp.route("/")
@login_required
def index():
    """The radar: real Technology-layer ArchiMateElements, grouped by their
    architect-set adopt/trial/assess/hold ring. Empty state when the tenant
    has no technology-layer elements at all."""
    state = service.radar_state()
    return render_template(
        "tech_radar/index.html",
        state=state,
        rings=RADAR_RINGS,
        ring_labels=RADAR_RING_LABELS,
        sunsets=service.sunset_entries(),
        today=date.today(),
    )


@tech_radar_bp.route("/api")
@login_required
def api_state():
    state = service.radar_state()
    return jsonify({
        "success": True,
        "total_candidates": state["total_candidates"],
        "classified_count": state["classified_count"],
        "unclassified_count": len(state["unclassified"]),
        "rings": {
            ring: [
                {**row["entry"].to_dict()}
                for row in rows
            ]
            for ring, rows in state["rings"].items()
        },
        "sunsets": [
            {
                **row["entry"].to_dict(),
                "applications": row["affected"]["applications"],
                "applications_reason": row["affected"]["reason"],
            }
            for row in service.sunset_entries()
        ],
    })


def _wants_json() -> bool:
    """True when the caller is the fetch/XHR API rather than the radar's own form.

    The radar page submits a plain HTML <form> to this endpoint. It used to
    reply with jsonify() unconditionally, so a CTO who classified a technology
    in the UI was navigated to /technology/radar/classify and left staring at
    `{"success": true, "entry": {...}}` with no way back but the Back button --
    the browser walkthrough found this; no server-side test could, because a
    test client asserts on the JSON and never has to look at it.
    """
    if request.is_json:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.accept_mimetypes
    return accept["application/json"] > accept["text/html"]

@tech_radar_bp.route("/classify", methods=["POST"])
@login_required
@require_roles("admin", "administrator", "architect", "enterprise_architect", "cto", "platform_admin")
def classify():
    """Set/update the ring for one Technology-layer element. Rejects
    anything that is not a real, already-modelled technology element —
    a radar entry can never be created out of thin air."""
    element_id = request.form.get("archimate_element_id", type=int)
    ring = (request.form.get("ring") or "").strip().lower()
    # Absent fields are left as recorded (the "Move" control sends only the
    # ring); a field sent empty clears what was recorded.
    rationale = request.form.get("rationale")

    def _fail(message, status):
        if _wants_json():
            return jsonify({"success": False, "error": message}), status
        flash(message, "error")
        return redirect(url_for("tech_radar.index")), status

    if not element_id or ring not in RADAR_RINGS:
        return _fail("archimate_element_id and a valid ring are required", 400)

    details = {}
    if "review_date" in request.form:
        raw = (request.form.get("review_date") or "").strip()
        try:
            details["review_date"] = date.fromisoformat(raw) if raw else None
        except ValueError:
            return _fail("review_date must be a date (YYYY-MM-DD)", 400)
    if "requesting_initiative_id" in request.form:
        raw = (request.form.get("requesting_initiative_id") or "").strip()
        if raw and not raw.isdigit():
            return _fail("requesting_initiative_id must be an initiative id", 400)
        details["requesting_initiative_id"] = int(raw) if raw else None

    try:
        entry = service.classify(element_id, ring, rationale, current_user.id, **details)
    except ValueError as exc:
        return _fail(str(exc), 400)
    except Exception:  # noqa: BLE001
        logger.exception("tech radar classify failed for element %s", element_id)
        return _fail("Could not save classification", 500)

    if _wants_json():
        return jsonify({"success": True, "entry": entry.to_dict()})

    flash(
        "%s moved to %s." % (entry.element.name if entry.element else "Technology",
                             RADAR_RING_LABELS.get(ring, ring)),
        "success",
    )
    return redirect(url_for("tech_radar.index"))


@tech_radar_bp.route("/sunset", methods=["POST"])
@login_required
@require_roles("admin", "administrator", "architect", "enterprise_architect", "cto", "platform_admin")
def sunset():
    """Retire a technology standard: move it to the hold ring with a sunset
    date and (optionally) the standard that replaces it, then notify the owners
    of every application running it. The affected applications and owners are
    listed on the radar page after the redirect."""
    element_id = request.form.get("archimate_element_id", type=int)
    replacement_id = request.form.get("replacement_element_id", type=int)
    rationale = request.form.get("rationale") or ""
    raw_date = (request.form.get("sunset_date") or "").strip()

    def _fail(message, status):
        if _wants_json():
            return jsonify({"success": False, "error": message}), status
        flash(message, "error")
        return redirect(url_for("tech_radar.index")), status

    try:
        sunset_date = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        sunset_date = None
    if not element_id or sunset_date is None:
        return _fail("Choose a technology and a sunset date.", 400)

    try:
        result = service.sunset(
            element_id,
            sunset_date,
            replacement_id,
            rationale,
            current_user.id,
            notify_url=url_for("tech_radar.index"),
        )
    except ValueError as exc:
        return _fail(str(exc), 400)
    except Exception:  # noqa: BLE001
        from app import db

        db.session.rollback()
        logger.exception("tech radar sunset failed for element %s", element_id)
        return _fail("Could not save the sunset", 500)

    entry = result["entry"]
    applications = result["affected"]["applications"]
    notified = result["notified"].get("notified_users", 0)
    if _wants_json():
        return jsonify({
            "success": True,
            "entry": entry.to_dict(),
            "applications": applications,
            "applications_reason": result["affected"]["reason"],
            "notified_users": notified,
        })

    name = entry.element.name if entry.element else "Technology"
    if applications:
        flash(
            "%s moved to Hold with a sunset date. %d %s running it; %d %s notified."
            % (
                name,
                len(applications),
                "application is" if len(applications) == 1 else "applications are",
                notified,
                "owner" if notified == 1 else "owners",
            ),
            "success",
        )
    else:
        flash("%s moved to Hold with a sunset date. %s" % (name, service.NO_APPLICATIONS_REASON), "success")
    return redirect(url_for("tech_radar.index"))
