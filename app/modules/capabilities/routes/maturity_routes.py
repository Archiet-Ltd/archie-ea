#!/usr/bin/env python3
"""
Migration: Copied from app/main/capability_maturity_routes.py -> app/modules/capabilities/routes/maturity_routes.py
Date: 2026-02-14 | Relative imports fixed for new location.

Business Capability Maturity Management Routes

Provides user-friendly routes for managing business capability maturity levels
without requiring admin privileges.
"""

from datetime import datetime

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.decorators import audit_log
from sqlalchemy import text

from app import db
from app.utils.framework_classifier import FrameworkClassifier
from app.utils.pagination import safe_int_arg
from app.utils.tenant_sql import current_org_id

# Create blueprint
maturity_management = Blueprint("maturity_management", __name__)


# maturity_dashboard removed — empty shell page, frozen sidebar link
# (template capability_maturity/dashboard.html deleted)


@maturity_management.route("/capability-maturity/search")
@login_required
def search_capabilities():
    """Search and filter capabilities"""

    query = request.args.get("q", "")
    domain = request.args.get("domain", "")
    strategic_importance = request.args.get("strategic_importance", "")
    page = safe_int_arg('page', 1, minimum=1)
    per_page = 20

    try:
        # ORM, not raw SQL, and deliberately so. The raw version selected a
        # column that does not exist (capability_type), which 500'd into the
        # error banner below on every visit; and it carried a "# tenant-filtered"
        # comment on statements that were not filtered at all -- raw SQL never
        # reaches do_orm_execute, so the search read across every organisation.
        # BusinessCapability is a TenantMixin model, so the org predicate is
        # applied mechanically and the column list is checked by the mapper.
        from app.models.business_capabilities import BusinessCapability

        search = BusinessCapability.query
        if query:
            like = "%" + query + "%"
            search = search.filter(
                db.or_(
                    BusinessCapability.name.ilike(like),
                    BusinessCapability.description.ilike(like),
                )
            )
        if domain:
            search = search.filter(BusinessCapability.business_domain == domain)
        if strategic_importance:
            search = search.filter(
                BusinessCapability.strategic_importance == strategic_importance
            )

        # count() before the window, so the total describes the whole result set
        # rather than the page. The previous str.replace() approach stopped
        # matching when the SELECT list changed, and nothing noticed.
        total_count = search.count()

        capabilities = (
            search.order_by(
                BusinessCapability.business_domain, BusinessCapability.name
            )
            .limit(per_page)
            .offset((page - 1) * per_page)
            .all()
        )

        domains = [
            value
            for (value,) in db.session.query(BusinessCapability.business_domain)
            .filter(BusinessCapability.business_domain.isnot(None))
            .distinct()
            .all()
        ]

        return render_template(
            "capability_maturity/search.html",
            capabilities=capabilities,
            domains=domains,
            total_count=total_count,
            page=page,
            per_page=per_page,
            query=query,
            selected_domain=domain,
            selected_importance=strategic_importance,
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error searching capabilities")
        flash("Error searching capabilities. Please try again.", "error")
        return render_template(
            "capability_maturity/search.html",
            capabilities=[],
            domains=[],
            total_count=None,
            load_error="The capability search could not be run.",
        )


@maturity_management.route("/capability-maturity/edit/<int:capability_id>", methods=["GET", "POST"])
@login_required
@audit_log("edit_capability_maturity")
def edit_capability_maturity(capability_id):
    """Edit maturity levels for a specific capability"""

    try:
        if request.method == "POST":
            # Get form data
            current_level = request.form.get("current_maturity_level")
            target_level = request.form.get("target_maturity_level")
            notes = request.form.get("assessment_notes", "")

            # Validate input
            if current_level:
                try:
                    current_level = int(current_level)
                    if current_level < 1 or current_level > 5:
                        flash("Current maturity level must be between 1 and 5", "error")
                        return redirect(
                            url_for(
                                "maturity_management.edit_capability_maturity",
                                capability_id=capability_id,
                            )
                        )
                except ValueError:
                    flash("Invalid current maturity level", "error")
                    return redirect(
                        url_for(
                            "maturity_management.edit_capability_maturity",
                            capability_id=capability_id,
                        )
                    )

            if target_level:
                try:
                    target_level = int(target_level)
                    if target_level < 1 or target_level > 5:
                        flash("Target maturity level must be between 1 and 5", "error")
                        return redirect(
                            url_for(
                                "maturity_management.edit_capability_maturity",
                                capability_id=capability_id,
                            )
                        )
                except ValueError:
                    flash("Invalid target maturity level", "error")
                    return redirect(
                        url_for(
                            "maturity_management.edit_capability_maturity",
                            capability_id=capability_id,
                        )
                    )

            # Calculate gap
            gap = 0
            if current_level is not None and target_level is not None:
                gap = target_level - current_level

            # Update the capability
            update_query = """
                UPDATE business_capability
                SET current_maturity_level = :current_level,
                    target_maturity_level = :target_level,
                    maturity_gap = :gap,
                    maturity_assessment_date = :assessment_date,
                    maturity_assessment_notes = :notes,
                    updated_at = :updated_at
                WHERE id = :capability_id AND organization_id = :org_id
            """

            params = {
                "current_level": current_level,
                "target_level": target_level,
                "gap": gap,
                "assessment_date": datetime.utcnow(),
                "notes": notes,
                "updated_at": datetime.utcnow(),
                "capability_id": capability_id,
                "org_id": getattr(g, "current_org_id", None),
            }


            db.session.execute(text(update_query), params)  # tenant-filtered
            db.session.commit()

            flash("Capability maturity levels updated successfully!", "success")
            return redirect(url_for("maturity_management.search_capabilities"))

        # GET request - show edit form
        #
        # BusinessCapability is a TenantMixin model: the ORM listener applies the
        # organisation predicate automatically, so a capability belonging to
        # another organisation and one that does not exist are indistinguishable
        # here (the same lookup capability_line_of_sight uses).
        from app.models.capability_models import BusinessCapability

        if current_org_id() is None:
            flash("Capability not found", "error")
            return redirect(url_for("maturity_management.search_capabilities"))

        capability = BusinessCapability.query.filter_by(id=capability_id).first()

        if not capability:
            flash("Capability not found", "error")
            return redirect(url_for("maturity_management.search_capabilities"))

        return render_template("capability_maturity/edit.html", capability=capability)

    except Exception:
        flash("Error updating capability. Please try again.", "error")
        return redirect(url_for("maturity_management.search_capabilities"))


@maturity_management.route("/capability-maturity/batch-update", methods=["GET", "POST"])
@login_required
@audit_log("batch_update_maturity")
def batch_update_maturity():
    """Batch update maturity levels for multiple capabilities"""

    try:
        if request.method == "POST":
            # Get form data
            domain = request.form.get("domain", "")
            strategic_importance = request.form.get("strategic_importance", "")
            current_level = request.form.get("current_maturity_level")
            target_level = request.form.get("target_maturity_level")
            notes = request.form.get("assessment_notes", "")

            # Validate levels
            if current_level:
                try:
                    current_level = int(current_level)
                    if current_level < 1 or current_level > 5:
                        flash("Current maturity level must be between 1 and 5", "error")
                        return redirect(url_for("maturity_management.batch_update_maturity"))
                except ValueError:
                    flash("Invalid current maturity level", "error")
                    return redirect(url_for("maturity_management.batch_update_maturity"))

            if target_level:
                try:
                    target_level = int(target_level)
                    if target_level < 1 or target_level > 5:
                        flash("Target maturity level must be between 1 and 5", "error")
                        return redirect(url_for("maturity_management.batch_update_maturity"))
                except ValueError:
                    flash("Invalid target maturity level", "error")
                    return redirect(url_for("maturity_management.batch_update_maturity"))

            # Build update query
            update_query = """
                UPDATE business_capability
                SET current_maturity_level = :current_level,
                    target_maturity_level = :target_level,
                    maturity_assessment_date = :assessment_date,
                    maturity_assessment_notes = :notes,
                    updated_at = :updated_at
            """

            params = {
                "current_level": current_level,
                "target_level": target_level,
                "assessment_date": datetime.utcnow(),
                "notes": notes,
                "updated_at": datetime.utcnow(),
            }

            # Add WHERE conditions. CRITICAL: always scope to the caller's org
            # so a blank filter can never mass-update every tenant's capabilities.
            # Refuse the write outright if there is no tenant context (should not
            # happen on a @login_required route — but never run an unscoped UPDATE).
            _org = getattr(g, "current_org_id", None)
            if _org is None:
                flash("Your session has expired — please sign in again.", "error")
                return redirect(url_for("maturity_management.batch_update_maturity"))
            where_conditions = ["organization_id = :org_id"]
            params["org_id"] = _org

            # The form's domain selector says "Only update capabilities in this
            # domain". It was read from the form and then never used in the WHERE,
            # so a domain-scoped batch update silently rewrote every capability in
            # the organisation. The predicate is the contract the label states.
            if domain:
                where_conditions.append("business_domain = :domain")
                params["domain"] = domain

            if strategic_importance:
                where_conditions.append("strategic_importance = :strategic_importance")
                params["strategic_importance"] = strategic_importance

            update_query += " WHERE " + " AND ".join(where_conditions)

            # Calculate and update gaps
            if current_level is not None and target_level is not None:
                update_query = update_query.replace(
                    "SET current_maturity_level = :current_level,",
                    "SET current_maturity_level = :current_level, maturity_gap = (:target_level - :current_level),",
                )

            # Execute update
            result = db.session.execute(text(update_query), params)  # tenant-filtered
            db.session.commit()

            flash(f"Successfully updated {result.rowcount} capabilities!", "success")
            return redirect(url_for("maturity_management.frameworks_overview"))

        # GET request - show batch update form
        # Get available domains
        _batch_domain_q = "SELECT DISTINCT business_domain FROM business_capability WHERE business_domain IS NOT NULL"
        _batch_domain_p = {}

        # Preview what would be updated
        domain = request.args.get("domain", "")
        strategic_importance = request.args.get("strategic_importance", "")

        # business_capability is tenant-scoped, but raw SQL bypasses the ORM
        # listener: without these predicates the batch-update form previewed —
        # and offered for edit — every organisation's capabilities.
        from flask import g as _g
        _org = getattr(_g, "current_org_id", None)
        _org_and = " AND organization_id = :org" if _org is not None else ""

        preview_query = f"""
            SELECT id, name, business_domain, current_maturity_level, target_maturity_level, strategic_importance
            FROM business_capability
            WHERE 1=1{_org_and}
        """

        params = {"org": _org} if _org is not None else {}

        if domain:
            preview_query += " AND business_domain = :domain"
            params["domain"] = domain

        if strategic_importance:
            preview_query += " AND strategic_importance = :strategic_importance"
            params["strategic_importance"] = strategic_importance

        preview_query += " ORDER BY business_domain, name LIMIT 20"

        result = db.session.execute(text(preview_query), params)  # tenant-filtered
        preview_capabilities = result.fetchall()

        # `domains` populates the domain selector on the batch-update form. Its query
        # was missing, so this route raised NameError on every request.
        domains = [
            row[0]
            for row in db.session.execute(
                text(
                    "SELECT DISTINCT business_domain FROM business_capability "
                    f"WHERE business_domain IS NOT NULL{_org_and} ORDER BY business_domain"
                ),
                ({"org": _org} if _org is not None else {}),
            ).fetchall()
        ]

        return render_template(
            "capability_maturity/batch_update.html",
            domains=domains,
            preview_capabilities=preview_capabilities,
            selected_domain=domain,
            selected_importance=strategic_importance,
        )

    except Exception:
        flash("Error in batch update. Please try again.", "error")
        return redirect(url_for("maturity_management.frameworks_overview"))


@maturity_management.route("/capability-maturity/api/capability/<int:capability_id>")
@login_required
def get_capability_api(capability_id):
    """API endpoint to get capability details"""

    try:
        # Same ORM lookup as the GET branch above: another organisation's
        # capability is filtered out by the tenant predicate and reads as not
        # found, same as an id that does not exist.
        from app.models.capability_models import BusinessCapability

        if current_org_id() is None:
            return jsonify({"error": "Capability not found"}), 404

        capability = BusinessCapability.query.filter_by(id=capability_id).first()

        if not capability:
            return jsonify({"error": "Capability not found"}), 404

        return jsonify(
            {
                "id": capability.id,
                "name": capability.name,
                "business_domain": capability.business_domain,
                "description": capability.description,
                "current_maturity_level": capability.current_maturity_level,
                "target_maturity_level": capability.target_maturity_level,
                "maturity_gap": capability.maturity_gap,
                "strategic_importance": capability.strategic_importance,
                "business_owner": capability.business_owner,
                "maturity_assessment_notes": capability.maturity_assessment_notes,
                "maturity_assessment_date": capability.maturity_assessment_date.isoformat()
                if capability.maturity_assessment_date
                else None,
            }
        )

    except Exception:
        return jsonify({"error": "An internal error occurred"}), 500


# Framework-Specific Routes


@maturity_management.route("/capability-maturity/framework/<framework_key>")
@login_required
def framework_dashboard(framework_key):
    """Framework-specific dashboard"""

    try:
        # Get framework information
        framework_info = FrameworkClassifier.get_framework_summary(framework_key)

        if not framework_info:
            flash(f'Framework "{framework_key}" not found', "error")
            return redirect(url_for("maturity_management.frameworks_overview"))

        # Get framework categories
        framework_categories = FrameworkClassifier.get_framework_categories(framework_key)

        # Build WHERE clause for framework categories
        category_filter = " OR ".join([f"category = '{cat}'" for cat in framework_categories])

        # Tenant scoping. These are raw SQL statements, so the ORM tenant filter in
        # app/middleware/tenant_isolation.py does NOT apply to them (see ADR 0003) —
        # the predicate has to be added explicitly here.
        _org_id = getattr(g, "current_org_id", None)
        _fw_org_filter = " AND organization_id = :org_id" if _org_id else ""
        _fw_params = {"org_id": _org_id} if _org_id else {}

        # Get framework statistics. The columns below are exactly what
        # templates/capability_maturity/frameworks/*_dashboard.html reads from
        # `stats`; this query and its assignment had been removed, leaving `stats`
        # unbound and the route raising NameError on every request.
        _stats_row = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT COUNT(*) AS total_capabilities,
                   AVG(current_maturity_level) AS avg_current,
                   AVG(target_maturity_level)  AS avg_target,
                   COUNT(*) FILTER (
                       WHERE maturity_gap IS NOT NULL AND maturity_gap > 0
                   ) AS with_gap
            FROM business_capability
            WHERE ({category_filter}){_fw_org_filter}
        """
            ),
            _fw_params,
        ).mappings().first()
        stats = dict(_stats_row) if _stats_row else {}

        # Get domain distribution within framework.
        # This loop previously built domain_filter and _dom_params and then did
        # nothing with them: the execute and the domain_stats assignment were both
        # missing, so domain_stats stayed {} on every request. That failed silently —
        # the dashboard rendered with an empty domain breakdown rather than erroring.
        domain_stats = {}
        for domain_key, domain_data in framework_info["domains"].items():
            domain_categories = FrameworkClassifier.get_domain_categories(framework_key, domain_key)
            if domain_categories:
                domain_filter = " OR ".join([f"category = '{cat}'" for cat in domain_categories])
                _dom_params = dict(_fw_params)
                _dom_row = db.session.execute(  # tenant-filtered
                    text(
                        f"""
                    SELECT COUNT(*) AS total_capabilities,
                           AVG(current_maturity_level) AS avg_current,
                           AVG(target_maturity_level)  AS avg_target
                    FROM business_capability
                    WHERE ({domain_filter}){_fw_org_filter}
                """
                    ),
                    _dom_params,
                ).mappings().first()
                domain_stats[domain_key] = dict(_dom_row) if _dom_row else {}

        # Get capabilities needing attention in this framework
        result = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT id, name, business_domain, current_maturity_level, target_maturity_level,
                   maturity_gap, strategic_importance, maturity_assessment_date, category
            FROM business_capability
            WHERE ({category_filter}) AND
                  ((maturity_gap IS NOT NULL AND maturity_gap > 1) OR current_maturity_level IS NULL){_fw_org_filter}
            ORDER BY CASE WHEN maturity_gap IS NULL THEN 0 ELSE maturity_gap END DESC,
                     strategic_importance DESC
            LIMIT 20
        """
            ),
            _fw_params,
        )
        attention_needed = result.fetchall()

        return render_template(
            f"capability_maturity/frameworks/{framework_key}_dashboard.html",
            framework_info=framework_info,
            stats=stats,
            domain_stats=domain_stats,
            attention_needed=attention_needed,
        )

    except Exception:
        flash("Error loading framework dashboard. Please try again.", "error")
        return redirect(url_for("maturity_management.frameworks_overview"))


