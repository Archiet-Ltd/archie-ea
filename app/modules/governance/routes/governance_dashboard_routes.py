"""
Governance Dashboard Routes
Provides central governance oversight, ARB reviews, ADRs, risk register, and enterprise roadmap.
"""
import logging
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, redirect, render_template, url_for
from flask_login import login_required

from app import db
from app.decorators import require_roles

logger = logging.getLogger(__name__)

governance_bp = Blueprint("governance", __name__, url_prefix="/governance")

# ArchiMate Principle carries an RFC-2119 enforcement level; the dashboard badges
# a "priority". Map the two rather than inventing a priority the model never held.
_ENFORCEMENT_TO_PRIORITY = {
    "MUST": "Critical",
    "SHOULD": "High",
    "MAY": "Medium",
}


@governance_bp.route("/dashboard")
@login_required
def dashboard():
    """Main governance dashboard."""
    return render_template("governance/dashboard.html")


@governance_bp.route("/api/metrics")
@login_required
def api_metrics():
    """API endpoint to get governance metrics."""
    try:
        from app.models.solution_governance import SolutionARBReview as SolutionGovernance
        
        # Count pending ARB reviews
        pending_reviews = db.session.query(SolutionGovernance).filter(
            SolutionGovernance.arb_decision.in_(['pending', 'in_review', 'arb_review'])
        ).count()
        
        # Count active risks (if risk model exists)
        active_risks = 0
        try:
            from app.models.risk import Risk, RiskStatus
            # 'severity' is not a column; risk level derives from likelihood*impact
            # (>=9 == high/critical). 'status' is a RiskStatus enum (OPEN == active),
            # not the string 'active'. The old filters raised AttributeError (swallowed
            # below), so this metric was always 0.
            active_risks = db.session.query(Risk).filter(
                Risk.status == RiskStatus.OPEN,
                (Risk.likelihood * Risk.impact) >= 9,
            ).count()
        except Exception:
            pass
        
        # Count recent ADRs (last 90 days)
        recent_adrs = 0
        try:
            from app.models.architecture_decision import ArchitectureDecision
            ninety_days_ago = datetime.utcnow() - timedelta(days=90)
            recent_adrs = db.session.query(ArchitectureDecision).filter(
                ArchitectureDecision.created_at >= ninety_days_ago
            ).count()
        except Exception:
            pass
        
        # Calculate compliance rate
        total_solutions = db.session.query(SolutionGovernance).count()
        approved_solutions = db.session.query(SolutionGovernance).filter(
            SolutionGovernance.arb_decision == 'approved'
        ).count()
        compliance_rate = round((approved_solutions / total_solutions * 100) if total_solutions > 0 else 0, 1)
        
        return jsonify({
            'pending_reviews': pending_reviews,
            'active_risks': active_risks,
            'recent_adrs': recent_adrs,
            'compliance_rate': compliance_rate
        })
    except Exception as e:
        # Previously returned all-zero metrics with HTTP 200, which the dashboard
        # rendered as fact — "Compliance Rate 0%" for a query that never ran.
        # Fail loudly so the client shows its "could not be loaded" state instead.
        logger.error(f"Error getting governance metrics: {e}", exc_info=True)
        return jsonify({'error': 'Failed to load governance metrics'}), 500


@governance_bp.route("/api/principles")
@login_required
def api_principles():
    """Architecture principles for the governance dashboard.

    Reads the ArchiMate Principle element (app/models/models.py) — the backbone
    model per DESIGN.md — rather than a parallel governance-only table. This
    previously imported a non-existent `app.models.architecture_principle`, so
    the ImportError branch silently returned [] and the dashboard showed nothing.

    Tenant scoping is implicit: Principle carries TenantMixin, so do_orm_execute
    injects `WHERE organization_id = g.current_org_id`. Rows predating that change
    have a NULL organization_id and are excluded until
    `flask --app manage backfill-principle-org` has been run.
    """
    try:
        from app.models.models import Principle

        # Deprecated/superseded principles are not current governance.
        principles = (
            db.session.query(Principle)
            .filter(Principle.status.notin_(["deprecated", "superseded"]))
            .order_by(Principle.name)
            .all()
        )

        return jsonify([{
            'id': p.id,
            'name': p.name,
            'statement': p.statement,
            # The dashboard renders `priority` as a badge (Critical -> destructive).
            # RFC-2119 enforcement level is the closest real signal we hold.
            'priority': _ENFORCEMENT_TO_PRIORITY.get(
                (p.enforcement_level or "").upper(), p.enforcement_level or "—"
            ),
            'domain': p.category or "—",
        } for p in principles])
    except Exception as e:
        logger.error(f"Error getting principles: {e}", exc_info=True)
        return jsonify({'error': 'Failed to load architecture principles'}), 500


