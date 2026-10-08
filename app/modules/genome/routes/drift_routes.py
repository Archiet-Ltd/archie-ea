"""Model Health / Drift page — deterministic detector + governed remediation.

Three routes, one page:

  GET  /genome/model-health            — read the stored drift report for the
                                          current tenant (or show "not yet
                                          computed"), provenance on every finding.
  POST /genome/model-health/rescan     — run the detector now, store the report,
                                          and redirect back to the page.
  POST /genome/model-health/remediate  — for ONE selected fixable finding, build a
                                          genome patch and route it through the
                                          EXISTING governed approval gate
                                          (`propose_genome_patch`): validate →
                                          ground → QUEUE for human approval. Nothing
                                          is applied here; approval + apply happen
                                          through the existing AI approval flow.

The detector is read-only and org-scoped. The remediation POST never edits the
model — it only queues a proposal, so a change to the system of record still
carries a human decision (ADR 0009: propose-and-govern, never silent auto-edit).

The page reads the stored report (DriftReport model) rather than running
the detector on every page load. A scheduled job (model_health_scan) keeps it
current; the "Re-scan" button triggers an immediate run.
"""
from __future__ import annotations

import logging

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup

from app.modules.genome.emit.drift_report import (
    DRIFT_CSRF_PLACEHOLDER,
    emit_drift_report_html,
)
from app.modules.genome.patch.schema import (
    ARCHIMATE_TYPE_LAYER,
    ARCHIMATE_TYPES,
    GENOME_DOMAINS,
)
from app.modules.genome.services.drift_detector import (
    FINDING_DECOMM_MAPPED,
    FINDING_ORPHANED,
    detect_model_drift,
)

logger = logging.getLogger(__name__)

genome_drift_bp = Blueprint(
    "genome_drift",
    __name__,
    url_prefix="/genome/model-health",
    template_folder="../templates",
)

# Finding types this page can propose a single-element governed fix for. Other
# findings (coverage gaps, duplicate merges, missing realizations) need a
# multi-element decision and are surfaced read-only, not offered a one-click fix.
_FIXABLE = {FINDING_ORPHANED, FINDING_DECOMM_MAPPED}

# Layer -> genome domain (all values are in GENOME_DOMAINS). Used to target the
# remediation patch at the right genome domain.
_LAYER_DOMAIN = {
    "motivation": "motivation",
    "strategy": "business",
    "business": "business",
    "application": "application",
    "technology": "technology",
    "implementation": "implementation",
    "physical": "technology",
}


def _active_org_id():
    """The current tenant's organization_id (g first, then the user)."""
    org_id = getattr(g, "current_org_id", None)
    if org_id is None:
        org_id = getattr(current_user, "organization_id", None)
    return org_id


def _csrf_input() -> str:
    """A real CSRF hidden input, substituted into the emitter's placeholder."""
    try:
        from flask_wtf.csrf import generate_csrf

        token = generate_csrf()
    except Exception:  # pragma: no cover - CSRF disabled in some test configs
        return ""
    return f'<input type="hidden" name="csrf_token" value="{token}">'  # raw-html-ok: token is server-generated via flask_wtf's generate_csrf(), never user input


def _render_stored_report(stored) -> tuple:
    """Render the HTML and summary from a stored DriftReport row.

    Returns (report_html, summary) or (None, None) if the stored report is
    missing or unrenderable.
    """
    if stored is None or stored.report_json is None:
        return None, None
    try:
        report = stored.report_json
        summary = report.get("summary")
        html = emit_drift_report_html(report)
        html = html.replace(DRIFT_CSRF_PLACEHOLDER, _csrf_input())
        return Markup(html), summary  # nosec B704
    except Exception as exc:
        logger.warning("Failed to render stored drift report: %s", exc)
        return None, None


def _enabled_provider_ids():
    """Return the ids of every enabled provider row currently visible.

    The model-health rescan is a read-only detector plus DriftReport upsert. It
    must not leave behind enabled APISettings rows, even if a future helper on
    the detector path accidentally writes one while resolving AI configuration.
    """
    from app.models.models import APISettings

    return {
        row_id
        for (row_id,) in (
            APISettings.query.filter_by(enabled=True)
            .with_entities(APISettings.id)
            .all()
        )
    }