@maturity_management.route("/capability-maturity/framework/<framework_key>/<domain_key>")
@login_required
def domain_dashboard(framework_key, domain_key):
    """Domain-specific dashboard within a framework"""

    try:
        # Get framework and domain information
        framework_info = FrameworkClassifier.get_framework_summary(framework_key)

        if not framework_info or domain_key not in framework_info["domains"]:
            flash(f'Domain "{domain_key}" in framework "{framework_key}" not found', "error")
            return redirect(
                url_for("maturity_management.framework_dashboard", framework_key=framework_key)
            )

        domain_info = framework_info["domains"][domain_key]
        domain_categories = FrameworkClassifier.get_domain_categories(framework_key, domain_key)

        if not domain_categories:
            flash(f'No categories found for domain "{domain_key}"', "error")
            return redirect(
                url_for("maturity_management.framework_dashboard", framework_key=framework_key)
            )

        # Build WHERE clause for domain categories
        category_filter = " OR ".join([f"category = '{cat}'" for cat in domain_categories])

        # Tenant scoping — raw SQL bypasses the ORM tenant filter (ADR 0003).
        _org_id = getattr(g, "current_org_id", None)
        _dd_org_filter = " AND organization_id = :org_id" if _org_id else ""
        _dd_params = {"org_id": _org_id} if _org_id else {}

        # Get domain statistics. Same defect as framework_dashboard: the query and
        # its assignment were missing, so `stats` was unbound and this route raised
        # NameError on every request. Columns match what the dashboard template reads.
        _stats_row = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT COUNT(*) AS total_capabilities,
                   AVG(current_maturity_level) AS avg_current,
                   AVG(target_maturity_level)  AS avg_target,
                   COUNT(*) FILTER (
                       WHERE maturity_gap IS NOT NULL AND maturity_gap > 0
                   ) AS with_gap
            FROM business_capability
            WHERE ({category_filter}){_dd_org_filter}
        """
            ),
            _dd_params,
        ).mappings().first()
        stats = dict(_stats_row) if _stats_row else {}

        # Get all capabilities in this domain
        result = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT id, name, business_domain, current_maturity_level, target_maturity_level,
                   maturity_gap, strategic_importance, maturity_assessment_date,
                   category, business_owner, description
            FROM business_capability
            WHERE ({category_filter}){_dd_org_filter}
            ORDER BY strategic_importance DESC, name
        """
            ),
            _dd_params,
        )
        capabilities = result.fetchall()

        # Get capabilities needing attention
        result = db.session.execute(  # tenant-filtered
            text(
                f"""
            SELECT id, name, business_domain, current_maturity_level, target_maturity_level,
                   maturity_gap, strategic_importance, maturity_assessment_date, category
            FROM business_capability
            WHERE ({category_filter}) AND
                  ((maturity_gap IS NOT NULL AND maturity_gap > 1) OR current_maturity_level IS NULL){_dd_org_filter}
            ORDER BY CASE WHEN maturity_gap IS NULL THEN 0 ELSE maturity_gap END DESC,
                     strategic_importance DESC
        """
            ),
            _dd_params,
        )
        attention_needed = result.fetchall()

        return render_template(
            f"capability_maturity/frameworks/{framework_key}_{domain_key}_dashboard.html",
            framework_info=framework_info,
            domain_info=domain_info,
            stats=stats,
            capabilities=capabilities,
            attention_needed=attention_needed,
        )

    except Exception:
        flash("Error loading domain dashboard. Please try again.", "error")
        return redirect(
            url_for("maturity_management.framework_dashboard", framework_key=framework_key)
        )


