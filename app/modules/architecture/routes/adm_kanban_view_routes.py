# mass-deletion-ok — intentional removal of v1 board/move_card/update_card_status routes (ARCH-012)
"""
ADM Kanban View Routes - Web interface for TOGAF ADM kanban boards

Official module source for ADM Kanban UI.
Registered via app.modules.architecture.register()

Provides:
- v2 virtual projection board (default and explicit URL)
- 301 redirects from legacy v1 and boards URLs
- Board analytics and reporting views
"""

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required, current_user

from app.decorators import admin_required, audit_log
from app.middleware.tenant_context import current_org_id
from app.modules.genome.services.roadmap_links import redirect_to_roadmap
from app.middleware.tenant_decorators import is_active_org_admin
from app.models.adm_kanban import create_adm_phases, KanbanCard
from app.models.archimate_core import ArchiMateElement
from app.middleware.tenant_decorators import platform_admin_required

adm_kanban_view_bp = Blueprint("adm_kanban_view", __name__, url_prefix="/adm-kanban")


@adm_kanban_view_bp.route("/")
@login_required
def index():
    """ADM Kanban — default view (v2 virtual projection board)."""
    return render_template("adm_kanban/board_v2.html")


@adm_kanban_view_bp.route("/v2")
@login_required
def board_v2():
    """ADM Kanban v2 — virtual projection board (explicit URL, 301 alias)."""
    return redirect(url_for("adm_kanban_view.index"), code=301)


@adm_kanban_view_bp.route("/v1")
@login_required
def v1_redirect():
    """Legacy v1 board listing — 301 redirect to v2."""
    return redirect(url_for("adm_kanban_view.index"), code=301)


@adm_kanban_view_bp.route("/boards")
@adm_kanban_view_bp.route("/boards/")
@login_required
def boards_redirect():
    """Legacy v1 boards list — 301 redirect to v2."""
    return redirect(url_for("adm_kanban_view.index"), code=301)


@adm_kanban_view_bp.route("/boards/<path:rest>")
@login_required
def boards_detail_redirect(rest):
    """Legacy v1 board detail/create/settings/analytics — 301 redirect to v2."""
    return redirect(url_for("adm_kanban_view.index"), code=301)


@adm_kanban_view_bp.route("/init-phases", methods=["POST"])
@login_required
@admin_required
@audit_log("init_adm_phases")
@platform_admin_required
def init_phases():
    """Initialize ADM phases (admin only)"""
    try:
        if not is_active_org_admin():
            flash("Admin access required.", "error")
            return redirect(url_for("adm_kanban_view.index"))

        create_adm_phases()
        flash("ADM phases initialized successfully!", "success")
    except Exception as e:
        current_app.logger.error(f"Error initializing phases: {str(e)}")
        flash("Error initializing phases. Please try again.", "error")

    return redirect(url_for("adm_kanban_view.index"))


@adm_kanban_view_bp.route("/approvals/<int:approval_id>")
@login_required
def approval_detail(approval_id):
    """View approval detail for an ADM phase transition request."""
    # Approval model not yet implemented — render template with board context
    flash("Approval workflow details are not yet available.", "warning")
    return redirect(url_for("adm_kanban_view.index"))


@adm_kanban_view_bp.route("/approvals/<int:approval_id>/compliance")
@login_required
def compliance_checklist(approval_id):
    """View compliance checklist for an ADM approval."""
    # Approval model not yet implemented — render template with board context
    flash("Compliance checklist is not yet available.", "warning")
    return redirect(url_for("adm_kanban_view.index"))


@adm_kanban_view_bp.route("/gap-analysis")
@login_required
def gap_analysis():
    """Gap Analysis — show Gap cards and the WorkPackages that close them."""
    try:
        gaps = (
            KanbanCard.query
            .filter_by(arch_element_type="Gap", organization_id=current_user.organization_id)
            .order_by(KanbanCard.adm_phase_id, KanbanCard.title)
            .all()
        )
        closers = (
            KanbanCard.query
            .filter(KanbanCard.closes_gap_id.isnot(None), KanbanCard.organization_id == current_user.organization_id)
            .order_by(KanbanCard.title)
            .all()
        )
        gap_closers: dict = {}
        for card in closers:
            gap_closers.setdefault(card.closes_gap_id, []).append(card)

        board_unclosed_count = sum(1 for g in gaps if g.id not in gap_closers)
    except Exception as exc:
        current_app.logger.error("Gap analysis query failed: %s", exc)
        gaps, gap_closers, board_unclosed_count = [], {}, 0

    # The figures are the recorded gaps: the one register the roadmap and the
    # gaps register count.
    from app.services.gap_register_service import count_gaps

    organization_id = current_org_id()
    recorded_count = count_gaps(organization_id)
    covered_count = count_gaps(organization_id, has_work_package=True)
    unplanned_count = count_gaps(organization_id, has_work_package=False)
    closed_count = count_gaps(organization_id, closed=True)

    # Portfolio findings listed under the figures (not gap signals; the signal figure is below).
    computed_gaps = {}
    try:
        from app.modules.solutions_strategic.v2.services.gap_analysis_service import ArchitecturalGapAnalyzer
        computed_gaps = ArchitecturalGapAnalyzer().analyze_portfolio_gaps()
    except Exception as exc:
        current_app.logger.debug("Portfolio gap analysis unavailable: %s", exc)

    # The signal figure is the roadmap's: one computation, shown the same on every screen.
    computed_gap_count = None
    try:
        from app.modules.capabilities.routes.roadmap_routes import compute_gap_signals
        computed_gap_count = len(compute_gap_signals())
    except Exception as exc:
        current_app.logger.warning("Gap signals unavailable: %s", exc)

    return render_template(
        "adm_kanban/gap_analysis.html",
        gaps=gaps,
        gap_closers=gap_closers,
        recorded_count=recorded_count,
        covered_count=covered_count,
        unclosed_count=unplanned_count,
        closed_count=closed_count,
        board_unclosed_count=board_unclosed_count,
        computed_gaps=computed_gaps,
        computed_gap_count=computed_gap_count,
    )


@adm_kanban_view_bp.route("/plateau-roadmap")
@login_required
def plateau_roadmap():
    """The plateau roadmap is the one roadmap, Strategic view (plateaus and the critical path)."""
    return redirect_to_roadmap("strategic")


@adm_kanban_view_bp.route("/roadmap")
@login_required
def adm_roadmap_timeline():
    """The ADM roadmap is the one roadmap, Strategic view."""
    return redirect_to_roadmap("strategic")


@adm_kanban_view_bp.route("/api/elements/search")
@login_required
def search_elements():
    """Search ArchiMate elements by name for element picker."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})
    try:
        elements = (
            ArchiMateElement.query
            .filter(ArchiMateElement.name.ilike(f"%{q}%"))
            .order_by(ArchiMateElement.name)
            .limit(20)
            .all()
        )
        return jsonify({
            "results": [
                {
                    "id": el.id,
                    "name": el.name,
                    "type": el.type or "",
                    "layer": el.layer or "",
                }
                for el in elements
            ]
        })
    except Exception as e:
        current_app.logger.exception(f"Element search error: {str(e)}")
        return jsonify({"error": "Could not search ArchiMate elements"}), 500
