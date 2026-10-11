"""
Dashboard Views v2 — guardrail-enabled.

Uses the new architecture:
- @timed_route for automatic metrics collection on all endpoints
- Observability (request_id in response headers)

URL prefix preserved: /dashboard (applied via register() in v2/__init__.py)
Blueprint name: dashboard (same as v1 — no cross-module url_for refs found)

All 17 routes preserved exactly from v1 dashboard_views.py.
The dashboard_api blueprint (9 routes) is nested inside this blueprint.
URL prefix /dashboard applied via register() in v2/__init__.py — route decorators must not repeat it.
"""

import logging

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import db
from app.core.compat import mark_blueprint_guardrailed
from app.core.decorators import timed_route
from app.decorators import audit_log

logger = logging.getLogger(__name__)

dashboard_bp_v2 = Blueprint("dashboard", __name__, template_folder="templates")
mark_blueprint_guardrailed(dashboard_bp_v2)


@dashboard_bp_v2.route("/")
@timed_route
@login_required
def index():
    """Dashboard index - redirects to overview landing page."""
    return redirect(url_for("dashboard.overview"))


@dashboard_bp_v2.route("/overview")
@timed_route
@login_required
def overview():
    from app.config.navigation_registry import get_navigation_sections
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.modules.dashboard.v2.services.executive_dashboard_service import (
        format_health_score,
    )

    metrics = {
        "applications": 0,
        "vendors": 0,
        "users": 0,
        "active_sessions": 0,
    }
    try:
        metrics["applications"] = (
            db.session.query(db.func.count(ApplicationComponent.id)).scalar() or 0
        )
        metrics["vendors"] = (
            db.session.query(db.func.count(VendorOrganization.id)).scalar() or 0
        )
        metrics["users"] = (
            db.session.query(db.func.count(User.id))
            .filter(User.organization_id == g.current_org_id)
            .scalar()
            or 0
        )

        active_sessions_query = db.session.query(db.func.count(User.id)).filter(
            User.organization_id == g.current_org_id
        )
        if hasattr(User, "confirmed"):
            active_sessions_query = active_sessions_query.filter(User.confirmed.is_(True))
        metrics["active_sessions"] = active_sessions_query.scalar() or 0
    except Exception as exc:
        logger.warning("dashboard overview core metrics unavailable: %s", exc)
        db.session.rollback()

    # Navigation hub stats
    nav_stats = {
        "elements": 0,
        "relationships": 0,
        "consolidation": 0,
        "solutions": 0,
        "capabilities": 0,
    }
    try:
        from app.models.archimate_core import ArchiMateElement

        nav_stats["elements"] = (
            db.session.query(db.func.count(ArchiMateElement.id)).scalar() or 0
        )
    except Exception:
        db.session.rollback()

    try:
        from app.models.archimate_core import ArchiMateRelationship

        nav_stats["relationships"] = (
            db.session.query(db.func.count(ArchiMateRelationship.id)).scalar() or 0
        )
    except Exception:
        db.session.rollback()

    try:
        from app.models.consolidation_list import ConsolidationListEntry

        nav_stats["consolidation"] = (
            db.session.query(db.func.count(ConsolidationListEntry.id)).scalar() or 0
        )
    except Exception:
        db.session.rollback()

    try:
        from app.models.solution_models import Solution

        nav_stats["solutions"] = db.session.query(db.func.count(Solution.id)).scalar() or 0
    except Exception:
        db.session.rollback()

    try:
        nav_stats["capabilities"] = (
            db.session.query(db.func.count(BusinessCapability.id)).scalar() or 0
        )
    except Exception:
        db.session.rollback()

    # Per-ArchiMate-3.2-layer element counts, driving the dashboard's by-layer
    # architecture lens (replacing the ambiguous "Architect"/"Business" persona
    # tabs). Six canonical layers — ArchiMate 3.2 folds Physical elements
    # (Equipment/Facility/Material) into Technology, so there is no separate
    # Physical tab. Grouped count so it is one query, not O(N).
    #
    # LAYER_TYPES / LAYER_TYPE_TO_LAYER are the single system of record for
    # this mapping (ADR 0008) -- imported from archimate_viewpoint_service,
    # which the composer's `layer=` filter also reads, so this card's count
    # and the composer's element set can never drift apart. Do not
    # reintroduce a local copy here.
    from app.services.archimate_viewpoint_service import LAYER_TYPES as _LAYER_TYPES
    from app.services.archimate_viewpoint_service import LAYER_TYPE_TO_LAYER as _type_to_layer
    layer_breakdown = {layer: 0 for layer in _LAYER_TYPES}
    try:
        from app.models.archimate_core import ArchiMateElement

        rows = (
            db.session.query(ArchiMateElement.type, db.func.count(ArchiMateElement.id))
            .group_by(ArchiMateElement.type)
            .all()
        )
        for elem_type, count in rows:
            layer = _type_to_layer.get((elem_type or "").lower())
            if layer:
                layer_breakdown[layer] += count or 0
    except Exception:
        db.session.rollback()

    applications = []
    vendors = []
    try:
        applications = (
            ApplicationComponent.query.order_by(ApplicationComponent.name).limit(10).all()
        )
    except Exception as exc:
        logger.warning("dashboard overview applications unavailable: %s", exc)
        db.session.rollback()

    try:
        vendors = (
            VendorOrganization.query.order_by(VendorOrganization.name).limit(10).all()
        )
    except Exception as exc:
        logger.warning("dashboard overview vendors unavailable: %s", exc)
        db.session.rollback()

    feature_sections = []
    try:
        feature_sections = [
            {
                **section,
                "items": [
                    item
                    for item in section.get("items", [])
                    if not item.get("disabled") and item.get("url") and item.get("url") != "#"
                ],
            }
            for section in get_navigation_sections(
                current_endpoint=request.endpoint,
                view_args=request.view_args,
                applications=applications,
                vendors=vendors,
            )
        ]
        feature_sections = [section for section in feature_sections if section["items"]]
    except Exception as exc:
        logger.warning("dashboard overview feature directory unavailable: %s", exc)

    # Persona-specific metrics (ENT-017)
    try:
        from app.models.solution_models import Solution as SolutionModel
        HAS_SOLUTION_MODELS = True
    except ImportError:
        HAS_SOLUTION_MODELS = False

    persona_metrics = {}
    if HAS_SOLUTION_MODELS:
        try:
            # Exclude automated test artifacts and blank drafts from health metrics.
            # These inflate solution count (suppressing gov_health) and have zero or
            # fabricated maturity from test runs. Only real enterprise solutions count.
            # Pattern rationale: all patterns are CLEARLY test/debug session artifacts
            # based on naming conventions used by automated journey tests and developers.
            from sqlalchemy import not_, or_
            _test_name_patterns = [
                "J1-AutoTest-%", "New Solution%", "J1 Bootstrap%",
                "J1 Test%", "J1 Regression%",
                "J1-Debug%", "J1-Test-%",
                "%E2E Test%", "%Journey Test%", "AI Test%",
                "Minimal Test%", "QA Test%", "Driver Test%",
                "Blueprint Test%", "%JDD Test%", "%Gap2 Persistence%",
                "Post-Deploy%", "%Smoke Test%", "% Test Solution%",
                "% Test Solution", "%Audit Test%", "%Forensic Audit%",
                "%Verification Test%", "%Uniformity Verification%",
                "% Test Programme%", "%PESTLE News Analyser%",
                "MDM Test%", "Create an architecture for%",
            ]
            # D5 (round-4 refuter finding): also exclude soft-deleted rows
            # ("[DELETED] ..." name prefix), matching the grain the Health
            # Scorecard tile (dashboard_views.py total_solutions below,
            # fixed round-2/R2-5) and the Solutions list's org_total (S-01)
            # both already use. This was the third surface answering "how
            # many solutions" with its own, uniquely-inflated count.
            solutions = SolutionModel.query.filter(
                SolutionModel.name.isnot(None),
                not_(SolutionModel.name.like("[DELETED]%")),
                not_(or_(*[SolutionModel.name.like(p) for p in _test_name_patterns])),
            ).all()

            # CTO metrics: portfolio risk and ARB pipeline
            # Scope risks to the filtered solution set — test artifacts have dozens of
            # fabricated risks that pollute the portfolio risk metric.
            risk_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            _sol_ids_with_risks: set = set()
            try:
                # Was querying SolutionRisk, a different, essentially unused
                # table from the `risks` model the Risk Register and
                # /api/risks actually read - every open, critical
                # solution-linked risk in the real register showed here as
                # zero. Risk.impact is an int 1-5, not a stored severity
                # string; risk_level derives the same bucket the register
                # itself renders.
                from app.models.risk import Risk, RiskStatus
                _sol_ids = [s.id for s in solutions]
                for r in Risk.query.filter(
                    Risk.solution_id.in_(_sol_ids), Risk.status == RiskStatus.OPEN
                ).all():
                    risk_counts[r.risk_level] = risk_counts.get(r.risk_level, 0) + 1
                    _sol_ids_with_risks.add(r.solution_id)
            except Exception as exc:
                logger.warning("ENT-017: Risk unavailable: %s", exc)
                # Query failed: report unknown (em dash), not a fabricated zero-risk portfolio.
                risk_counts = {"critical": None, "high": None, "medium": None, "low": None}
            arb_pipeline = {"pending": 0, "approved": 0, "rejected": 0}
            try:
                from app.models.architecture_review_board import ARBReviewItem
                for item in ARBReviewItem.query.all():
                    st = (item.status or "").lower()
                    if st in ("pending", "submitted", "in_review", "draft"):
                        arb_pipeline["pending"] += 1
                    elif st in ("approved", "approved_with_conditions"):
                        arb_pipeline["approved"] += 1
                    elif st in ("rejected", "deferred"):
                        arb_pipeline["rejected"] += 1
            except Exception as exc:
                logger.warning("ENT-017: ARBReviewItem unavailable: %s", exc)
                # Query failed: report unknown (em dash), not a fabricated empty pipeline.
                arb_pipeline = {"pending": None, "approved": None, "rejected": None}
            persona_metrics["cto"] = {
                "risk_counts": risk_counts,
                "arb_pipeline": arb_pipeline,
                "total_solutions": len(solutions),
                "sol_ids_with_risks": _sol_ids_with_risks,
            }

            # CFO metrics: TCO aggregation by category
            tco_by_category = {}
            try:
                from app.models.solution_lifecycle_models import SolutionTCOItem
                for item in SolutionTCOItem.query.all():
                    cat = item.cost_category or "other"
                    tco_by_category[cat] = tco_by_category.get(cat, 0) + float(item.amount or 0)
            except Exception as exc:
                logger.warning("ENT-017: SolutionTCOItem unavailable: %s", exc)
            persona_metrics["cfo"] = {
                "tco_by_category": tco_by_category,
                "total_tco": sum(tco_by_category.values()),
            }

            # Architect metrics: ArchiMate catalog size and solution maturity
            # Use COUNT(*) instead of fetching all rows — consistent with
            # archimate_crud.api_layer_count and avoids O(N) memory on large catalogs.
            archimate_counts = {}
            total_archimate = 0
            try:
                from app.models.archimate_core import ArchiMateElement
                total_archimate = ArchiMateElement.query.count()
                for (elem_type,) in ArchiMateElement.query.with_entities(ArchiMateElement.type).all():
                    t = elem_type or "unknown"
                    archimate_counts[t] = archimate_counts.get(t, 0) + 1
            except Exception as exc:
                logger.warning("ENT-017: ArchiMateElement unavailable: %s", exc)
            # Maturity: prefer maturity_current (lifecycle-data score synced by CLI),
            # fall back to section_scores avg, then ADM phase percentage.
            # maturity_current is populated by `flask solutions sync-maturity --apply`.
            # ADM phase -> % complete: A=12, B=25, C=37, D=50, E=62, F=75, G=87, H=100
            _adm_phase_pct = {"A": 12, "B": 25, "C": 37, "D": 50, "E": 62, "F": 75, "G": 87, "H": 100}
            maturity_scores = []
            for s in solutions:
                mc = getattr(s, "maturity_current", None)
                if mc is not None and int(mc) > 0:
                    maturity_scores.append(int(mc))
                    continue
                sec = getattr(s, "section_scores", None)
                if sec and isinstance(sec, dict):
                    overalls = [v.get("overall", 0) for v in sec.values() if isinstance(v, dict)]
                    if overalls and any(v > 0 for v in overalls):
                        maturity_scores.append(round(sum(overalls) / len(overalls)))
                        continue
                phase = (getattr(s, "adm_phase", None) or "A").upper()
                maturity_scores.append(_adm_phase_pct.get(phase, 12))
            avg_maturity = round(sum(maturity_scores) / len(maturity_scores)) if maturity_scores else 0
            persona_metrics["architect"] = {
                "archimate_counts": archimate_counts,
                "avg_maturity": avg_maturity,
                "total_archimate": total_archimate,
            }
        except Exception as exc:
            logger.error("ENT-017: persona_metrics computation failed: %s", exc)

    # Business Architect metrics: capability count, maturity, gaps, business-layer
    # elements. Every sub-query is independently fault-tolerant (try/except -> 0)
    # so a missing table/column never 500s the dashboard (see
    # docs/known-issues/schema-drift-on-existing-databases.md).
    _ba_total_capabilities = 0
    _ba_avg_maturity = 0
    _ba_gap_count = 0
    _ba_business_element_count = 0
    try:
        from app.models.business_capabilities import BusinessCapability as _BACap
        _ba_total_capabilities = db.session.query(db.func.count(_BACap.id)).scalar() or 0
    except Exception as exc:
        logger.warning("ENT-017: business capability count unavailable: %s", exc)
        db.session.rollback()
    try:
        from app.models.business_capabilities import BusinessCapability as _BACap
        _levels = [
            lvl for (lvl,) in db.session.query(_BACap.current_maturity_level).all()
            if lvl is not None
        ]
        if _levels:
            _ba_avg_maturity = round(sum(_levels) / len(_levels) / 5 * 100)
    except Exception as exc:
        logger.warning("ENT-017: business capability maturity unavailable: %s", exc)
        db.session.rollback()
    try:
        from app.models.business_capabilities import BusinessCapability as _BACap
        _ba_gap_count = (
            db.session.query(db.func.count(_BACap.id))
            .filter(_BACap.maturity_gap.isnot(None), _BACap.maturity_gap > 0)
            .scalar()
        ) or 0
    except Exception as exc:
        logger.warning("ENT-017: business capability gap count unavailable: %s", exc)
        db.session.rollback()
    try:
        from app.models.archimate_core import ArchiMateElement as _BAElement
        _ba_business_element_count = (
            db.session.query(db.func.count(_BAElement.id))
            .filter(_BAElement.layer == "business")
            .scalar()
        ) or 0
    except Exception as exc:
        logger.warning("ENT-017: business-layer element count unavailable: %s", exc)
        db.session.rollback()
    persona_metrics["business"] = {
        "total_capabilities": _ba_total_capabilities,
        "avg_maturity": _ba_avg_maturity,
        "gap_count": _ba_gap_count,
        "business_element_count": _ba_business_element_count,
    }

    # CAP-017: Capability health heat map data
    capability_health = []
    try:
        from app.models.application_capability import ApplicationCapabilityMapping

        l1_caps = (
            BusinessCapability.query
            .filter(BusinessCapability.level == 1)
            .order_by(BusinessCapability.name)
            .all()
        )
        # Pre-compute all descendant capability IDs for each L1 cap via a recursive walk.
        # This ensures apps mapped to L2/L3 sub-capabilities roll up to the L1 coverage metric.
        from app.models.business_capability import BusinessCapability as _BC
        _all_caps = {c.id: c for c in _BC.query.all()}
        _children: dict = {}
        for cid, c in _all_caps.items():
            pid = getattr(c, "parent_capability_id", None)
            if pid:
                _children.setdefault(pid, []).append(cid)

        def _subtree_ids(root_id: int) -> list:
            ids, stack = [], [root_id]
            while stack:
                cur = stack.pop()
                ids.append(cur)
                stack.extend(_children.get(cur, []))
            return ids

        for cap in l1_caps:
            subtree = _subtree_ids(cap.id)
            # ApplicationCapabilityMapping is plain db.Model, not TenantMixin, so
            # do_orm_execute's tenant filter never applies to it directly -- but
            # its own organization_id column is NULL on every row in production
            # (added nullable by reconcile-schema, never backfilled), so a
            # predicate on THAT column would always report a gap. `subtree` is
            # already scoped: it is built from l1_caps / _BC.query.all(), both
            # TenantMixin BusinessCapability reads auto-filtered to this org, so
            # business_capability_id.in_(subtree) alone scopes this count via the
            # FK parent. See e622d36 / rationalization_scoring_service.py.
            app_count = (
                # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability, ACM.organization_id is NULL in prod (see e622d36).
                db.session.query(db.func.count(ApplicationCapabilityMapping.id))
                .filter(
                    ApplicationCapabilityMapping.business_capability_id.in_(subtree),
                )
                .scalar()
            ) or 0
            if app_count >= 2:
                coverage_status = "covered"
            elif app_count == 1:
                coverage_status = "partial"
            else:
                coverage_status = "gap"
            capability_health.append({
                "id": cap.id,
                "name": cap.name,
                "app_count": app_count,
                "coverage_status": coverage_status,
            })
    except Exception as exc:
        logger.warning("CAP-017: capability health unavailable: %s", exc)
        db.session.rollback()

    # ENH-002: Lifecycle distribution (Abacus codes from real data)
    lifecycle_distribution = []
    try:
        lifecycle_rows = (
            db.session.query(
                ApplicationComponent.lifecycle_status,
                db.func.count(ApplicationComponent.id),
            )
            .group_by(ApplicationComponent.lifecycle_status)
            .order_by(db.func.count(ApplicationComponent.id).desc())
            .all()
        )
        total_apps = metrics["applications"] or 1
        for status, count in lifecycle_rows:
            lifecycle_distribution.append({
                "status": status or "Unknown",
                "count": count,
                "pct": round(count / total_apps * 100),
            })
    except Exception as exc:
        logger.warning("ENH-002: lifecycle distribution unavailable: %s", exc)
        db.session.rollback()

    # ENH-002: Solution pipeline by ADM phase
    solution_pipeline = []
    try:
        from app.models.solution_models import Solution as SolModel
        phase_rows = (
            db.session.query(
                SolModel.adm_phase,
                db.func.count(SolModel.id),
            )
            .group_by(SolModel.adm_phase)
            .all()
        )
        _phase_labels = {
            "A": "A: Vision", "B": "B: Business", "C": "C: IS Arch",
            "D": "D: Technology", "E": "E: Options", "F": "F: Migration",
            "G": "G: Governance", "H": "H: Change",
        }
        phase_map = {}
        for phase, count in phase_rows:
            normalized_phase = (phase or "").strip().upper()
            phase_map[normalized_phase] = phase_map.get(normalized_phase, 0) + count
        for letter in "ABCDEFGH":
            solution_pipeline.append({
                "phase": letter,
                "label": _phase_labels.get(letter, letter),
                "count": phase_map.get(letter, 0),
            })
        # Legacy rows may have no phase. Keep them in the measured total without
        # inventing phase A, or an existing portfolio reads as "No solutions".
        unclassified = sum(count for phase, count in phase_map.items() if phase not in _phase_labels)
        if unclassified:
            solution_pipeline.append({"phase": None, "label": "Unclassified", "count": unclassified})
    except Exception as exc:
        solution_pipeline = None
        logger.warning("ENH-002: solution pipeline unavailable: %s", exc)
        db.session.rollback()

    # ENH-002: Architecture Health Score (weighted composite)
    # Phase Maturity 40% + Risk 30% + Capability Coverage 20% + Governance 10%
    # None, not 0: a health score of 0 and "we could not compute a health score"
    # are different facts, and 0 is the more alarming of the two to a reader who
    # cannot tell them apart.
    health_score = None
    health_components = {}
    try:
        # The executive-summary API uses this same tenant-scoped computation.
        # Do not derive an identically labelled score from different measures
        # (maturity_current, risk-documentation coverage, or ARB/solution counts).
        from app.modules.dashboard.v2.services.executive_dashboard_service import ExecutiveDashboardService
        health = ExecutiveDashboardService()._get_health_score()
        components = health["components"]
        health_score = health["composite_score"]
        health_components = {
            "phase_maturity": components["phase_maturity"],
            "risk_health": components["risk_posture"],
            "capability_coverage": components["capability_coverage"],
            "governance": components["governance"],
        }
    except Exception as exc:
        logger.warning("ENH-002: health score computation failed: %s", exc)
        # Leave health_score None. A failed computation must not surface as a
        # score of 0, which reads as a measured verdict on the estate.
        health_score = None
        health_components = {}

    # Data Coverage strip — real per-field coverage of the application portfolio.
    # Single aggregate query; percentages match DATA_REALITY.md definitions.
    data_coverage = {}
    try:
        _nonempty = lambda col: db.func.sum(  # noqa: E731
            db.case((db.and_(col.isnot(None), col != ""), 1), else_=0)
        )
        cov_row = db.session.query(
            db.func.count(ApplicationComponent.id),
            _nonempty(ApplicationComponent.business_owner),
            db.func.sum(
                db.case(
                    (
                        db.or_(
                            ApplicationComponent.vendor_product_id.isnot(None),
                            db.and_(
                                ApplicationComponent.vendor_name.isnot(None),
                                ApplicationComponent.vendor_name != "",
                            ),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            db.func.sum(
                db.case(
                    (
                        db.or_(
                            ApplicationComponent.license_cost.isnot(None),
                            ApplicationComponent.total_cost_of_ownership.isnot(None),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            _nonempty(ApplicationComponent.technical_risk),
            _nonempty(ApplicationComponent.business_criticality),
        ).one()
        cov_total = cov_row[0] or 1
        data_coverage = {
            "owner": round((cov_row[1] or 0) / cov_total * 100),
            "vendor": round((cov_row[2] or 0) / cov_total * 100),
            "cost": round((cov_row[3] or 0) / cov_total * 100),
            "risk": round((cov_row[4] or 0) / cov_total * 100),
            "criticality": round((cov_row[5] or 0) / cov_total * 100),
        }
    except Exception as exc:
        logger.warning("data coverage strip unavailable: %s", exc)
        db.session.rollback()
        data_coverage = {"query_error": str(exc)}

    # PLT-040: Role-based workspace cards
    enterprise_role = getattr(current_user, "enterprise_role", "platform_admin")

    # Dashboard mode (shell-wave-1 Task 5): guided setup for a sparse org vs. the
    # data-led view once there is something to show. Reuses counts already fetched
    # above — no new queries. capability_mapping_count sums the per-L1-capability
    # ApplicationCapabilityMapping counts collected while building capability_health.
    capability_mapping_count = sum(c.get("app_count", 0) for c in capability_health)
    applications_count = metrics.get("applications") or 0
    dashboard_mode = (
        "guided" if applications_count < 5 and capability_mapping_count == 0 else "data"
    )

    guided_steps = []
    if dashboard_mode == "guided":
        guided_steps = [
            {
                "title": "Import applications",
                "description": f"Bring your application portfolio into {current_app.config['APP_NAME']}.",
                "href": "/applications/",
                "done": applications_count > 0,
            },
            {
                "title": "Map capabilities",
                "description": "Connect applications to the capabilities they support.",
                "href": "/capability-map/",
                "done": capability_mapping_count > 0,
            },
        ]
        try:
            _can_invite = bool(current_user.is_admin())
        except Exception:
            _can_invite = False
        if _can_invite:
            # metrics["users"] is a GLOBAL count across every tenant (User is not
            # TenantMixin, so nothing auto-scopes it) -- using it here would show
            # "Done" for a 1-user org purely because some other org has users.
            # Scope explicitly to the current org, same as any other cross-tenant
            # aggregate outside an auto-filtered query.
            try:
                org_user_count = User.query.filter_by(
                    organization_id=current_user.organization_id
                ).count()
            except Exception:
                org_user_count = 0
            guided_steps.append(
                {
                    "title": "Invite your team",
                    "description": f"Bring in the colleagues who'll use {current_app.config['APP_NAME']} with you.",
                    "href": "/admin/users",
                    "done": org_user_count > 1,
                }
            )

    # PROG-019: AI-governance alerts — cheap read of the latest EA briefing only
    # (never re-runs the expensive stewardship review on a dashboard load).
    governance_alerts = None
    try:
        from app.models.strategic import EnterpriseBriefing
        b = (
            EnterpriseBriefing.query
            .order_by(EnterpriseBriefing.generated_at.desc(), EnterpriseBriefing.id.desc())
            .first()
        )
        if b is not None:
            governance_alerts = {
                "briefing_flagged": b.flagged_count or 0,
                "briefing_findings": b.finding_count or 0,
                "generated_at": b.generated_at.isoformat() if b.generated_at else None,
                "headline": b.headline,
            }
    except Exception as _ga_exc:  # noqa: BLE001 — alerts are advisory
        logger.debug("governance alerts unavailable: %s", _ga_exc)

    return render_template(
        "dashboards/overview.html",
        metrics=metrics,
        nav_stats=nav_stats,
        layer_breakdown=layer_breakdown,
        feature_sections=feature_sections,
        persona_metrics=persona_metrics,
        capability_health=capability_health,
        lifecycle_distribution=lifecycle_distribution,
        solution_pipeline=solution_pipeline,
        health_score=health_score,
        health_score_text=format_health_score(health_score),
        health_components=health_components,
        enterprise_role=enterprise_role,
        data_coverage=data_coverage,
        governance_alerts=governance_alerts,
        dashboard_mode=dashboard_mode,
        guided_steps=guided_steps,
        capability_mapping_count=capability_mapping_count,
    )


@dashboard_bp_v2.route("/api/overview/chart")
@timed_route
@login_required
def api_overview_chart():
    return jsonify(
        {
            "success": False,
            "labels": [],
            "datasets": [],
            "message": "No live overview chart data is configured.",
        }
    )


@dashboard_bp_v2.route("/api/onboarding-complete", methods=["POST"])
@timed_route
@login_required
def api_onboarding_complete():
    """PLT-040: Mark user onboarding as complete, optionally update enterprise_role."""
    import datetime

    data = request.get_json(silent=True) or {}
    new_role = data.get("enterprise_role")
    valid_roles = {
        "solution_architect", "enterprise_architect",
        "arb_member", "portfolio_manager", "platform_admin",
        "cto", "application_manager", "procurement",
    }
    if new_role and new_role in valid_roles:
        current_user.enterprise_role = new_role
    current_user.onboarding_completed_at = datetime.datetime.utcnow()
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error("PLT-040: onboarding-complete failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True})


@dashboard_bp_v2.route("/api/welcome-dismiss", methods=["POST"])
@timed_route
@login_required
def api_welcome_dismiss():
    """Dismiss the one-line "Welcome to Entelim" banner, once per user,
    forever (shell-wave-1 Task 5). Deliberately separate from
    /api/onboarding-complete, which also rewrites enterprise_role -- this
    endpoint has exactly one side effect: setting the dismiss timestamp."""
    import datetime

    if current_user.welcome_banner_dismissed_at is None:
        current_user.welcome_banner_dismissed_at = datetime.datetime.utcnow()
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("welcome-dismiss failed: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True})


@dashboard_bp_v2.route("/api/overview/table")
@timed_route
@login_required
def api_overview_table():
    return jsonify({"rows": []})


@dashboard_bp_v2.route("/api/operations/chart")
@timed_route
@login_required
def api_operations_chart():
    return jsonify(
        {
            "success": False,
            "labels": [],
            "datasets": [],
            "message": "No live operations chart data is configured.",
        }
    )


@dashboard_bp_v2.route("/api/operations/table")
@timed_route
@login_required
def api_operations_table():
    return jsonify({"rows": []})


def _pref_key(pref_name: str) -> str:
    """Return a per-user session key for a dashboard preference."""
    uid = getattr(current_user, "id", "anon")
    return f"dashboard_pref_{uid}_{pref_name}"


@dashboard_bp_v2.route("/api/colvis", methods=["GET", "POST"])
@timed_route
@login_required
@audit_log("update_column_visibility")
def api_colvis():
    """Save/get column visibility preferences (session-backed)."""
    key = _pref_key("colvis")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("colvis", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "colvis": session.get(key, {})})


@dashboard_bp_v2.route("/api/colorder", methods=["GET", "POST"])
@timed_route
@login_required
@audit_log("update_column_order")
def api_colorder():
    """Save/get column order preferences (session-backed)."""
    key = _pref_key("colorder")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("order", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "order": session.get(key, [])})


@dashboard_bp_v2.route("/api/sort", methods=["GET", "POST"])
@timed_route
@login_required
@audit_log("update_sort_preference")
def api_sort():
    """Save/get sort preferences (session-backed)."""
    key = _pref_key("sort")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("sort", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "sort": session.get(key)})


@dashboard_bp_v2.route("/api/edit", methods=["POST"])
@timed_route
@login_required
@audit_log("dashboard_edit")
def api_edit():
    """Cell edit — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Inline cell edit is not yet wired to a data model. Use the dedicated edit form.",
    }), 501


@dashboard_bp_v2.route("/api/filters", methods=["GET", "POST"])
@timed_route
@login_required
@audit_log("update_filters")
def api_filters():
    """Save/get filter preferences (session-backed)."""
    key = _pref_key("filters")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("filters", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "filters": session.get(key, {})})


@dashboard_bp_v2.route("/api/tab", methods=["POST", "GET"])
@timed_route
@login_required
@audit_log("update_tab")
def api_tab():
    """Save/load tab preferences (session-backed)."""
    key = _pref_key("tab")
    if request.method == "POST":
        data = request.get_json() or {}
        session[key] = data.get("tab", data)
        session.modified = True
        return jsonify({"success": True})
    return jsonify({"success": True, "tab": session.get(key, "outline")})


@dashboard_bp_v2.route("/api/duplicate", methods=["POST"])
@timed_route
@login_required
@audit_log("dashboard_duplicate")
def api_duplicate():
    """Row duplication — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Row duplication is not yet wired to a data model. Use the dedicated create form.",
    }), 501


@dashboard_bp_v2.route("/api/delete", methods=["POST"])
@timed_route
@login_required
@audit_log("dashboard_delete")
def api_delete():
    """Row deletion — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Row deletion is not yet wired to a data model. Use the dedicated delete action.",
    }), 501


@dashboard_bp_v2.route("/api/bulk-delete", methods=["POST"])
@timed_route
@login_required
@audit_log("dashboard_bulk_delete")
def api_bulk_delete():
    """Bulk row deletion — requires model-specific wiring; not yet implemented."""
    return jsonify({
        "success": False,
        "error": "Bulk delete is not yet wired to a data model.",
    }), 501


@dashboard_bp_v2.route("/api/table/<table_name>", methods=["GET"])
@timed_route
@login_required
def api_table_get(table_name):
    """Get table data (generic scaffold — no model wired)."""
    return jsonify({"success": True, "edits": {}, "rows": []})


def _assemble_health_scorecard_metrics():
    """Assemble the Architecture Health Scorecard's real metrics: solution
    risk summary, ARB pipeline, ArchiMate coverage by layer, and ADM phase
    distribution. Factored out of health_scorecard() so the AI executive
    briefing endpoint can be handed the exact same, already-computed
    metrics dict rather than re-querying (or worse, inventing numbers)."""
    # 1. Solution risk summary by impact level
    # Was querying SolutionRisk, a different, essentially unused table from
    # the `risks` model the Risk Register and /api/risks actually read - a
    # 6 Sep 2026 QA pass caught this labeled "Live from the solution risk
    # register" while showing zero against a register with real open,
    # critical, solution-linked risks. Risk.impact is an int 1-5, not a
    # stored severity string; risk_level derives the same bucket the
    # register itself renders.
    risk_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    try:
        from app.models.risk import Risk, RiskStatus
        for r in Risk.query.filter(Risk.status == RiskStatus.OPEN).all():
            risk_counts[r.risk_level] = risk_counts.get(r.risk_level, 0) + 1
    except Exception as exc:
        logger.warning("health_scorecard: Risk unavailable: %s", exc)
        # Query failed: report unknown (em dash), not a fabricated zero-risk summary.
        risk_counts = {"critical": None, "high": None, "medium": None, "low": None}

    # 2. ARB pipeline from ARBReviewItem
    arb_pipeline = {"pending": 0, "approved": 0, "rejected": 0, "under_review": 0, "deferred": 0}
    try:
        from app.models.architecture_review_board import ARBReviewItem
        for (status,) in ARBReviewItem.query.with_entities(ARBReviewItem.status).all():
            s = (status or "pending").lower()
            if s in ("draft", "submitted"):
                arb_pipeline["pending"] += 1
            elif s == "under_review":
                arb_pipeline["under_review"] += 1
            elif s == "approved":
                arb_pipeline["approved"] += 1
            elif s == "rejected":
                arb_pipeline["rejected"] += 1
            else:
                arb_pipeline["pending"] += 1
        for (decision,) in ARBReviewItem.query.with_entities(ARBReviewItem.decision).filter(
            ARBReviewItem.decision.isnot(None)
        ).all():
            if decision and decision.lower() == "deferred":
                arb_pipeline["deferred"] += 1
    except Exception as exc:
        logger.warning("health_scorecard: ARBReviewItem unavailable: %s", exc)

    # 3. ArchiMate element count grouped by layer
    # LAYER_TYPES / LAYER_TYPE_TO_LAYER are the single system of record for
    # this mapping (ADR 0008) -- imported from archimate_viewpoint_service,
    # same as the by-layer card query above. Do not reintroduce a local copy.
    from app.services.archimate_viewpoint_service import LAYER_TYPES as _scorecard_layer_types
    from app.services.archimate_viewpoint_service import (
        LAYER_TYPE_TO_LAYER as _scorecard_type_to_layer,
    )
    archimate_by_layer = {layer: 0 for layer in _scorecard_layer_types}
    archimate_by_layer["other"] = 0
    total_archimate = 0
    try:
        from app.models.archimate_core import ArchiMateElement
        for (elem_type,) in ArchiMateElement.query.with_entities(ArchiMateElement.type).all():
            total_archimate += 1
            t = (elem_type or "").lower()
            layer = _scorecard_type_to_layer.get(t, "other")
            archimate_by_layer[layer] = archimate_by_layer.get(layer, 0) + 1
    except Exception as exc:
        logger.warning("health_scorecard: ArchiMateElement unavailable: %s", exc)

    # 4. ADM phase distribution and average maturity
    # Read through the same phase summary as the Health Score's phase maturity,
    # so the two maturity figures count the same solutions.
    from app.modules.dashboard.v2.services.solution_phase_measures import (
        average_phase_progress,
        recorded_phase_summary,
    )

    adm_distribution = {p: 0 for p in "ABCDEFGH"}
    avg_maturity = None
    total_solutions = None
    try:
        phase_summary = recorded_phase_summary()
        total_solutions = phase_summary["total"]
        adm_distribution.update(phase_summary["counts"])
        if phase_summary["unclassified"]:
            adm_distribution["Unclassified"] = phase_summary["unclassified"]
        avg_maturity = average_phase_progress(phase_summary)
    except Exception as exc:
        adm_distribution = None
        total_solutions = None
        avg_maturity = None
        logger.warning("health_scorecard: Solution unavailable: %s", exc)
        db.session.rollback()

    return {
        "risk_counts": risk_counts,
        "arb_pipeline": arb_pipeline,
        "archimate_by_layer": archimate_by_layer,
        "total_archimate": total_archimate,
        "adm_distribution": adm_distribution,
        "avg_maturity": avg_maturity,
        "total_solutions": total_solutions,
    }


@dashboard_bp_v2.route("/health")
@timed_route
@login_required
def health_scorecard():
    """Architecture Health Scorecard — real metrics from SolutionRisk, ARBReviewItem,
    ArchiMateElement and Solution ADM phase distribution."""
    from app.modules.dashboard.v2.services.executive_dashboard_service import (
        ExecutiveDashboardService,
        format_health_score,
        health_score_basis,
    )

    metrics = _assemble_health_scorecard_metrics()
    health_score = None
    unavailable = None
    try:
        health = ExecutiveDashboardService()._get_health_score()
        health_score = health["composite_score"]
        unavailable = health["unavailable_components"]
    except Exception as exc:
        logger.warning("health_scorecard: health score unavailable: %s", exc)
        db.session.rollback()
    return render_template(
        "dashboards/health.html",
        health_score_text=format_health_score(health_score),
        health_basis=health_score_basis(health_score, unavailable),
        **metrics,
    )


@dashboard_bp_v2.route("/api/ai-executive-briefing", methods=["POST"])
@timed_route
@login_required
def ai_executive_briefing():
    """POST /dashboard/api/ai-executive-briefing

    Generates an AI executive briefing for the CTO from the Architecture
    Health Scorecard's own metrics: a headline, what changed, risks, and
    recommended focus areas. Advisory only — nothing here is persisted.
    """
    from app.services.feature_flag_service import FeatureFlagService

    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS,
        endpoint_name="dashboard.ai_executive_briefing",
    )
    if feature_guard:
        return feature_guard

    from app.modules.dashboard.v2.services.executive_briefing_service import (
        ExecutiveBriefingAIError,
        generate_executive_briefing,
    )

    metrics = _assemble_health_scorecard_metrics()

    try:
        briefing = generate_executive_briefing(metrics)
    except ExecutiveBriefingAIError as e:
        logger.warning("Executive briefing unparseable: %s", e)
        return jsonify({"error": f"AI executive briefing failed: {e}"}), 502
    except Exception as e:
        logger.exception("Executive briefing generation failed")
        return jsonify({"error": f"AI executive briefing failed: {e}"}), 502

    return jsonify({"briefing": briefing})