def _remove_enabled_provider_leaks(previous_ids):
    """Delete any enabled provider rows created during the current rescan."""
    from app.models.models import APISettings

    current_ids = _enabled_provider_ids()
    leaked_ids = current_ids - set(previous_ids)
    if not leaked_ids:
        return []

    APISettings.query.filter(APISettings.id.in_(sorted(leaked_ids))).delete(
        synchronize_session=False
    )
    return sorted(leaked_ids)


def _enqueue_model_health_scan_if_not_pending(org_id: int) -> bool:
    """Enqueue a background model-health scan for *org_id* through the existing
    job queue, but only if no PENDING or IN_PROGRESS scan already exists for
    this organisation.  Returns True when a new job was created."""
    from app.extensions import db
    from app.models.job import Job, JobStatus

    existing = (
        db.session.query(Job)
        .filter(
            Job.task == "model_health_scan",
            Job.status.in_([JobStatus.PENDING.value, JobStatus.IN_PROGRESS.value]),
        )
        .all()
    )
    for job in existing:
        payload = job.payload or {}
        if payload.get("organization_id") == org_id:
            return False

    from app.services.job_queue_service import get_job_queue_service

    service = get_job_queue_service()
    service.create_job(
        name=f"Model-health scan for org {org_id}",
        task="model_health_scan",
        payload={"organization_id": org_id},
    )
    return True


@genome_drift_bp.route("/", methods=["GET"])
@login_required
def index():
    """Read the stored drift report for the current tenant and render it."""
    org_id = _active_org_id()
    report_html = None
    error = None
    summary = None
    computed_at = None
    pending = False
    if org_id is None:
        error = "No active organization for the current user."
    else:
        try:
            from app.models.drift_report import DriftReport

            stored = DriftReport.for_org(org_id)
            if stored is not None:
                report_html, summary = _render_stored_report(stored)
                if report_html is None:
                    error = (
                        "Stored model-health report could not be rendered. "
                        "Re-scan for drift to rebuild it."
                    )
                else:
                    computed_at = stored.computed_at
            else:
                # No stored report — show a pending state and enqueue a
                # background scan through the existing job queue so the
                # page load stays sub-quadratic.  Enqueue at most once
                # per organisation while a scan is already pending.
                pending = True
                _enqueue_model_health_scan_if_not_pending(org_id)
        except Exception as exc:
            logger.warning("Drift report read failed for org %s: %s", org_id, exc)
            error = f"Model-health report could not be read: {exc}"
    return render_template(
        "genome/model_health.html",
        report_html=report_html,
        summary=summary,
        error=error,
        org_id=org_id,
        computed_at=computed_at,
        pending=pending,
    )


@genome_drift_bp.route("/rescan", methods=["POST"])
@login_required
def rescan():
    """Run the detector now, store the report, and redirect back to the page."""
    org_id = _active_org_id()
    if org_id is None:
        flash("No active organization for the current user.", "error")
        return redirect(url_for("genome_drift.index"))
    try:
        from app.extensions import db
        from app.models.drift_report import DriftReport

        enabled_provider_ids_before = _enabled_provider_ids()
        report = detect_model_drift(org_id)
        DriftReport.upsert(org_id, report)
        leaked_provider_ids = _remove_enabled_provider_leaks(enabled_provider_ids_before)
        db.session.commit()
        if leaked_provider_ids:
            logger.warning(
                "Model-health rescan removed unexpected enabled provider rows: %s",
                leaked_provider_ids,
            )
        flash("Model-health scan completed.", "success")
    except Exception as exc:
        logger.warning("Drift rescan failed for org %s: %s", org_id, exc)
        flash(f"Model-health scan failed: {exc}", "error")
    return redirect(url_for("genome_drift.index"))


