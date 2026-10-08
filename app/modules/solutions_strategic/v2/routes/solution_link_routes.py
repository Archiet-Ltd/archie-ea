from app.services.archimate_backbone import sync_archimate_element
import logging
from flask import abort, g, jsonify, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.apqc_process import APQCProcess
from app.models.solution_governance import SolutionNotification
from app.models.solution_sad_models import SolutionADRDirect, SolutionAPQCProcess
from app.models.solution_models import Solution
from app.decorators import audit_log
from .solution_design_routes import (
    _get_solution_requirements,
    solution_design_bp,
)
from app.utils.pagination import safe_int_arg
from app.utils.tenant_users import escape_like_literal

logger = logging.getLogger(__name__)

# PLT-014: Completeness thresholds that trigger notifications (ascending order)
_COMPLETENESS_THRESHOLDS = [50, 75, 100]


def _check_completeness_threshold(solution, old_score):
    """PLT-014: Create notification if completeness score crosses a threshold.

    Compares the new score against _COMPLETENESS_THRESHOLDS. If the new score
    crosses a threshold that old_score was below, notify the solution owner.
    Caller must commit.
    """
    if not solution or not getattr(solution, 'created_by_id', None):
        return
    if old_score is None:
        # The "before" score could not be read, so no crossing can be proven.
        # Notifying anyway would assert a threshold the user never crossed.
        return
    try:
        result = solution.architecture_completeness_score
        new_score = result.get("score", 0) if isinstance(result, dict) else 0
    except Exception:
        return
    for threshold in _COMPLETENESS_THRESHOLDS:
        if old_score < threshold <= new_score:
            _create_notification(
                user_id=solution.created_by_id,
                notification_type="solution_update",
                message=(
                    f"Solution '{solution.name}' completeness reached "
                    f"{new_score}% (crossed {threshold}% threshold)."
                ),
                solution_id=solution.id,
            )
            break  # Only notify for the highest threshold crossed


def _get_completeness_score(solution):
    """PLT-014: Get the current completeness score, or None if unreadable.

    Returns None — not 0 — when the score cannot be computed. The only caller
    passes this to `_check_completeness_threshold` as the *before* score, and
    0 is the one value that makes every threshold look freshly crossed: a
    failure here used to send the owner "completeness reached 100%" on a score
    that was never read. `_check_completeness_threshold` skips on None.
    """
    try:
        result = solution.architecture_completeness_score
        return result.get("score", 0) if isinstance(result, dict) else 0
    except Exception:
        logger.exception("Completeness score unreadable for solution %s",
                         getattr(solution, "id", None))
        return None


def _create_notification(user_id, notification_type, message, solution_id=None):
    """Create a solution lifecycle notification (ENT-020). Caller must commit.

    PLT-017: Checks the target user's notification_preferences before inserting.
    """
    if not user_id:
        return
    _type_to_pref = {
        "arb_submission": "arb_decisions",
        "outcome_recorded": "arb_decisions",
        "phase_advance": "solution_updates",
        "solution_update": "solution_updates",
        "assignment": "assignment_changes",
        "weekly_digest": "weekly_digest",
        "comment_mention": "mention_notifications",
    }
    pref_key = _type_to_pref.get(notification_type)
    if pref_key:
        try:
            from app.models.user import User
            target_user = db.session.get(User, user_id)
            if target_user and not target_user.get_notification_preference(pref_key):
                return
        except Exception as e:
            logger.debug("Could not check notification preference for user %s: %s", user_id, e)
    try:
        n = SolutionNotification(
            solution_id=solution_id,
            user_id=user_id,
            type=notification_type,
            message=message,
        )
        db.session.add(n)
    except Exception as e:
        logger.debug("Could not create notification: %s", e)


# =============================================================================
# SOLUTION LIFECYCLE NOTIFICATIONS (ENT-012)
# =============================================================================


@solution_design_bp.route("/notifications", methods=["GET"])
@login_required
def list_solution_notifications():
    """List notifications for the current user. Returns unread_count for badge."""
    limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 100)
    unread_only = request.args.get("unread_only", "false").lower() == "true"
    q = SolutionNotification.query.filter_by(user_id=current_user.id).order_by(
        SolutionNotification.created_at.desc()
    )
    if unread_only:
        q = q.filter_by(read=False)
    notifications = q.limit(limit).all()
    unread_count = SolutionNotification.query.filter_by(
        user_id=current_user.id, read=False
    ).count()
    # Include url for each notification so header can link to solution
    items = []
    for n in notifications:
        d = n.to_dict()
        if n.solution_id:
            d["url"] = url_for("solution_design.view_solution", solution_id=n.solution_id)
        else:
            d["url"] = "#"
        items.append(d)
    return jsonify({
        "notifications": items,
        "items": items,
        "unread_count": unread_count,
    })


@solution_design_bp.route("/notifications/<int:notification_id>/read", methods=["PUT"])
@login_required
def mark_solution_notification_read(notification_id: int):
    """Mark a notification as read."""
    n = SolutionNotification.query.filter_by(
        id=notification_id, user_id=current_user.id
    ).first_or_404()
    n.read = True
    db.session.commit()
    return jsonify({"success": True, "id": n.id})


# =============================================================================
# USERS SEARCH (for @mention autocomplete ENT-022)
# =============================================================================


@solution_design_bp.route("/users", methods=["GET"])
@login_required
def api_solution_users_search():
    """Search users for @mention autocomplete (ENT-022)."""
    from app.models.user import User
    q = (request.args.get("search") or "").strip()[:50]
    if not q:
        return jsonify({"users": []})
    term = f"%{q}%"
    users = User.query.filter(
        User.organization_id == g.current_org_id,
        or_(
            User.email.ilike(term),
            User.first_name.ilike(term),
            User.last_name.ilike(term),
        )
    ).limit(10).all()
    result = []
    for u in users:
        name = getattr(u, "full_name", None) and u.full_name() or getattr(u, "first_name", None) or u.email or ""
        result.append({"id": u.id, "email": u.email, "name": name or u.email})
    return jsonify({"users": result})


# =============================================================================
# ACTIVITY FEED (ENT-022)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/activity", methods=["GET"])
@login_required
def api_solution_activity(solution_id: int):
    """Chronological activity feed: comments and entity changes (ENT-022)."""
    from app.models.solution_models import SolutionComment
    solution = Solution.query.get_or_404(solution_id)
    if solution.created_by_id != current_user.id and not current_user.is_admin:
        abort(403)
    limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 100)
    activities = []
    comments = SolutionComment.query.filter_by(solution_id=solution_id).order_by(
        SolutionComment.created_at.desc()
    ).limit(limit).all()
    for c in comments:
        activities.append({
            "type": "comment",
            "entity_type": "comment",
            "user": c.author_name or "Unknown",
            "timestamp": c.created_at.isoformat() if c.created_at else "",
            "summary": (c.content or "")[:120] + ("..." if (c.content or "") and len(c.content or "") > 120 else ""),
        })
    activities.sort(key=lambda x: x["timestamp"], reverse=True)
    return jsonify({"activities": activities[:limit]})