@maturity_management.route("/capability-maturity/frameworks-overview")
@login_required
def frameworks_overview_redirect():
    """Redirect /capability-maturity/frameworks-overview to canonical URL."""
    return redirect(url_for("maturity_management.frameworks_overview"))


@maturity_management.route("/capability-maturity/frameworks")
@login_required
def frameworks_overview():
    """Overview of all frameworks"""

    try:
        # Get all frameworks
        all_frameworks = FrameworkClassifier.get_all_frameworks()

        # Get statistics for each framework
        framework_stats = {}

        # Tenant scoping: raw SQL, so the ORM tenant filter in
        # app/middleware/tenant_isolation.py does not apply (ADR 0003) — the
        # organization_id predicate has to be added by hand.
        _org_id = getattr(g, "current_org_id", None)
        _org_filter = " AND organization_id = :org_id" if _org_id else ""

        for framework_key, framework_data in all_frameworks.items():
            framework_categories = FrameworkClassifier.get_framework_categories(framework_key)

            if not framework_categories:
                continue

            # Placeholders, not interpolated values, so a framework category can
            # never carry SQL into the statement.
            placeholders = ", ".join(
                [f":cat_{i}" for i in range(len(framework_categories))]
            )
            params = {f"cat_{i}": cat for i, cat in enumerate(framework_categories)}
            if _org_id:
                params["org_id"] = _org_id

            # Column ORDER is load-bearing: frameworks_overview.html reads this
            # row positionally as stats[0], [1], [2], [4], [5].
            row = db.session.execute(  # tenant-filtered
                text(
                    f"""
                SELECT COUNT(*)                                              AS total,
                       COUNT(current_maturity_level)                         AS with_current,
                       COUNT(target_maturity_level)                          AS with_target,
                       COUNT(*) FILTER (
                           WHERE maturity_gap IS NOT NULL AND maturity_gap > 0
                       )                                                     AS with_gap,
                       AVG(current_maturity_level)                           AS avg_current,
                       AVG(target_maturity_level)                            AS avg_target
                FROM business_capability
                WHERE category IN ({placeholders}){_org_filter}
            """  # nosec B608 -- placeholder names are integer-generated; values are bound
                ),
                params,
            ).first()

            framework_stats[framework_key] = row


        return render_template(
            "capability_maturity/frameworks_overview.html",
            all_frameworks=all_frameworks,
            framework_stats=framework_stats,
        )

    except Exception:
        flash("Error loading frameworks overview. Please try again.", "error")
        return redirect(url_for("capability_map.index"))