@governance_bp.route("/api/standards")
@login_required
def api_standards():
    """Approved-technology register for the governance dashboard.

    The TechnologyStandard model did not exist when this route was written, so the
    ImportError branch silently returned [] — which is what drove the template's
    fabricated "Python 3.11+ / Approved" fallback. Tenant-scoped via TenantMixin.
    """
    try:
        from app.models.technology_standard import TechnologyStandard

        standards = (
            db.session.query(TechnologyStandard)
            .filter(TechnologyStandard.is_active.is_(True))
            .order_by(TechnologyStandard.category, TechnologyStandard.technology_name)
            .all()
        )
        return jsonify([s.to_dict() for s in standards])
    except Exception as e:
        logger.error(f"Error getting standards: {e}", exc_info=True)
        return jsonify({'error': 'Failed to load technology standards'}), 500


@governance_bp.route("/api/reviews/recent")
@login_required
def api_recent_reviews():
    """API endpoint to get recent ARB reviews."""
    try:
        from app.models.solution_governance import SolutionARBReview as SolutionGovernance
        from app.models.solution_models import Solution
        
        reviews = db.session.query(
            SolutionGovernance, Solution
        ).join(
            Solution, SolutionGovernance.solution_id == Solution.id
        ).order_by(
            SolutionGovernance.submitted_at.desc()
        ).limit(10).all()
        
        return jsonify([{
            'id': gov.id,
            'solution_name': sol.name,
            'solution_url': url_for('solution_design.view_solution', solution_id=sol.id),
            'review_date': gov.submitted_at.strftime('%Y-%m-%d') if gov.submitted_at else 'N/A',
            'status': gov.arb_decision,
            'reviewer': gov.reviewer_name if hasattr(gov, 'reviewer_name') else 'ARB'
        } for gov, sol in reviews])
    except Exception as e:
        # Was: return [] with HTTP 200 — indistinguishable from "no reviews exist".
        logger.error(f"Error getting recent reviews: {e}", exc_info=True)
        return jsonify({'error': 'Failed to load recent ARB reviews'}), 500


@governance_bp.route("/arb-reviews")
@login_required
def arb_reviews():
    """Redirect the legacy dashboard shortcut to the ARB review workflow."""
    return redirect(url_for("arb.reviews"))


@governance_bp.route("/adr-list")
@login_required
def adr_list():
    """Redirect the legacy dashboard shortcut to the decision register."""
    return redirect(url_for("arch_decisions.list_decisions"))


@governance_bp.route("/risk-register")
@login_required
def risk_register():
    """Redirect the legacy dashboard shortcut to the risk register."""
    return redirect(url_for("risk.risk_register"))


@governance_bp.route("/principles")
@login_required
@require_roles("admin", "architect")
def principles():
    """Architecture Principles management page."""
    return render_template("governance/principles.html")


TECHNOLOGY_STANDARD_STATUSES = (
    "approved", "preferred", "acceptable", "under_review", "deprecated", "prohibited",
)


def _standards_by_domain():
    """Active standards grouped by category (the domain), each with the radar
    ring its linked technology element carries — read from the radar, never
    stored twice."""
    from app.models.technology_standard import TechnologyStandard
    from app.modules.tech_radar import service as radar

    rows = (
        db.session.query(TechnologyStandard)
        .filter(TechnologyStandard.is_active.is_(True))
        .order_by(TechnologyStandard.category, TechnologyStandard.technology_name)
        .all()
    )
    rings = radar.rings_for_elements([r.archimate_element_id for r in rows])
    grouped = {}
    for row in rows:
        grouped.setdefault(row.category or "", []).append(
            {"standard": row, "ring": rings.get(row.archimate_element_id)}
        )
    return grouped


