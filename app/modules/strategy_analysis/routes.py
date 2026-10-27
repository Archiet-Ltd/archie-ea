"""Routes for the Strategy analyses.

Blueprint: strategy_analysis_bp, url_prefix="/strategy".

    GET  /strategy/competitors     the competitor comparison matrix (design ruling R38)
    GET  /strategy/drafts          the SWOT and PESTLE drafts waiting to be accepted
    POST /strategy/drafts/run      queue the drafting job (never run inside the request)
    POST /strategy/drafts/accept   record the drafts as cited model records

The organisation is the signed-in person's active organisation, never a request value.
"""
from __future__ import annotations

import logging

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models.organization import Organization
from app.models.user import Permission
from app.services import strategy_draft_service as engine

from . import views

logger = logging.getLogger(__name__)

strategy_analysis_bp = Blueprint("strategy_analysis", __name__, url_prefix="/strategy")

_BACK = {"competitors": "strategy_analysis.competitors", "drafts": "strategy_analysis.drafts"}


def _org() -> Organization:
    org = db.session.get(Organization, g.current_org_id)
    if org is None:
        abort(404)
    return org


def _may_write() -> bool:
    return bool(current_user.can(Permission.GENERAL))


def _back():
    return redirect(url_for(_BACK.get(request.form.get("next"), "strategy_analysis.competitors")))


@strategy_analysis_bp.route("/competitors")
@login_required
def competitors():
    org = _org()
    return render_template("strategy/competitors.html", matrix=views.competitor_matrix(org), can_write=_may_write())


@strategy_analysis_bp.route("/drafts")
@login_required
def drafts():
    org = _org()
    return render_template("strategy/drafts.html", drafts=views.framework_drafts(org), can_write=_may_write())


@strategy_analysis_bp.route("/drafts/run", methods=["POST"])
@login_required
def run():
    if not _may_write():
        abort(403)
    org = _org()
    try:
        engine.queue_drafts(org.id, current_user.id)
    except engine.DraftsRefused as refused:
        flash(str(refused), "error")
    else:
        flash("Drafting the competitor analysis, SWOT and PESTLE from your profile and public sources.", "info")
    return _back()


@strategy_analysis_bp.route("/drafts/accept", methods=["POST"])
@login_required
def accept():
    if not _may_write():
        abort(403)
    org = _org()
    done = engine.accept_drafts(org, current_user)
    if done["kept"]:
        flash("Recorded %d %s in your model, each with its sources." % (done["kept"], "statement" if done["kept"] == 1 else "statements"), "success")
    else:
        flash("There were no drafts to accept.", "info")
    return _back()