# =============================================================================
# SECTION COMMENT THREADS (SDX-022)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/comments", methods=["GET"])
@login_required
def api_get_comments(solution_id: int):
    """Get comments for a solution, optionally filtered by section."""
    from app.models.solution_models import SolutionComment

    Solution.query.get_or_404(solution_id)
    section = request.args.get("section", "").strip()

    query = SolutionComment.query.filter_by(solution_id=solution_id)
    if section:
        query = query.filter_by(section_name=section)
    all_comments = query.order_by(SolutionComment.created_at.asc()).all()

    # Nest replies under parent comments
    by_id = {c.id: {**c.to_dict(), "replies": []} for c in all_comments}
    top_level = []
    for c in all_comments:
        c_dict = by_id[c.id]
        if c.parent_comment_id and c.parent_comment_id in by_id:
            by_id[c.parent_comment_id]["replies"].append(c_dict)
        else:
            top_level.append(c_dict)

    return jsonify({
        "success": True,
        "comments": top_level,
    })


@solution_design_bp.route("/<int:solution_id>/comments", methods=["POST"])
@login_required
@audit_log("add_solution_comment")
def api_add_comment(solution_id: int):
    """Add a comment to a specific section of a solution."""
    from app.models.solution_models import SolutionComment

    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json()

    section_name = (data.get("section_name") or "").strip()
    content = (data.get("content") or "").strip()

    if not section_name or section_name not in SolutionComment.VALID_SECTIONS:
        return jsonify({"success": False, "error": "Invalid section_name"}), 400
    if not content:
        return jsonify({"success": False, "error": "Comment content is required"}), 400
    if len(content) > 2000:
        return jsonify({"success": False, "error": "Comment too long (max 2000 chars)"}), 400

    try:
        author_name = current_user.full_name() if hasattr(current_user, 'full_name') and callable(current_user.full_name) else (current_user.email or "Unknown")
        parent_comment_id = data.get("parent_comment_id")
        comment = SolutionComment(
            solution_id=solution_id,
            section_name=section_name,
            author_id=current_user.id,
            author_name=author_name,
            content=content,
            parent_comment_id=int(parent_comment_id) if parent_comment_id else None,
        )
        db.session.add(comment)
        db.session.commit()

        # Notify other commenters (ENT-020)
        from sqlalchemy import distinct
        other_author_ids = [
            r[0] for r in
            db.session.query(distinct(SolutionComment.author_id))
            .filter(SolutionComment.solution_id == solution_id, SolutionComment.author_id != current_user.id)
            .all()
        ]
        for uid in other_author_ids:
            if uid:
                _create_notification(
                    uid,
                    "comment",
                    f"New comment on solution '{solution.name}' ({section_name}).",
                    solution_id=solution_id,
                )
        if other_author_ids:
            db.session.commit()

        return jsonify({
            "success": True,
            "comment": comment.to_dict(),
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding comment: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to add comment"}), 500


# =============================================================================
# LINK / UNLINK — Associated Applications
# =============================================================================


def _import_app_capabilities(solution_id: int, app_id: int, user_id: int) -> int:
    """CAP-027: Import BusinessCapability mappings from a linked app into SolutionCapabilityMapping.

    Best-effort — never raises, rolls back on any failure. Returns count imported.
    """
    try:
        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.solution_models import SolutionCapabilityMapping

        app_caps = (
            # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
            ApplicationCapabilityMapping.query
            .filter_by(application_component_id=app_id, is_active=True)
            .limit(25)
            .all()
        )
        if not app_caps:
            return 0

        imported = 0
        for acm in app_caps:
            already = SolutionCapabilityMapping.query.filter_by(
                solution_id=solution_id,
                capability_id=acm.business_capability_id,
            ).first()
            if not already:
                db.session.add(SolutionCapabilityMapping(
                    solution_id=solution_id,
                    capability_id=acm.business_capability_id,
                    support_level="existing",
                    coverage_percentage=acm.coverage_percentage,
                    created_by_id=user_id,
                ))
                imported += 1

        if imported:
            db.session.commit()
            logger.info(
                "CAP-027: Auto-imported %d capabilities for solution %s from app %s",
                imported, solution_id, app_id,
            )
        return imported
    except Exception as exc:
        db.session.rollback()
        logger.warning("CAP-027: Capability auto-import skipped: %s", exc)
        return 0


@solution_design_bp.route("/<int:solution_id>/link-application", methods=["POST"])
@login_required
def link_application(solution_id):
    """Link an application to this solution via the junction table."""
    # tenant-filtered: scoped via parent FK (solution_applications junction)
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    app_id = data.get("application_id")
    if not app_id:
        return jsonify({"success": False, "error": "application_id required"}), 400
    app_obj = ApplicationComponent.query.get(app_id)
    if not app_obj:
        return jsonify({"success": False, "error": "Application not found"}), 404
    try:
        tbl = db.metadata.tables.get("solution_applications")
        if tbl is None:
            return jsonify({"success": False, "error": "Junction table not found"}), 500
        existing = db.session.execute(  # tenant-filtered: scoped via solution_id FK
            tbl.select().where(tbl.c.solution_id == solution_id).where(tbl.c.application_component_id == app_id)
        ).first()
        if existing:
            return jsonify({"success": False, "error": "Already linked"}), 409
        old_score = _get_completeness_score(solution)  # PLT-014
        db.session.execute(tbl.insert().values(solution_id=solution_id, application_component_id=app_id))  # tenant-filtered
        db.session.commit()
        _check_completeness_threshold(solution, old_score)  # PLT-014
        db.session.commit()
        # CAP-027: auto-import capability mappings from the linked application
        caps_imported = _import_app_capabilities(solution_id, app_id, current_user.id)
        return jsonify({"success": True, "capabilities_imported": caps_imported})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error linking application: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/capability-coverage", methods=["GET"])
@login_required
def capability_coverage(solution_id):
    """CAP-027: Capability coverage for the blueprint value_stream_map panel.

    For each SolutionCapabilityMapping, returns the capability name/code and
    which solution-linked apps already cover it. Flags has_gap=true when no
    app provides coverage.
    """
    Solution.query.get_or_404(solution_id)

    try:
        from app.models.solution_models import SolutionCapabilityMapping
        from app.models.business_capabilities import BusinessCapability
        from app.models.application_capability import ApplicationCapabilityMapping

        mappings = (
            SolutionCapabilityMapping.query
            .filter_by(solution_id=solution_id)
            .limit(50)
            .all()
        )
        if not mappings:
            return jsonify({"success": True, "capabilities": [], "total": 0, "gap_count": 0})

        # Get app IDs linked to this solution
        tbl = db.metadata.tables.get("solution_applications")
        app_ids = []
        if tbl is not None:
            rows = db.session.execute(
                tbl.select().where(tbl.c.solution_id == solution_id)
            ).fetchall()
            app_ids = [r[1] for r in rows]

        results = []
        for m in mappings:
            cap = BusinessCapability.query.get(m.capability_id)
            if not cap:
                continue

            covering_app_names = []
            if app_ids:
                acm_list = (
                    # tenant-scoping-ok: FK id already org-scoped (application/capability resolved via a TenantMixin model or the current request's own app/solution).
                    ApplicationCapabilityMapping.query
                    .filter(
                        ApplicationCapabilityMapping.application_component_id.in_(app_ids),
                        ApplicationCapabilityMapping.business_capability_id == m.capability_id,
                        ApplicationCapabilityMapping.is_active.is_(True),
                    )
                    .with_entities(ApplicationCapabilityMapping.application_component_id)
                    .limit(5)
                    .all()
                )
                if acm_list:
                    covering_ids = [r[0] for r in acm_list]
                    apps = ApplicationComponent.query.filter(
                        ApplicationComponent.id.in_(covering_ids)
                    ).with_entities(ApplicationComponent.name).all()
                    covering_app_names = [a[0] for a in apps]

            results.append({
                "capability_id": cap.id,
                "capability_name": cap.name,
                "capability_code": cap.code or "",
                "support_level": m.support_level or "existing",
                "covering_apps": covering_app_names,
                "has_gap": len(covering_app_names) == 0,
            })

        gap_count = sum(1 for r in results if r["has_gap"])
        return jsonify({
            "success": True,
            "capabilities": results,
            "total": len(results),
            "gap_count": gap_count,
        })
    except Exception as exc:
        logger.error("CAP-027: capability_coverage failed for solution %s: %s", solution_id, exc, exc_info=True)
        return jsonify({"success": False, "error": "Failed to load capability coverage"}), 500


@solution_design_bp.route("/<int:solution_id>/unlink-application/<int:app_id>", methods=["DELETE"])
@login_required
def unlink_application(solution_id, app_id):
    """Unlink an application from this solution."""
    # tenant-filtered: scoped via parent FK (solution_applications junction)
    Solution.query.get_or_404(solution_id)
    try:
        tbl = db.metadata.tables.get("solution_applications")
        if tbl is None:
            return jsonify({"success": False, "error": "Junction table not found"}), 500
        db.session.execute(  # tenant-filtered: scoped via solution_id FK
            tbl.delete().where(tbl.c.solution_id == solution_id).where(tbl.c.application_component_id == app_id)
        )
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error unlinking application: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# LINK / UNLINK — Vendor Products
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/link-vendor-product", methods=["POST"])
@login_required
def link_vendor_product(solution_id):
    """Link a vendor product to this solution via the junction table."""
    # tenant-filtered: scoped via parent FK (solution_vendor_products junction)
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    vp_id = data.get("vendor_product_id")
    if not vp_id:
        return jsonify({"success": False, "error": "vendor_product_id required"}), 400
    try:
        from app.models.vendor.vendor_organization import VendorProduct
        vp = VendorProduct.query.get(vp_id)
        if not vp:
            return jsonify({"success": False, "error": "Vendor product not found"}), 404
        tbl = db.metadata.tables.get("solution_vendor_products")
        if tbl is None:
            return jsonify({"success": False, "error": "Junction table not found"}), 500
        existing = db.session.execute(  # tenant-filtered: scoped via solution_id FK
            tbl.select().where(tbl.c.solution_id == solution_id).where(tbl.c.vendor_product_id == vp_id)
        ).first()
        if existing:
            return jsonify({"success": False, "error": "Already linked"}), 409
        old_score = _get_completeness_score(solution)  # PLT-014
        db.session.execute(tbl.insert().values(solution_id=solution_id, vendor_product_id=vp_id))  # tenant-filtered
        db.session.commit()
        _check_completeness_threshold(solution, old_score)  # PLT-014
        db.session.commit()
        # SAP BTP: auto-populate canonical ArchiMate elements for SAP/Microsoft vendors
        template_result = {"linked": 0, "skipped": 0, "elements": []}
        try:
            from app.modules.solutions_strategic.v2.services.vendor_template_service import VendorTemplateService
            template_result = VendorTemplateService.populate_from_vendor(
                solution_id, vp_id, current_user.id
            )
        except Exception as _te:
            logger.warning("VendorTemplateService failed for solution=%s product=%s: %s", solution_id, vp_id, _te)
        return jsonify({"success": True, "template_elements": template_result})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error linking vendor product: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/unlink-vendor-product/<int:vp_id>", methods=["DELETE"])
@login_required
def unlink_vendor_product(solution_id, vp_id):
    """Unlink a vendor product from this solution."""
    # tenant-filtered: scoped via parent FK (solution_vendor_products junction)
    Solution.query.get_or_404(solution_id)
    try:
        tbl = db.metadata.tables.get("solution_vendor_products")
        if tbl is None:
            return jsonify({"success": False, "error": "Junction table not found"}), 500
        db.session.execute(  # tenant-filtered: scoped via solution_id FK
            tbl.delete().where(tbl.c.solution_id == solution_id).where(tbl.c.vendor_product_id == vp_id)
        )
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error unlinking vendor product: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# LINK / UNLINK — Architecture Decision Records
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/link-adr", methods=["POST"])
@login_required
def link_adr(solution_id):
    """Directly link an ADR to this solution."""
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    adr_id = data.get("adr_id")
    if not adr_id:
        return jsonify({"success": False, "error": "adr_id required"}), 400
    try:
        from app.models.adr import ArchitectureDecisionRecord
        adr = ArchitectureDecisionRecord.query.get(adr_id)
        if not adr:
            return jsonify({"success": False, "error": "ADR not found"}), 404
        existing = SolutionADRDirect.query.filter_by(solution_id=solution_id, adr_id=adr_id).first()
        if existing:
            return jsonify({"success": False, "error": "Already linked"}), 409
        old_score = _get_completeness_score(solution)  # PLT-014
        link = SolutionADRDirect(solution_id=solution_id, adr_id=adr_id, linked_by_id=current_user.id)
        db.session.add(link)
        db.session.commit()
        _check_completeness_threshold(solution, old_score)  # PLT-014
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error linking ADR: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/unlink-adr/<int:adr_id>", methods=["DELETE"])
@login_required
def unlink_adr(solution_id, adr_id):
    """Unlink an ADR from this solution (direct link only)."""
    Solution.query.get_or_404(solution_id)
    try:
        link = SolutionADRDirect.query.filter_by(solution_id=solution_id, adr_id=adr_id).first()
        if link:
            db.session.delete(link)
            db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error unlinking ADR: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/search-adrs", methods=["GET"])
@login_required
def search_adrs():
    """Search architecture decision records by title/number."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    try:
        from app.models.adr import ArchitectureDecisionRecord
        safe_q = escape_like_literal(q)
        results = ArchitectureDecisionRecord.query.filter(
            or_(
                ArchitectureDecisionRecord.title.ilike(f"%{safe_q}%", escape="\\"),
                ArchitectureDecisionRecord.adr_number.cast(db.String).ilike(f"%{safe_q}%", escape="\\"),
            )
        ).order_by(ArchitectureDecisionRecord.adr_number).limit(10).all()
        return jsonify({
            "results": [
                {
                    "id": a.id, "adr_number": a.adr_number, "title": a.title,
                    "status": a.status or "draft",
                }
                for a in results
            ]
        })
    except Exception as e:
        logger.exception(f"ADR search error: {e}")
        return jsonify({"success": False, "error": "ADR search failed"}), 500


# =============================================================================
# LINK / UNLINK — APQC Processes
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/link-apqc-process", methods=["POST"])
@login_required
def link_apqc_process(solution_id):
    """Directly link an APQC process to this solution."""
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    process_id = data.get("apqc_process_id")
    if not process_id:
        return jsonify({"success": False, "error": "apqc_process_id required"}), 400
    try:
        proc = APQCProcess.query.get(process_id)
        if not proc:
            return jsonify({"success": False, "error": "APQC process not found"}), 404
        existing = SolutionAPQCProcess.query.filter_by(solution_id=solution_id, apqc_process_id=process_id).first()
        if existing:
            return jsonify({"success": False, "error": "Already linked"}), 409
        old_score = _get_completeness_score(solution)  # PLT-014
        link = SolutionAPQCProcess(solution_id=solution_id, apqc_process_id=process_id, linked_by_id=current_user.id)
        db.session.add(link)
        db.session.commit()
        _check_completeness_threshold(solution, old_score)  # PLT-014
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error linking APQC process: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/unlink-apqc-process/<int:process_id>", methods=["DELETE"])
@login_required
def unlink_apqc_process(solution_id, process_id):
    """Unlink an APQC process from this solution (direct link only)."""
    Solution.query.get_or_404(solution_id)
    try:
        link = SolutionAPQCProcess.query.filter_by(solution_id=solution_id, apqc_process_id=process_id).first()
        if link:
            db.session.delete(link)
            db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error unlinking APQC process: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# LIST ALL — Options for multi-select picker modals
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/all-applications", methods=["GET"])
@login_required
def all_applications(solution_id):
    """Return all applications for the multi-select picker."""
    Solution.query.get_or_404(solution_id)
    try:
        apps = ApplicationComponent.query.order_by(ApplicationComponent.name).limit(500).all()
        return jsonify({
            "items": [
                {"id": a.id, "name": a.name, "sub": getattr(a, "app_abbreviation", None) or ""}
                for a in apps
            ]
        })
    except Exception as e:
        logger.exception(f"Error listing applications: {e}")
        return jsonify({"success": False, "error": "Could not load applications"}), 500


@solution_design_bp.route("/<int:solution_id>/all-vendor-products", methods=["GET"])
@login_required
def all_vendor_products(solution_id):
    """Return all vendor products for the multi-select picker."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.vendor.vendor_organization import VendorProduct
        vps = VendorProduct.query.options(
            joinedload(VendorProduct.vendor_organization)
        ).order_by(VendorProduct.name).limit(500).all()
        return jsonify({
            "items": [
                {
                    "id": v.id, "name": v.name,
                    "sub": (v.vendor_organization.name if getattr(v, "vendor_organization", None) else "Unknown"),
                }
                for v in vps
            ]
        })
    except Exception as e:
        logger.exception(f"Error listing vendor products: {e}")
        return jsonify({"success": False, "error": "Could not load vendor products"}), 500


@solution_design_bp.route("/<int:solution_id>/all-adrs", methods=["GET"])
@login_required
def all_adrs(solution_id):
    """Return all ADRs for the multi-select picker."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.adr import ArchitectureDecisionRecord
        adrs = ArchitectureDecisionRecord.query.order_by(ArchitectureDecisionRecord.adr_number).limit(500).all()
        return jsonify({
            "items": [
                {
                    "id": a.id,
                    "name": f"ADR-{(a.adr_number or 0):03d}: {a.title}",
                    "sub": a.status or "draft",
                }
                for a in adrs
            ]
        })
    except Exception as e:
        logger.exception(f"Error listing ADRs: {e}")
        return jsonify({"success": False, "error": "Could not load ADRs"}), 500


@solution_design_bp.route("/<int:solution_id>/all-apqc-processes", methods=["GET"])
@login_required
def all_apqc_processes(solution_id):
    """Return all APQC processes for the multi-select picker."""
    Solution.query.get_or_404(solution_id)
    try:
        procs = APQCProcess.query.order_by(APQCProcess.process_code).limit(500).all()
        return jsonify({
            "items": [
                {"id": p.id, "name": p.process_name, "sub": p.process_code or ""}
                for p in procs
            ]
        })
    except Exception as e:
        logger.exception(f"Error listing APQC processes: {e}")
        return jsonify({"success": False, "error": "Could not load APQC processes"}), 500


# =============================================================================
# SYNC — Replace all links with selected set (multi-select picker save)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/sync-applications", methods=["POST"])
@login_required
def sync_applications(solution_id):
    """Replace all application links with the given set of IDs."""
    # tenant-filtered: scoped via parent FK (solution_applications junction)
    Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    selected_ids = set(data.get("ids", []))
    try:
        tbl = db.metadata.tables.get("solution_applications")
        if tbl is None:
            return jsonify({"success": False, "error": "Junction table not found"}), 500
        (set(
            r[0] for r in db.session.execute(  # tenant-filtered: scoped via solution_id FK
                tbl.select().where(tbl.c.solution_id == solution_id)
            ).fetchall()
        ))
        # The first column after solution_id is application_component_id
        current_rows = db.session.execute(  # tenant-filtered: scoped via solution_id FK
            tbl.select().where(tbl.c.solution_id == solution_id)
        ).fetchall()
        current_ids = set(row.application_component_id for row in current_rows)
        to_add = selected_ids - current_ids
        to_remove = current_ids - selected_ids
        for app_id in to_remove:
            db.session.execute(  # tenant-filtered: scoped via solution_id FK
                tbl.delete().where(tbl.c.solution_id == solution_id).where(tbl.c.application_component_id == app_id)
            )
        for app_id in to_add:
            db.session.execute(tbl.insert().values(solution_id=solution_id, application_component_id=app_id))  # tenant-filtered
        db.session.commit()
        return jsonify({"success": True, "added": len(to_add), "removed": len(to_remove)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error syncing applications: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/sync-vendor-products", methods=["POST"])
@login_required
def sync_vendor_products(solution_id):
    """Replace all vendor product links with the given set of IDs."""
    # tenant-filtered: scoped via parent FK (solution_vendor_products junction)
    Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    selected_ids = set(data.get("ids", []))
    try:
        tbl = db.metadata.tables.get("solution_vendor_products")
        if tbl is None:
            return jsonify({"success": False, "error": "Junction table not found"}), 500
        current_rows = db.session.execute(  # tenant-filtered: scoped via solution_id FK
            tbl.select().where(tbl.c.solution_id == solution_id)
        ).fetchall()
        current_ids = set(row.vendor_product_id for row in current_rows)
        to_add = selected_ids - current_ids
        to_remove = current_ids - selected_ids
        for vp_id in to_remove:
            db.session.execute(  # tenant-filtered: scoped via solution_id FK
                tbl.delete().where(tbl.c.solution_id == solution_id).where(tbl.c.vendor_product_id == vp_id)
            )
        for vp_id in to_add:
            db.session.execute(tbl.insert().values(solution_id=solution_id, vendor_product_id=vp_id))  # tenant-filtered
        db.session.commit()
        return jsonify({"success": True, "added": len(to_add), "removed": len(to_remove)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error syncing vendor products: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/sync-adrs", methods=["POST"])
@login_required
def sync_adrs(solution_id):
    """Replace all direct ADR links with the given set of IDs."""
    # tenant-filtered: scoped via parent FK (solution ADR junction)
    Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    selected_ids = set(data.get("ids", []))
    try:
        current_links = SolutionADRDirect.query.filter_by(solution_id=solution_id).all()
        current_ids = set(link.adr_id for link in current_links)
        to_add = selected_ids - current_ids
        to_remove = current_ids - selected_ids
        for adr_id in to_remove:
            link = SolutionADRDirect.query.filter_by(solution_id=solution_id, adr_id=adr_id).first()
            if link:
                db.session.delete(link)
        for adr_id in to_add:
            db.session.add(SolutionADRDirect(solution_id=solution_id, adr_id=adr_id, linked_by_id=current_user.id))
        db.session.commit()
        return jsonify({"success": True, "added": len(to_add), "removed": len(to_remove)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error syncing ADRs: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/sync-apqc-processes", methods=["POST"])
@login_required
def sync_apqc_processes(solution_id):
    """Replace all direct APQC process links with the given set of IDs."""
    # tenant-filtered: scoped via parent FK (solution APQC junction)
    Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    selected_ids = set(data.get("ids", []))
    try:
        current_links = SolutionAPQCProcess.query.filter_by(solution_id=solution_id).all()
        current_ids = set(link.apqc_process_id for link in current_links)
        to_add = selected_ids - current_ids
        to_remove = current_ids - selected_ids
        for pid in to_remove:
            link = SolutionAPQCProcess.query.filter_by(solution_id=solution_id, apqc_process_id=pid).first()
            if link:
                db.session.delete(link)
        for pid in to_add:
            db.session.add(SolutionAPQCProcess(solution_id=solution_id, apqc_process_id=pid, linked_by_id=current_user.id))
        db.session.commit()
        return jsonify({"success": True, "added": len(to_add), "removed": len(to_remove)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error syncing APQC processes: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# CAPABILITIES — List all + sync (picker endpoints for DATA-003)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/all-capabilities", methods=["GET"])
@login_required
def all_capabilities(solution_id):
    """Return all business capabilities for the picker modal.

    Supports ``?format=tree`` to return a nested L1 → L2 → L3 hierarchy
    used by the hierarchical capability picker (CAP-016).
    """
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.business_capability import BusinessCapability

        if request.args.get("format") == "tree":
            l1_caps = (
                BusinessCapability.query
                .filter_by(level=1)
                .order_by(BusinessCapability.name)
                .all()
            )
            tree = []
            for cap in l1_caps:
                children = (
                    BusinessCapability.query
                    .filter_by(parent_capability_id=cap.id)
                    .order_by(BusinessCapability.name)
                    .all()
                )
                tree.append({
                    "id": cap.id,
                    "name": cap.name,
                    "domain": getattr(cap, "business_domain", "") or "",
                    "level": cap.level,
                    "children_count": len(children),
                    "children": [
                        {
                            "id": child.id,
                            "name": child.name,
                            "domain": getattr(child, "business_domain", "") or "",
                            "level": child.level,
                            "children": [
                                {
                                    "id": gc.id,
                                    "name": gc.name,
                                    "level": gc.level,
                                }
                                for gc in BusinessCapability.query.filter_by(
                                    parent_capability_id=child.id
                                ).order_by(BusinessCapability.name).all()
                            ],
                        }
                        for child in children
                    ],
                })
            return jsonify({"items": tree, "format": "tree"})

        caps = BusinessCapability.query.order_by(BusinessCapability.name).limit(500).all()
        return jsonify({
            "items": [
                {"id": c.id, "name": c.name, "sub": f"{c.code} — {c.business_domain}" if c.code and getattr(c, "business_domain", "") else getattr(c, "business_domain", "") or getattr(c, "code", "") or "", "description": getattr(c, "description", "") or ""}
                for c in caps
            ]
        })
    except Exception as e:
        logger.exception(f"Error listing capabilities: {e}")
        return jsonify({"success": False, "error": "Could not load capabilities"}), 500


@solution_design_bp.route("/<int:solution_id>/sync-capabilities", methods=["POST"])
@login_required
def sync_capabilities(solution_id):
    """Replace all capability mappings with the given set of IDs (picker save)."""
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    try:
        selected_ids = set(int(i) for i in data.get("ids", []) if i)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "ids must be integers"}), 400
    try:
        from app.models.solution_models import SolutionCapabilityMapping
        from app.models.solution_architect_models import SolutionProblemDefinition

        # Dual-path merge: gather mappings from BOTH paths so sync
        # doesn't lose problem_id mappings when syncing solution_id ones
        # or vice versa.
        problem_id = None
        if solution.analysis_session_id:
            pd = SolutionProblemDefinition.query.filter_by(
                session_id=solution.analysis_session_id
            ).first()
            if pd:
                problem_id = pd.id

        # Collect all current mappings from both paths
        all_mappings = []
        if problem_id is not None:
            all_mappings.extend(
                SolutionCapabilityMapping.query.filter_by(problem_id=problem_id).all()
            )
        direct_mappings = SolutionCapabilityMapping.query.filter_by(
            solution_id=solution_id, problem_id=None
        ).all()
        all_mappings.extend(direct_mappings)

        # Deduplicate: build a map of capability_id -> first mapping
        seen_caps: dict[int, SolutionCapabilityMapping] = {}
        duplicates = []
        for m in all_mappings:
            if m.capability_id in seen_caps:
                duplicates.append(m)
            else:
                seen_caps[m.capability_id] = m
        # Remove duplicate mappings (same capability from both paths)
        for dup in duplicates:
            db.session.delete(dup)

        current_ids = set(seen_caps.keys())
        to_add = selected_ids - current_ids
        to_remove = current_ids - selected_ids
        for cap_id in to_remove:
            m = seen_caps.get(cap_id)
            if m:
                db.session.delete(m)
        # New mappings use problem_id path if available, else solution_id
        for cap_id in to_add:
            if problem_id is not None:
                db.session.add(SolutionCapabilityMapping(
                    problem_id=problem_id,
                    capability_id=cap_id,
                    support_level="required",
                    created_by_id=current_user.id,
                ))
            else:
                db.session.add(SolutionCapabilityMapping(
                    solution_id=solution_id,
                    problem_id=None,
                    capability_id=cap_id,
                    support_level="required",
                    created_by_id=current_user.id,
                ))

        db.session.commit()
        return jsonify({"success": True, "added": len(to_add), "removed": len(to_remove)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error syncing capabilities: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/all-requirements", methods=["GET"])
@login_required
def all_requirements(solution_id):
    """Return all requirements in the solution's canonical requirement set."""
    # Outside the try, as in every sibling picker endpoint: a missing solution
    # is a genuine 404 and must not be swallowed into the generic handler.
    solution = Solution.query.get_or_404(solution_id)
    try:
        rows = _get_solution_requirements(solution)
        items = [
            {
                "id": r.id,
                "name": r.name or ("Requirement #" + str(r.id)),
                "sub": (r.moscow_priority or "") + (" · " + r.togaf_phase if r.togaf_phase else ""),
                "moscow_priority": r.moscow_priority,
                "togaf_phase": r.togaf_phase,
            }
            for r in rows
        ]
        return jsonify({"items": items})
    except Exception as e:
        logger.exception(f"Error loading requirements: {e}")
        return jsonify({"success": False, "error": "Could not load requirements"}), 500


@solution_design_bp.route("/<int:solution_id>/sync-requirements", methods=["POST"])
@login_required
def sync_requirements(solution_id):
    """Replace direct requirement links with the selected requirement IDs."""
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    try:
        selected_ids = set(int(i) for i in data.get("ids", []) if i)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "ids must be integers"}), 400
    try:
        from app.models.solution_architect_models import SolutionRequirement

        relevant_requirements = _get_solution_requirements(solution)
        relevant_by_id = {requirement.id: requirement for requirement in relevant_requirements}
        existing = SolutionRequirement.query.filter_by(solution_id=solution_id).all()
        current_ids = {requirement.id for requirement in existing}
        to_add = selected_ids - current_ids
        to_remove = current_ids - selected_ids

        for requirement in existing:
            if requirement.id in to_remove:
                requirement.solution_id = None

        for requirement_id in to_add:
            requirement = relevant_by_id.get(requirement_id)
            if requirement:
                requirement.solution_id = solution_id

        db.session.commit()
        return jsonify({"success": True, "added": len(to_add), "removed": len(to_remove)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error syncing requirements: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# GENERATE — ArchiMate from Capabilities (CAP-007 solution-scoped)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/generate-from-capabilities", methods=["POST"])
@login_required
def generate_from_capabilities(solution_id):
    """Generate ArchiMate elements from this solution's linked capabilities."""
    from app.models.solution_models import SolutionArchiMateElement, SolutionCapabilityMapping

    Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    capability_ids = data.get("capability_ids", [])

    if not capability_ids:
        mappings = SolutionCapabilityMapping.query.filter_by(solution_id=solution_id).all()
        capability_ids = [m.capability_id for m in mappings]

    if not capability_ids:
        return jsonify({"success": False, "error": "No capabilities linked to this solution"}), 400

    try:
        from app.modules.architecture.services.archimate_core_service import ArchiMateService
        service = ArchiMateService()
        result = service.generate_architecture_from_capabilities(capability_ids)

        created_elements = result.get("created_elements", [])
        linked_count = 0

        relationship_map = {
            "ApplicationFunction": "realizes", "ApplicationService": "serves",
            "DataObject": "accesses", "BusinessProcess": "triggers", "Requirement": "realizes",
        }

        for el in created_elements:
            el_id = getattr(el, "id", None) or (el.get("id") if isinstance(el, dict) else None)
            el_layer = getattr(el, "layer", None) or (el.get("layer", "application") if isinstance(el, dict) else "application")
            el_name = getattr(el, "name", None) or (el.get("name", "") if isinstance(el, dict) else "")
            el_type = getattr(el, "type", None) or (el.get("type", "") if isinstance(el, dict) else "")

            if not el_id:
                continue

            existing = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id, element_id=el_id, element_table="archimate_elements",
            ).first()
            if existing:
                continue

            mapping = SolutionArchiMateElement(
                solution_id=solution_id, layer_type=el_layer, element_id=el_id,
                element_table="archimate_elements", element_name=el_name,
                relationship_type=relationship_map.get(el_type, "realizes"),
                notes="AI-generated from capability-driven design",
                is_new_element=False, created_by_id=current_user.id,
            )
            db.session.add(mapping)
            linked_count += 1

        db.session.commit()

        return jsonify({
            "success": True, "elements_created": result.get("elements_created", 0),
            "linked_to_solution": linked_count,
            "data": {"elements_created": [
                {
                    "id": getattr(el, "id", None) or (el.get("id") if isinstance(el, dict) else None),
                    "name": getattr(el, "name", None) or (el.get("name", "") if isinstance(el, dict) else ""),
                    "type": getattr(el, "type", None) or (el.get("type", "") if isinstance(el, dict) else ""),
                    "layer": getattr(el, "layer", None) or (el.get("layer", "") if isinstance(el, dict) else ""),
                    "relationship_type": relationship_map.get(
                        getattr(el, "type", None) or (el.get("type", "") if isinstance(el, dict) else ""), "realizes"
                    ),
                } for el in created_elements
            ]},
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error generating from capabilities: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# LINK — Single ArchiMate Element with auto-link (CAP-009 + CAP-010)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/link-archimate-element", methods=["POST"])
@login_required
def link_archimate_element(solution_id):
    """Link a single ArchiMate element to this solution (CAP-009 staging accept).

    Also auto-links to solution_applications or solution_requirements based on
    element_type (CAP-010).
    """
    from app.models.solution_models import SolutionArchiMateElement, solution_applications

    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}

    element_id = data.get("element_id")
    element_type = (data.get("element_type") or "").strip()
    layer_type = (data.get("layer_type") or data.get("layer") or "unknown").strip().lower()
    element_name = (data.get("element_name") or "").strip()
    relationship_type = (data.get("relationship_type") or "realizes").strip()

    if not element_id:
        return jsonify({"success": False, "error": "element_id is required"}), 400

    try:
        existing = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id, element_table="archimate_elements", element_id=element_id,
        ).first()

        if existing:
            return jsonify({"success": True, "message": "Already linked", "id": existing.id})

        old_score = _get_completeness_score(solution)  # PLT-014

        mapping = SolutionArchiMateElement(
            solution_id=solution_id, layer_type=layer_type, element_id=element_id,
            element_table="archimate_elements", element_name=element_name or None,
            relationship_type=relationship_type,
            notes="AI-generated from capability-driven design",
            is_new_element=False, created_by_id=current_user.id,
        )
        db.session.add(mapping)

        # CAP-010: Auto-link to other junction tables based on element type
        auto_linked = {}

        if element_type in ("ApplicationComponent", "ApplicationService") and element_name:
            from app.models.application_portfolio import ApplicationComponent
            app_match = ApplicationComponent.query.filter(
                ApplicationComponent.name.ilike(f"%{element_name}%")
            ).first()
            if app_match:
                already = db.session.execute(  # tenant-filtered: scoped via solution_id FK
                    solution_applications.select().where(
                        db.and_(solution_applications.c.solution_id == solution_id,
                                solution_applications.c.application_component_id == app_match.id)
                    )
                ).first()
                if not already:
                    db.session.execute(solution_applications.insert().values(  # tenant-filtered
                        solution_id=solution_id, application_component_id=app_match.id, role="supporting",
                    ))
                    auto_linked["application"] = {"id": app_match.id, "name": app_match.name}

        if element_type == "Requirement" and element_name:
            from app.models.solution_architect_models import SolutionRequirement
            existing_req = SolutionRequirement.query.filter_by(
                solution_id=solution_id, name=element_name
            ).first()
            if not existing_req:
                req = SolutionRequirement(
                    solution_id=solution_id, name=element_name,
                    description=f"Auto-generated: {element_name}",
                    source="AI-generated from capability-driven design",
                )
                db.session.add(req)
                sync_archimate_element(req)
                auto_linked["requirement"] = {"name": element_name}

        db.session.commit()

        # PLT-014: Check completeness threshold crossing
        _check_completeness_threshold(solution, old_score)
        db.session.commit()

        return jsonify({
            "success": True, "id": mapping.id,
            "message": f"Linked {element_name or 'element'} to solution",
            "auto_linked": auto_linked,
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error linking ArchiMate element: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to link element"}), 500


# =============================================================================
# ERP FIT-GAP ANALYSIS — SAP BTP Extension Governance
# =============================================================================

@solution_design_bp.route("/<int:solution_id>/fit-gap", methods=["GET"])
@login_required
def get_fit_gap_entries(solution_id):
    """List all fit-gap entries for a solution, ordered by sort_order."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.solution_models import SolutionFitGapEntry
        entries = SolutionFitGapEntry.query.filter_by(
            solution_id=solution_id
        ).order_by(SolutionFitGapEntry.sort_order, SolutionFitGapEntry.id).all()
        return jsonify({"entries": [e.to_dict() for e in entries]})
    except Exception as e:
        logger.error("get_fit_gap_entries failed solution=%s: %s", solution_id, e)
        return jsonify({"error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/fit-gap", methods=["POST"])
@login_required
def create_fit_gap_entry(solution_id):
    """Create a new fit-gap entry. Writes provenance from current_user."""
    solution = Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    business_process = (data.get("business_process") or "").strip()
    if not business_process:
        return jsonify({"success": False, "error": "business_process is required"}), 400
    try:
        from app.models.solution_models import SolutionFitGapEntry
        from app.models.architecture_review_board import ARBAuditLog
        from datetime import datetime
        entry = SolutionFitGapEntry(
            solution_id=solution_id,
            business_process=business_process,
            erp_module=(data.get("erp_module") or "").strip() or None,
            fit_type=data.get("fit_type") or "standard",
            justification=(data.get("justification") or "").strip() or None,
            status=data.get("status") or "draft",
            capability_id=data.get("capability_id") or None,
            requirement_id=data.get("requirement_id") or None,
            option_id=data.get("option_id") or None,
            estimated_effort_days=data.get("estimated_effort_days") or None,
            sort_order=data.get("sort_order") or 0,
            provenance={
                "created_by": current_user.id,
                "created_at": datetime.utcnow().isoformat(),
                "source": "manual",
            },
        )
        db.session.add(entry)
        # Increment blueprint version
        if hasattr(solution, "blueprint_version"):
            solution.blueprint_version = (solution.blueprint_version or 1) + 1
        db.session.flush()
        # Audit log
        try:
            audit = ARBAuditLog(
                entity_type="solution_fit_gap_entry",
                entity_id=entry.id,
                action="create",
                user_id=current_user.id,
                new_value={"solution_id": solution_id, "business_process": business_process},
            )
            db.session.add(audit)
        except Exception as exc:
            logger.debug("suppressed error in create_fit_gap_entry (app/modules/solutions_strategic/v2/routes/solution_link_routes.py): %s", exc)  # Audit is best-effort
        db.session.commit()
        return jsonify({"success": True, "entry": entry.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        logger.error("create_fit_gap_entry failed solution=%s: %s", solution_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/fit-gap/<int:entry_id>", methods=["PATCH"])
@login_required
def update_fit_gap_entry(solution_id, entry_id):
    """Partial update of a fit-gap entry."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.solution_models import SolutionFitGapEntry
        from app.models.architecture_review_board import ARBAuditLog
        entry = SolutionFitGapEntry.query.filter_by(
            id=entry_id, solution_id=solution_id
        ).first_or_404()
        data = request.get_json() or {}
        updatable = [
            "business_process", "erp_module", "fit_type", "justification",
            "status", "capability_id", "requirement_id", "option_id",
            "estimated_effort_days", "sort_order",
        ]
        for field in updatable:
            if field in data:
                setattr(entry, field, data[field])
        try:
            audit = ARBAuditLog(
                entity_type="solution_fit_gap_entry",
                entity_id=entry_id,
                action="update",
                user_id=current_user.id,
                new_value={"fields_updated": [f for f in updatable if f in data]},
            )
            db.session.add(audit)
        except Exception as exc:
            logger.debug("suppressed error in update_fit_gap_entry (app/modules/solutions_strategic/v2/routes/solution_link_routes.py): %s", exc)
        db.session.commit()
        return jsonify({"success": True, "entry": entry.to_dict()})
    except Exception as e:
        db.session.rollback()
        logger.error("update_fit_gap_entry failed solution=%s entry=%s: %s", solution_id, entry_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/fit-gap/<int:entry_id>", methods=["DELETE"])
@login_required
def delete_fit_gap_entry(solution_id, entry_id):
    """Hard-delete a fit-gap entry."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.solution_models import SolutionFitGapEntry
        from app.models.architecture_review_board import ARBAuditLog
        entry = SolutionFitGapEntry.query.filter_by(
            id=entry_id, solution_id=solution_id
        ).first_or_404()
        try:
            audit = ARBAuditLog(
                entity_type="solution_fit_gap_entry",
                entity_id=entry_id,
                action="delete",
                user_id=current_user.id,
                new_value={"solution_id": solution_id},
            )
            db.session.add(audit)
        except Exception as exc:
            logger.debug("suppressed error in delete_fit_gap_entry (app/modules/solutions_strategic/v2/routes/solution_link_routes.py): %s", exc)
        db.session.delete(entry)
        db.session.commit()
        return jsonify({"success": True}), 204
    except Exception as e:
        db.session.rollback()
        logger.error("delete_fit_gap_entry failed solution=%s entry=%s: %s", solution_id, entry_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/fit-gap/export.csv", methods=["GET"])
@login_required
def export_fit_gap_csv(solution_id):
    """Export fit-gap entries as CSV for SI handoff."""
    Solution.query.get_or_404(solution_id)
    try:
        from app.models.solution_models import SolutionFitGapEntry
        import csv
        import io
        entries = SolutionFitGapEntry.query.filter_by(
            solution_id=solution_id
        ).order_by(SolutionFitGapEntry.sort_order, SolutionFitGapEntry.id).all()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "Business Process", "ERP Module", "Fit Type",
            "Justification", "Status", "Effort Days", "Capability ID",
            "Requirement ID", "Option ID", "Created At",
        ])
        for e in entries:
            writer.writerow([
                e.id, e.business_process, e.erp_module or "", e.fit_type or "",
                e.justification or "", e.status or "", e.estimated_effort_days or "",
                e.capability_id or "", e.requirement_id or "", e.option_id or "",
                e.created_at.isoformat() if e.created_at else "",
            ])
        csv_content = output.getvalue()
        from flask import Response
        return Response(
            csv_content,
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=fit-gap-{solution_id}.csv",
                "Content-Type": "text/csv; charset=utf-8",
            },
        )
    except Exception as e:
        logger.error("export_fit_gap_csv failed solution=%s: %s", solution_id, e)
        return jsonify({"error": str(e)}), 500


@solution_design_bp.route("/<int:solution_id>/fit-gap/reorder", methods=["PATCH"])
@login_required
def reorder_fit_gap_entries(solution_id):
    """Update sort_order for a set of fit-gap entries in one transaction.

    Body: {"order": [id1, id2, id3, ...]} — sets sort_order = index position.
    """
    Solution.query.get_or_404(solution_id)
    data = request.get_json() or {}
    order = data.get("order", [])
    if not isinstance(order, list):
        return jsonify({"success": False, "error": "order must be a list of IDs"}), 400
    try:
        from app.models.solution_models import SolutionFitGapEntry
        for position, entry_id in enumerate(order):
            SolutionFitGapEntry.query.filter_by(
                id=entry_id, solution_id=solution_id
            ).update({"sort_order": position})
        db.session.commit()
        return jsonify({"success": True, "reordered": len(order)})
    except Exception as e:
        db.session.rollback()
        logger.error("reorder_fit_gap_entries failed solution=%s: %s", solution_id, e)
        return jsonify({"success": False, "error": str(e)}), 500


# =============================================================================
# INTEGRATION ARCHITECTURE GOVERNANCE (INTARCH-001 Track C)
# =============================================================================


@solution_design_bp.route("/<int:solution_id>/integration-architecture", methods=["GET"])
@login_required
def solution_integration_architecture(solution_id):
    """GET /solutions/<id>/integration-architecture — full section data with scores."""
    solution = Solution.query.get_or_404(solution_id)
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BlueprintCompletenessService,
    )
    from app.models.solution_sad_models import SolutionIntegrationFlow
    from app.models.integration_pattern import IntegrationPattern

    svc = BlueprintCompletenessService()
    score_data = svc.score_section(solution.id, "integration_architecture")

    flows = SolutionIntegrationFlow.query.filter_by(solution_id=solution_id).all()
    flows_data = []
    for f in flows:
        pattern = None
        pattern_id = f.pattern_id
        if pattern_id:
            try:
                p = IntegrationPattern.query.get(pattern_id)
                if p:
                    pattern = {
                        "id": p.id,
                        "name": p.name,
                        "approval_status": p.approval_status,
                        "middleware": p.middleware,
                    }
            except Exception as exc:
                logger.debug("suppressed error in solution_integration_architecture (app/modules/solutions_strategic/v2/routes/solution_link_routes.py): %s", exc)
        flows_data.append({
            "id": f.id,
            "flow_name": f.flow_name,
            "source_app_id": f.source_app_id,
            "target_app_id": f.target_app_id,
            "protocol": f.protocol,
            "data_format": f.data_format,
            "criticality": f.criticality,
            "governance_status": f.governance_status or 'undocumented',
            "pattern": pattern,
        })

    return jsonify({
        "solution_id": solution_id,
        "score": score_data,
        "flows": flows_data,
        "total_flows": len(flows_data),
    })


@solution_design_bp.route(
    "/<int:solution_id>/integration-flows/<int:flow_id>/link-pattern", methods=["POST"]
)
@login_required
def solution_link_integration_pattern(solution_id, flow_id):
    """POST link an IntegrationPattern to an integration flow."""
    from app.models.solution_sad_models import SolutionIntegrationFlow
    from app.models.integration_pattern import IntegrationPattern

    flow = SolutionIntegrationFlow.query.filter_by(
        id=flow_id, solution_id=solution_id
    ).first_or_404()
    data = request.get_json() or {}
    pattern_id = data.get("pattern_id")
    if not pattern_id:
        return jsonify({"error": "pattern_id is required"}), 400

    pattern = IntegrationPattern.query.get_or_404(pattern_id)

    try:
        db.session.execute(
            db.text(
                "UPDATE solution_integration_flows "
                "SET pattern_id = :pid, governance_status = :gs "
                "WHERE id = :fid"
            ),
            {"pid": pattern.id, "gs": pattern.approval_status, "fid": flow.id},
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error("solution_link_integration_pattern failed flow=%s: %s", flow_id, exc)
        return jsonify({"error": f"Failed to link pattern: {exc}"}), 500

    return jsonify({
        "status": "ok",
        "flow_id": flow_id,
        "pattern_id": pattern.id,
        "pattern_name": pattern.name,
        "governance_status": pattern.approval_status,
    })


@solution_design_bp.route("/api/integration-patterns", methods=["GET"])
@login_required
def list_integration_patterns():
    """GET /solutions/api/integration-patterns — pattern library picker.

    Query params:
      q              full-text search on name
      vendor_key     filter by vendor_key (SAP_BTP, MICROSOFT, CROSS_VENDOR, GENERIC)
      approval_status filter by approval_status (approved, conditional, blocked)
      limit          max results (default 50, cap 100)
    """
    from app.models.integration_pattern import IntegrationPattern

    q = request.args.get("q", "").strip()
    vendor_key = request.args.get("vendor_key", "").strip()
    approval_status = request.args.get("approval_status", "").strip()
    limit = min(safe_int_arg('limit', 50, minimum=1, maximum=500), 100)

    try:
        query = IntegrationPattern.query
        if q:
            query = query.filter(IntegrationPattern.name.ilike(f"%{q}%"))
        if vendor_key:
            query = query.filter(IntegrationPattern.vendor_key == vendor_key)
        if approval_status:
            query = query.filter(IntegrationPattern.approval_status == approval_status)

        patterns = query.order_by(
            IntegrationPattern.approval_status.asc(),
            IntegrationPattern.name.asc(),
        ).limit(limit).all()
    except Exception as exc:
        from app import db as _db
        _db.session.rollback()
        return jsonify({"error": "integration_patterns table not available", "detail": str(exc)}), 503

    return jsonify([
        {
            "id": p.id,
            "name": p.name,
            "vendor_key": p.vendor_key,
            "pattern_type": p.pattern_type,
            "middleware": p.middleware,
            "approval_status": p.approval_status,
            "protocol": p.protocol,
            "codegen_target": p.codegen_target,
            "description": p.description,
            "approval_notes": p.approval_notes,
        }
        for p in patterns
    ])