# ── Capability Maturity Heatmap ───────────────────────────────────────────────

# The 1-5 scale, named. The template owns the colours (Tailwind only sees classes
# that appear in a template, and tailwind-output.css is committed pre-built), so
# this is labels only — never a default level.
MATURITY_SCALE = [
    (1, "Initial"),
    (2, "Developing"),
    (3, "Defined"),
    (4, "Managed"),
    (5, "Optimising"),
]


@maturity_management.route("/capability-maturity/heatmap")
@login_required
def maturity_heatmap():
    """Current-vs-target maturity heatmap, grouped by category.

    Uses the ORM, so the tenant filter in app/middleware/tenant_isolation.py
    applies automatically (ADR 0003) — no hand-written organization_id predicate
    is needed here, and adding one would double-filter.

    A capability counts as *assessed* only when ``maturity_assessment_date`` is
    set. Levels are nullable by design: an unassessed capability must render as
    "—", not as Level 1, or the page tells leadership the whole estate was
    assessed and found immature.
    """
    from app.models.capability_models import BusinessCapability

    try:
        rows = BusinessCapability.query.order_by(BusinessCapability.name).all()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Error loading capability maturity heatmap")
        return render_template(
            "capability_maturity/heatmap.html",
            groups=[],
            scale=MATURITY_SCALE,
            total_count=None,
            assessed_count=None,
            unassessed_count=None,
            avg_current=None,
            avg_target=None,
            load_error="The maturity heatmap could not be loaded.",
        )

    grouped = {}
    assessed_current = []
    assessed_target = []

    for cap in rows:
        assessed = cap.maturity_assessment_date is not None
        current = cap.current_maturity_level if assessed else None
        target = cap.target_maturity_level if assessed else None

        # Only trust a stored gap when both ends are real; otherwise recompute or
        # leave it unknown. Never substitute 0 for "not computed".
        if current is not None and target is not None:
            gap = target - current
        else:
            gap = None

        if current is not None:
            assessed_current.append(current)
        if target is not None:
            assessed_target.append(target)

        group = (cap.category or cap.business_domain or "Uncategorised").strip() or "Uncategorised"
        grouped.setdefault(group, []).append(
            {
                "id": cap.id,
                "name": cap.name,
                "assessed": assessed,
                "current": current,
                "target": target,
                "gap": gap,
                "assessed_on": cap.maturity_assessment_date,
                "strategic_importance": cap.strategic_importance,
            }
        )

    groups = [
        {"name": name, "capabilities": caps}
        for name, caps in sorted(grouped.items(), key=lambda kv: kv[0].lower())
    ]

    total_count = len(rows)
    assessed_count = sum(1 for c in rows if c.maturity_assessment_date is not None)

    return render_template(
        "capability_maturity/heatmap.html",
        groups=groups,
        scale=MATURITY_SCALE,
        total_count=total_count,
        assessed_count=assessed_count,
        unassessed_count=total_count - assessed_count,
        avg_current=(sum(assessed_current) / len(assessed_current)) if assessed_current else None,
        avg_target=(sum(assessed_target) / len(assessed_target)) if assessed_target else None,
        load_error=None,
    )