def _create_standard(form, user_id):
    """Record one technology standard from the page's form, and when a ring is
    chosen for its technology element, place that element on the radar in the
    same transaction. Returns (standard, None) or (None, error message)."""
    from datetime import date

    from app.models.archimate_core import ArchiMateElement
    from app.models.tech_radar import RADAR_RINGS
    from app.models.technology_standard import TechnologyStandard
    from app.modules.tech_radar import service as radar

    name = (form.get("technology_name") or "").strip()
    category = (form.get("category") or "").strip()
    status = (form.get("status") or "").strip().lower()
    ring = (form.get("ring") or "").strip().lower()
    element_raw = (form.get("archimate_element_id") or "").strip()
    if not name or not category:
        return None, "Technology and domain are required."
    if status not in TECHNOLOGY_STANDARD_STATUSES:
        return None, "Choose a status for the standard."
    if ring and ring not in RADAR_RINGS:
        return None, "Choose a radar ring from the list."
    dates = {}
    for field in ("sunset_date", "review_date"):
        raw = (form.get(field) or "").strip()
        try:
            dates[field] = date.fromisoformat(raw) if raw else None
        except ValueError:
            return None, "Dates must be given as YYYY-MM-DD."
    element_id = None
    if element_raw:
        if not element_raw.isdigit():
            return None, "Choose the technology element from the search results."
        element = ArchiMateElement.query.filter(ArchiMateElement.id == int(element_raw)).first()
        if element is None or element.layer != "Technology":
            return None, "The technology element must be a Technology-layer element in this organisation."
        element_id = element.id
    if ring and element_id is None:
        return None, "A radar ring needs the technology element it applies to."

    standard = TechnologyStandard(
        technology_name=name[:255],
        category=category[:100],
        status=status,
        approved_version=(form.get("approved_version") or "").strip()[:50] or None,
        rationale=(form.get("rationale") or "").strip() or None,
        replacement_technology=(form.get("replacement_technology") or "").strip()[:255] or None,
        sunset_date=dates["sunset_date"],
        review_date=dates["review_date"],
        archimate_element_id=element_id,
        owner_id=user_id,
    )
    try:
        db.session.add(standard)
        if ring:
            radar.classify(element_id, ring, None, user_id, commit=False)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return None, str(exc)
    return standard, None


@governance_bp.route("/standards", methods=["GET", "POST"])
@login_required
@require_roles("admin", "architect")
def standards():
    """Technology Standards: the register per domain, and the form that
    publishes a new standard into it."""
    from flask import flash, request
    from flask_login import current_user

    from app.models.tech_radar import RADAR_RING_LABELS, RADAR_RINGS

    if request.method == "POST":
        try:
            standard, error = _create_standard(request.form, current_user.id)
        except Exception:  # noqa: BLE001
            db.session.rollback()
            logger.exception("technology standard create failed")
            standard, error = None, "The standard could not be saved."
        if error:
            flash(error, "error")
            return render_template(
                "governance/standards.html",
                grouped=_standards_by_domain(),
                statuses=TECHNOLOGY_STANDARD_STATUSES,
                rings=RADAR_RINGS,
                ring_labels=RADAR_RING_LABELS,
                form=request.form,
            ), 400
        flash("%s published as a %s standard." % (
            standard.technology_name, standard.status.replace("_", " ")), "success")
        return redirect(url_for("governance.standards"))

    return render_template(
        "governance/standards.html",
        grouped=_standards_by_domain(),
        statuses=TECHNOLOGY_STANDARD_STATUSES,
        rings=RADAR_RINGS,
        ring_labels=RADAR_RING_LABELS,
        form={},
    )


@governance_bp.route("/roadmap")
@login_required
def roadmap():
    """Enterprise Roadmap page."""
    return render_template("governance/roadmap.html")
