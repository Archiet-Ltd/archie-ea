"""Trust centre routes: the finding tracker and the published closed-findings summary.

Blueprint: trust_centre_bp, url_prefix="/trust-centre".
"""

from __future__ import annotations

import logging

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.decorators import governance_gate_reader_required
from app.models.security_finding import (
    SEVERITIES,
    SEVERITY_LABELS,
    SOURCE_LABELS,
    SOURCES,
    STATUS_LABELS,
)

from . import services

logger = logging.getLogger(__name__)

trust_centre_bp = Blueprint("trust_centre", __name__, url_prefix="/trust-centre")


def _tracker_redirect():
    return redirect(url_for("trust_centre.findings"))


def _apply(change):
    """Run one register change; show its refusal or failure to the person."""
    try:
        change()
    except services.FindingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:  # noqa: BLE001
        db.session.rollback()
        logger.exception("security finding change failed")
        flash("The change could not be saved.", "error")
    return _tracker_redirect()


def _finding_or_404(finding_id):
    finding = services.get_finding(finding_id)
    if finding is None:
        abort(404)
    return finding


@trust_centre_bp.route("/security-findings/")
@login_required
@governance_gate_reader_required
def findings():
    """The security architect's tracker: every finding, its fix and its re-test."""
    return render_template(
        "trust_centre/findings.html",
        state=services.tracker_state(),
        severities=SEVERITIES,
        severity_labels=SEVERITY_LABELS,
        sources=SOURCES,
        source_labels=SOURCE_LABELS,
        status_labels=STATUS_LABELS,
    )


@trust_centre_bp.route("/security-findings/", methods=["POST"])
@login_required
@governance_gate_reader_required
def record_finding():
    def change():
        finding = services.record_finding(request.form, current_user)
        flash(f"Recorded: {finding.title}.", "success")

    return _apply(change)


@trust_centre_bp.route("/security-findings/<int:finding_id>/fix", methods=["POST"])
@login_required
@governance_gate_reader_required
def link_fix(finding_id):
    finding = _finding_or_404(finding_id)

    def change():
        services.link_fix(finding, request.form.get("fix_pr_url"), current_user)
        flash("Fix linked. It stays flagged until a re-test passes.", "success")

    return _apply(change)


@trust_centre_bp.route("/security-findings/<int:finding_id>/schedule-retest", methods=["POST"])
@login_required
@governance_gate_reader_required
def schedule_retest(finding_id):
    finding = _finding_or_404(finding_id)

    def change():
        services.schedule_retest(finding, request.form.get("retest_scheduled_on"), current_user)
        flash("Re-test scheduled.", "success")

    return _apply(change)


@trust_centre_bp.route("/security-findings/<int:finding_id>/record-retest", methods=["POST"])
@login_required
@governance_gate_reader_required
def record_retest(finding_id):
    finding = _finding_or_404(finding_id)
    passed = (request.form.get("result") or "").strip().lower() == "passed"

    def change():
        services.record_retest(finding, passed, request.form.get("retested_on"), current_user)
        flash("Re-test passed. The finding is closed." if passed else "Re-test failed. The finding is open again.",
              "success")

    return _apply(change)


@trust_centre_bp.route("/security-findings/<int:finding_id>/publish", methods=["POST"])
@login_required
@governance_gate_reader_required
def publish(finding_id):
    finding = _finding_or_404(finding_id)
    publish_it = (request.form.get("publish") or "1") != "0"

    def change():
        services.set_published(finding, publish_it, current_user)
        flash("Added to the published summary." if publish_it else "Removed from the published summary.", "success")

    return _apply(change)


@trust_centre_bp.route("/closed-findings")
@login_required
def closed_findings():
    """The published summary of findings closed after a passing re-test."""
    return render_template(
        "trust_centre/closed_findings.html",
        summary=services.published_summary(),
        source_labels=SOURCE_LABELS,
    )