# -- Capability Line of Sight -------------------------------------------------
#
# The one screen that answers a leader's three questions about a single
# capability, in order: how good are we, who owns it, what are we doing about it.
#
# Everything below is read from what is already modelled. Nothing is inferred:
# where the estate has no answer, the view says so in a sentence rather than
# rendering a zero or a blank, because "0 initiatives" and "we never linked any"
# are different facts and a leadership review must be able to tell them apart.
#
# Linkage notes (what is genuinely modelled today):
#   * Ownership is BusinessCapability.business_owner / it_owner -- free-text
#     names on the capability itself. There is no capability-to-User FK.
#   * There is no WorkPackage -> BusinessCapability foreign key. Work packages
#     reach a capability two ways, both used here:
#       (a) an ArchiMateRelationship between the capability's element and the
#           work package's element -- the ArchiMate backbone, either direction;
#       (b) WorkPackage.capability_id -> UnifiedCapability sharing the same
#           archimate_element_id as this capability.
#   * Goals and Drivers are likewise reached through the ArchiMate relationship
#     graph. Neither model carries organization_id, so the traversal is scoped
#     by starting from ArchiMateElement/ArchiMateRelationship, which are
#     TenantMixin models and therefore filtered by do_orm_execute.

_LOS_LIMIT = 25