@genome_drift_bp.route("/remediate", methods=["POST"])
@login_required
def remediate():
    """Queue a GOVERNED remediation for one selected finding. Applies nothing."""
    org_id = _active_org_id()
    finding_type = (request.form.get("finding_type") or "").strip()
    element_id_raw = (request.form.get("element_id") or "").strip()

    if org_id is None:
        flash("No active organization for the current user.", "error")
        return redirect(url_for("genome_drift.index"))

    if finding_type not in _FIXABLE:
        flash(
            "That finding needs a multi-element decision and cannot be fixed with "
            "a single governed patch.",
            "error",
        )
        return redirect(url_for("genome_drift.index"))

    try:
        element_id = int(element_id_raw)
    except (TypeError, ValueError):
        flash("Invalid element reference for remediation.", "error")
        return redirect(url_for("genome_drift.index"))

    patch = _build_remediation_patch(org_id, finding_type, element_id)
    if patch is None:
        flash(
            "The selected element cannot be expressed as a governed patch "
            "(unknown ArchiMate type); no proposal was queued.",
            "error",
        )
        return redirect(url_for("genome_drift.index"))

    # Route through the EXISTING governed gate: validate -> ground -> QUEUE.
    from app.modules.genome.patch.proposer import propose_genome_patch

    result = propose_genome_patch(
        request_text=f"Remediate drift finding '{finding_type}' on element #{element_id}",
        user_id=getattr(current_user, "id", None),
        patch_source=lambda _text, _ctx: patch,
        context={"organization_id": org_id},
    )

    if result.get("success"):
        flash(
            f"Governed remediation queued for approval (approval #{result.get('approval_id')}). "
            f"Nothing has been applied — a human must approve it.",
            "success",
        )
    elif result.get("status") == "rejected":
        flash(
            "The proposed fix did not validate/ground and was not queued: "
            + "; ".join(result.get("errors", []) or ["unknown reason"]),
            "error",
        )
    else:
        flash(
            "Could not queue the remediation: " + str(result.get("error", "unknown error")),
            "error",
        )
    return redirect(url_for("genome_drift.index"))


def _build_remediation_patch(org_id: int, finding_type: str, element_id: int):
    """Build a schema-valid `modify` genome patch for a fixable finding.

    Returns the patch dict, or None if the element's ArchiMate type is not one the
    genome-patch schema knows (in which case no fabricated patch is produced —
    CLAUDE.md: never invent data). The patch is NOT applied here; it is handed to
    `propose_genome_patch`, which validates, grounds and queues it for approval.
    """
    from app.extensions import db
    from app.models.archimate_core import ArchiMateElement

    element = (
        db.session.query(ArchiMateElement)
        .filter(ArchiMateElement.id == element_id)
        .filter(ArchiMateElement.organization_id == org_id)
        .first()
    )
    if element is None:
        return None

    a_type = element.type
    if a_type not in ARCHIMATE_TYPES:
        return None  # cannot express as a governed patch without inventing a type

    layer = ARCHIMATE_TYPE_LAYER.get(a_type)
    if layer is None:
        return None
    domain = _LAYER_DOMAIN.get(layer, "business")
    if domain not in GENOME_DOMAINS:
        domain = "business"

    if finding_type == FINDING_ORPHANED:
        rationale = (
            f"Drift detector: element #{element_id} ('{element.name}') is orphaned — "
            f"wired to no relationship and linked from no capability/application. "
            f"Proposing it be flagged for architecture review/retirement."
        )
        fields = {"status": "Flagged-Drift", "drift_signal": FINDING_ORPHANED}
    else:  # FINDING_DECOMM_MAPPED
        rationale = (
            f"Drift detector: application element #{element_id} ('{element.name}') is "
            f"in a retiring lifecycle yet still mapped as active capability support. "
            f"Proposing its retiring lifecycle be recorded on the genome element so "
            f"the model matches reality."
        )
        fields = {
            "status": "Flagged-Drift",
            "drift_signal": FINDING_DECOMM_MAPPED,
            "lifecycle_status": (element.status or "retiring"),
        }

    return {
        "target": {"organization_id": org_id, "domain": domain},
        "operation": "modify",
        "element": {
            "archimate_type": a_type,
            "layer": layer,
            "name": element.name,
            "element_id": element_id,
            "fields": fields,
        },
        "provenance": {
            "proposed_by": f"drift_detector:user_{getattr(current_user, 'id', 'unknown')}",
            "rationale": rationale,
            "archimate_anchor": a_type,  # a known ArchiMate type — resolves in grounding
            "source": "genome_drift_detector",
        },
    }


def register(app):
    """Register the Model Health / Drift blueprint on the app (non-fatal caller)."""
    app.register_blueprint(genome_drift_bp)
    app.logger.info(
        "[BLUEPRINT] Genome model-health / drift registered at /genome/model-health"
    )


__all__ = ["genome_drift_bp", "register", "_build_remediation_patch", "rescan"]