def _los_related_element_ids(element_id):
    """Element ids one ArchiMate relationship away from *element_id*, either way.

    ArchiMateRelationship is tenant-scoped, so this traversal cannot cross an
    organization boundary.
    """
    from app.models.models import ArchiMateRelationship

    if not element_id:
        return []

    rels = ArchiMateRelationship.query.filter(
        db.or_(
            ArchiMateRelationship.source_id == element_id,
            ArchiMateRelationship.target_id == element_id,
        )
    ).all()

    ids = set()
    for rel in rels:
        if rel.source_id and rel.source_id != element_id:
            ids.add(rel.source_id)
        if rel.target_id and rel.target_id != element_id:
            ids.add(rel.target_id)
    return sorted(ids)


@maturity_management.route("/capability-maturity/<int:capability_id>/line-of-sight")
@login_required
def capability_line_of_sight(capability_id):
    """Line of sight for one capability: maturity, ownership, and what is being done.

    Tenant scoping is the ORM's (ADR 0003): every model read here is either a
    TenantMixin model -- BusinessCapability, WorkPackage, ArchiMateRelationship,
    ApplicationComponent, SavedDiagram -- or is reached only through one. The
    single exception is ApplicationCapabilityMapping, which carries
    organization_id but does NOT inherit TenantMixin, so it is filtered by hand.
    """
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import SavedDiagram, SavedDiagramElement
    from app.models.capability_models import BusinessCapability
    from app.models.implementation_migration import WorkPackage
    from app.models.motivation import Driver, Goal
    from app.models.unified_capability import UnifiedCapability

    capability = BusinessCapability.query.filter_by(id=capability_id).first()
    if capability is None:
        # A capability belonging to another organization is filtered out by the
        # tenant predicate and is indistinguishable from one that never existed --
        # which is the correct answer to give either way.
        flash("That capability could not be found.", "error")
        return redirect(url_for("maturity_management.maturity_heatmap"))

    # -- 1. How good are we? ----------------------------------------------
    # maturity_assessment_date is the ONLY proof an assessment happened.
    assessed = capability.maturity_assessment_date is not None
    current = capability.current_maturity_level if assessed else None
    target = capability.target_maturity_level if assessed else None
    gap = (target - current) if (current is not None and target is not None) else None

    maturity = {
        "assessed": assessed,
        "current": current,
        "target": target,
        "gap": gap,
        "assessed_on": capability.maturity_assessment_date,
        "notes": (capability.maturity_assessment_notes or "").strip() or None,
        "current_label": dict(MATURITY_SCALE).get(current),
        "target_label": dict(MATURITY_SCALE).get(target),
    }

    # -- 2. Who owns it? --------------------------------------------------
    ownership = {
        "business_owner": (capability.business_owner or "").strip() or None,
        "it_owner": (capability.it_owner or "").strip() or None,
        "governance_model": (capability.governance_model or "").strip() or None,
    }
    ownership["has_owner"] = bool(ownership["business_owner"] or ownership["it_owner"])

    element_id = capability.archimate_element_id
    related_ids = _los_related_element_ids(element_id)

    # -- 3. What are we doing about it? -----------------------------------
    work_packages = []
    seen_wp = set()

    def _add_work_packages(rows):
        for wp in rows:
            if wp.id in seen_wp:
                continue
            seen_wp.add(wp.id)
            work_packages.append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "status": (wp.status or "").replace("_", " ").strip() or None,
                    "priority": (wp.priority or "").strip() or None,
                    "start_date": wp.start_date,
                    "target_date": wp.target_date,
                    # 0% complete on a planned package is a real measurement;
                    # a NULL is "nobody has reported progress". Keep them apart.
                    "percent_complete": wp.percent_complete,
                }
            )

    if related_ids:
        _add_work_packages(
            WorkPackage.query.filter(WorkPackage.archimate_element_id.in_(related_ids))
            .order_by(WorkPackage.name)
            .limit(_LOS_LIMIT)
            .all()
        )

    if element_id:
        unified_ids = [
            row.id
            for row in UnifiedCapability.query.filter_by(
                archimate_element_id=element_id
            ).all()
        ]
        if unified_ids:
            _add_work_packages(
                WorkPackage.query.filter(WorkPackage.capability_id.in_(unified_ids))
                .order_by(WorkPackage.name)
                .limit(_LOS_LIMIT)
                .all()
            )

    # -- Supporting evidence: applications --------------------------------
    # ApplicationCapabilityMapping carries organization_id but is NOT a
    # TenantMixin model, so do_orm_execute does not filter it. The predicate
    # below is the isolation for this query.
    applications = []
    org_id = getattr(g, "current_org_id", None)
    mapping_q = ApplicationCapabilityMapping.query.filter(
        ApplicationCapabilityMapping.business_capability_id == capability.id
    )
    if org_id is not None:
        mapping_q = mapping_q.filter(
            ApplicationCapabilityMapping.organization_id == org_id
        )
    mappings = mapping_q.limit(_LOS_LIMIT).all()
    if mappings:
        # ApplicationComponent IS tenant-scoped, so this second query is
        # filtered by the ORM and cannot resurrect a foreign application.
        components = {
            comp.id: comp
            for comp in ApplicationComponent.query.filter(
                ApplicationComponent.id.in_([m.application_component_id for m in mappings])
            ).all()
        }
        for mapping in mappings:
            comp = components.get(mapping.application_component_id)
            if comp is None:
                continue
            applications.append(
                {
                    "id": comp.id,
                    "name": comp.name,
                    "lifecycle_status": (comp.lifecycle_status or "").strip() or None,
                    "criticality": (comp.business_criticality or "").strip() or None,
                    "support_level": (mapping.support_level or "").strip() or None,
                    "coverage": mapping.coverage_percentage,
                }
            )
        applications.sort(key=lambda a: (a["name"] or "").lower())

    # -- Supporting evidence: strategy this capability serves --------------
    goals = []
    drivers = []
    if related_ids:
        goals = [
            {"id": row.id, "name": row.name}
            for row in Goal.query.filter(Goal.archimate_element_id.in_(related_ids))
            .order_by(Goal.name)
            .limit(_LOS_LIMIT)
            .all()
        ]
        drivers = [
            {"id": row.id, "name": row.name}
            for row in Driver.query.filter(Driver.archimate_element_id.in_(related_ids))
            .order_by(Driver.name)
            .limit(_LOS_LIMIT)
            .all()
        ]

    # -- Supporting evidence: diagrams it appears on -----------------------
    diagrams = []
    if element_id:
        diagram_ids = [
            row.diagram_id
            for row in SavedDiagramElement.query.filter_by(element_id=element_id).all()
        ]
        if diagram_ids:
            diagrams = [
                {"id": row.id, "name": row.name, "viewpoint": row.viewpoint_type}
                for row in SavedDiagram.query.filter(SavedDiagram.id.in_(diagram_ids))
                .order_by(SavedDiagram.name)
                .limit(_LOS_LIMIT)
                .all()
            ]

    return render_template(
        "capability_maturity/line_of_sight.html",
        capability=capability,
        maturity=maturity,
        ownership=ownership,
        work_packages=work_packages,
        applications=applications,
        goals=goals,
        drivers=drivers,
        diagrams=diagrams,
        scale=MATURITY_SCALE,
        is_modelled=element_id is not None,
    )


# ── Capability Maturity CSV Import ────────────────────────────────────────────

@maturity_management.route("/capability-maturity/import-csv", methods=["POST"])
@login_required
def import_maturity_csv():
    """Batch-update capability maturity levels from a CSV upload.

    CSV columns (header required):
        capability_name   — matched case-insensitively against business_capability.name
        current_maturity  — integer 1-5
        target_maturity   — integer 1-5 (optional)
        notes             — free text (optional)

    Returns JSON: {success, updated, skipped, errors:[{row, reason}]}
    """
    import csv
    import io
    from app.models.capability_models import BusinessCapability

    f = request.files.get("file")
    if not f:
        return jsonify({"success": False, "error": "No file provided"}), 400

    if not f.filename.lower().endswith(".csv"):
        return jsonify({"success": False, "error": "Only .csv files are accepted"}), 400

    if f.content_length and f.content_length > 2 * 1024 * 1024:
        return jsonify({"success": False, "error": "File exceeds 2 MB limit"}), 413

    try:
        content = f.read().decode("utf-8-sig", errors="replace")
    except Exception:
        return jsonify({"success": False, "error": "Could not read file"}), 400

    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return jsonify({"success": False, "error": "CSV has no header row"}), 400

    # Normalise header names
    headers_lower = [h.strip().lower() for h in reader.fieldnames]
    if "capability_name" not in headers_lower:
        return jsonify({"success": False, "error": "CSV must have a 'capability_name' column"}), 400

    # Build name→capability lookup (case-insensitive)
    caps = BusinessCapability.query.all()
    cap_map = {c.name.strip().lower(): c for c in caps if c.name}

    updated = 0
    skipped = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):
        row_norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        name_key = row_norm.get("capability_name", "").lower()

        if not name_key:
            skipped += 1
            continue

        cap = cap_map.get(name_key)
        if not cap:
            errors.append({"row": row_num, "reason": f"Capability not found: {row_norm.get('capability_name', '')}"})
            skipped += 1
            continue

        current_raw = row_norm.get("current_maturity", "") or row_norm.get("current_level", "")
        target_raw = row_norm.get("target_maturity", "") or row_norm.get("target_level", "")
        notes = row_norm.get("notes", "") or row_norm.get("assessment_notes", "")

        changed = False

        if current_raw:
            try:
                lvl = int(current_raw)
                if not (1 <= lvl <= 5):
                    raise ValueError()
                cap.current_maturity_level = lvl
                changed = True
            except ValueError:
                errors.append({"row": row_num, "reason": f"Invalid current_maturity '{current_raw}' — must be 1-5"})
                skipped += 1
                continue

        if target_raw:
            try:
                lvl = int(target_raw)
                if not (1 <= lvl <= 5):
                    raise ValueError()
                cap.target_maturity_level = lvl
                changed = True
            except ValueError:
                errors.append({"row": row_num, "reason": f"Invalid target_maturity '{target_raw}' — must be 1-5"})
                skipped += 1
                continue

        if notes and hasattr(cap, "maturity_assessment_notes"):
            cap.maturity_assessment_notes = notes
            changed = True

        # Recalculate gap
        if cap.current_maturity_level and cap.target_maturity_level:
            cap.maturity_gap = cap.target_maturity_level - cap.current_maturity_level

        if changed:
            cap.updated_at = datetime.utcnow()
            updated += 1
        else:
            skipped += 1

        if row_num > 501:
            errors.append({"row": row_num, "reason": "Row limit (500) reached — remaining rows skipped"})
            break

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": f"Database error: {str(e)}"}), 500

    return jsonify({
        "success": True,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "message": f"{updated} capabilities updated, {skipped} skipped" + (f", {len(errors)} errors" if errors else ""),
    })
