"""Executive dashboard service — aggregates existing analytics for CTO/CIO view.

Queries real data from Solution, SolutionRisk, ARBReviewItem,
BusinessCapability, SolutionCapabilityMapping, ApplicationComponent,
VendorOrganization, and ArchiMateElement models.
"""

import logging

from app import db

logger = logging.getLogger(__name__)

# What a screen shows for a measure that has no value.
MISSING_VALUE = "—"


def format_health_score(score):
    """The one display format for the composite health score.

    One decimal place exactly as measured (``66.7``, ``100.0``), or an em dash
    when the score could not be computed. Every screen that shows the score
    renders it through this function, so two screens cannot spell one value two
    ways.
    """
    if score is None:
        return MISSING_VALUE
    return f"{float(score):.1f}"


# The four components of the health score, in the order the Overview shows them,
# with the plain-words name each carries in a sentence.
HEALTH_COMPONENT_NAMES = (
    ("phase_maturity", "phase maturity"),
    ("risk_posture", "risk posture"),
    ("capability_coverage", "capability coverage"),
    ("governance", "governance"),
)


def _join_names(names):
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0]


def health_score_basis(score, unavailable):
    """What the health score was weighted from, and what it could not use.

    ``unavailable`` is the list of components the score could not measure, as
    ``_get_health_score`` returns it (``None`` when the score could not be
    computed at all, in which case nothing is claimed either way). The score is
    re-weighted over the components that were measured, so the components named
    as its basis are exactly those not in ``unavailable``, and only when there is
    a score.

    Returns ``{"weighted_from": "phase maturity", "not_measured": "risk posture,
    capability coverage and governance"}``; either value is ``None`` when it does
    not apply.
    """
    if unavailable is None:
        return {"weighted_from": None, "not_measured": None}
    measured = [name for key, name in HEALTH_COMPONENT_NAMES if key not in unavailable]
    missing = [name for key, name in HEALTH_COMPONENT_NAMES if key in unavailable]
    return {
        "weighted_from": _join_names(measured) if score is not None and measured else None,
        "not_measured": _join_names(missing) if missing else None,
    }


class ExecutiveDashboardService:
    """Aggregates cross-domain metrics into a single executive summary."""

    def get_executive_summary(self):
        """Return all executive KPIs in a single dict."""
        return {
            "architecture_health": self._get_health_score(),
            "programme_progress": self._get_phase_distribution(),
            "risk_posture": self._get_risk_summary(),
            "pending_decisions": self._get_arb_pending(),
            "capability_coverage": self._get_capability_coverage(),
            "portfolio_stats": self._get_portfolio_stats(),
        }

    # ------------------------------------------------------------------
    # Private metric methods
    # ------------------------------------------------------------------

    def _get_phase_distribution(self):
        """Count solutions per TOGAF ADM phase (A-H)."""
        try:
            from app.models.solution_models import Solution

            rows = (
                db.session.query(
                    Solution.adm_phase, db.func.count(Solution.id)
                )
                .group_by(Solution.adm_phase)
                .all()
            )
            distribution = {}
            total = 0
            for phase, count in rows:
                label = phase or "Unknown"
                distribution[label] = count
                total += count
            return {"distribution": distribution, "total": total}
        except Exception as exc:
            logger.warning("Executive dashboard: phase distribution unavailable: %s", exc)
            return {"distribution": {}, "total": None}  # honest: totals not computed on error

    def _get_risk_summary(self):
        """Aggregate open risks by impact severity.

        Was querying SolutionRisk (app.models.solution_lifecycle_models) -
        a different, essentially unused table from `risks` (app.models.risk.
        Risk), the model that actually backs the Risk Register UI and
        /api/risks. Every executive surface fed by this method reported 0
        open risks while the register itself showed real, open, critical
        risks: a QA acceptance pass on 6 Sep 2026 caught the Overview tile,
        CTO tab, Health Scorecard and Solutions "Needs Attention" panel all
        reading zero against a register with 5 open (4 critical). Risk.impact
        is an int 1-5 (likelihood x impact), not a stored severity string, so
        the severity bucket comes from the same `risk_level` property the
        register itself renders - this is now reading the one system of
        record with the one severity computation, not a second copy of both.
        """
        try:
            from app.models.risk import Risk, RiskStatus

            open_risks = Risk.query.filter(Risk.status == RiskStatus.OPEN).all()
            counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            for risk in open_risks:
                counts[risk.risk_level] = counts.get(risk.risk_level, 0) + 1
            return {"counts": counts, "total": len(open_risks)}
        except Exception as exc:
            logger.warning("Executive dashboard: risk summary unavailable: %s", exc)
            return {"counts": {"critical": 0, "high": 0, "medium": 0, "low": 0}, "total": 0}

    def _get_arb_pending(self):
        """Count ARB review items by status."""
        try:
            from app.models.architecture_review_board import ARBReviewItem

            rows = (
                db.session.query(
                    ARBReviewItem.status, db.func.count(ARBReviewItem.id)
                )
                .group_by(ARBReviewItem.status)
                .all()
            )
            by_status = {}
            for status, count in rows:
                by_status[status or "unknown"] = count

            pending = by_status.get("draft", 0) + by_status.get("submitted", 0) + by_status.get("pending", 0)
            return {
                "pending": pending,
                "approved": by_status.get("approved", 0),
                "rejected": by_status.get("rejected", 0),
                "total": sum(by_status.values()),
            }
        except Exception as exc:
            logger.warning("Executive dashboard: ARB pending unavailable: %s", exc)
            return {"pending": None, "approved": None, "rejected": None, "total": None}  # honest: ARB counts not computed on error

    def _get_capability_coverage(self):
        """Percentage of L1 business capabilities with at least one mapped
        application.

        4 Sep 2026: this used to independently count capabilities with a
        SolutionCapabilityMapping row (a much sparser relationship than
        applications), disagreeing live with the CTO-tab hero panel's
        identically-labelled, identically-weighted "Capability Coverage"
        component (100% vs 0% observed on the same page). Now calls the same
        shared computation the hero uses — see capability_coverage.py.
        """
        try:
            from app.modules.dashboard.v2.services.capability_coverage import (
                compute_l1_capability_coverage,
            )

            return compute_l1_capability_coverage()
        except Exception as exc:
            # Counts are None, not 0: the query failed, so nothing is known about
            # the capability set. Returning zeros would render as a measured
            # "0 capabilities, 0% covered".
            logger.warning("Executive dashboard: capability coverage unavailable: %s", exc)
            return {"total": None, "covered": None, "percentage": None}

    def _get_portfolio_stats(self):
        """Counts for solutions, applications, vendors, ArchiMate elements."""
        stats = {"solutions": 0, "applications": 0, "vendors": 0, "archimate_elements": 0}
        try:
            from app.models.solution_models import Solution

            stats["solutions"] = db.session.query(db.func.count(Solution.id)).scalar() or 0
        except Exception as exc:
            logger.warning("Executive dashboard: solution count unavailable: %s", exc)

        try:
            from app.models.application_portfolio import ApplicationComponent

            stats["applications"] = db.session.query(db.func.count(ApplicationComponent.id)).scalar() or 0
        except Exception as exc:
            logger.warning("Executive dashboard: application count unavailable: %s", exc)

        try:
            from app.models.vendor.vendor_organization import VendorOrganization

            stats["vendors"] = db.session.query(db.func.count(VendorOrganization.id)).scalar() or 0
        except Exception as exc:
            logger.warning("Executive dashboard: vendor count unavailable: %s", exc)

        try:
            from app.models.archimate_core import ArchiMateElement

            stats["archimate_elements"] = db.session.query(db.func.count(ArchiMateElement.id)).scalar() or 0
        except Exception as exc:
            logger.warning("Executive dashboard: ArchiMate count unavailable: %s", exc)

        return stats

    def _get_health_score(self):
        """Compute a composite architecture health score (0-100).

        Weighted average of:
        - Phase maturity (40%): share of solutions with a recorded phase
          that are in phase C or later
        - Risk posture (30%): inverse of high/critical risk ratio
        - Capability coverage (20%): % L1 capabilities with application mapping
        - Governance (10%): ARB presence, timeliness and approval rate

        A component is ``None`` when it could not be measured — the query failed,
        or there is nothing to measure yet. It is deliberately not zero, and for
        risk posture emphatically not 100: this method used to answer a database
        failure with ``risk_posture = 100.0``, reporting a *perfect* enterprise
        risk posture at exactly the moment it knew least. Per CLAUDE.md a value
        the system does not have must reach the UI as ``None`` (rendered as an em
        dash), because a plausible number is indistinguishable from a measured
        one and the reader acts on it.

        The composite is re-weighted over whichever components are available, and
        is itself ``None`` when none of them are.
        """
        scores = {}

        # Phase maturity: share of solutions in phase C or later.
        # Only recorded, valid phases supply a denominator. NULL/invalid phase
        # does not mean Phase A, nor a measured failure to progress past Phase B.
        # The Health Scorecard's average solution maturity reads the same
        # solutions through the same phase summary.
        try:
            from app.modules.dashboard.v2.services.solution_phase_measures import (
                recorded_phase_summary,
                share_in_advanced_phases,
            )

            scores["phase_maturity"] = share_in_advanced_phases(recorded_phase_summary())
        except Exception:
            logger.exception("health score: phase maturity could not be measured")
            scores["phase_maturity"] = None

        # Risk posture: fewer high/critical is better
        try:
            # Same system-of-record correction as _get_risk_summary below:
            # SolutionRisk is a different, essentially unused table from the
            # one the Risk Register and /api/risks actually read.
            from app.models.risk import Risk, RiskStatus

            open_risks = Risk.query.filter(Risk.status == RiskStatus.OPEN).all()
            total_risks = len(open_risks)
            if total_risks > 0:
                severe = sum(1 for r in open_risks if r.risk_level in ("critical", "high"))
                scores["risk_posture"] = round((1 - severe / total_risks) * 100, 1)
            else:
                # No open risks recorded is not the same as a clean risk posture.
                # On a new or unpopulated tenant it means nobody has captured any
                # risk yet, and scoring that 100 tells a CTO the opposite.
                scores["risk_posture"] = None
        except Exception:
            logger.exception("health score: risk posture could not be measured")
            scores["risk_posture"] = None

        # Capability coverage
        cap = self._get_capability_coverage()
        scores["capability_coverage"] = cap["percentage"]

        # Governance: ARB throughput score
        #
        # Old formula: resolved/total punished platforms that actively used
        # the ARB — a queue of pending items (which is healthy) scored 0%.
        #
        # New formula measures three things:
        #   - Presence: ARB process is being used at all (50 pts baseline if any item exists)
        #   - Timeliness: resolved items as % of items older than 30 days (pending > 30d = overdue)
        #   - Approval rate: approved / resolved (healthy governance approves most things)
        #
        # An organisation with an active, up-to-date ARB queue scores near 100.
        # An organisation with stale unresolved items scores lower.
        # No ARB activity leaves no denominator to measure governance against.
        try:
            from datetime import datetime, timedelta
            from app.models.architecture_review_board import ARBReviewItem

            total_arb = db.session.query(db.func.count(ARBReviewItem.id)).scalar() or 0
            if total_arb == 0:
                scores["governance"] = None
            else:
                resolved = (
                    db.session.query(db.func.count(ARBReviewItem.id))
                    .filter(ARBReviewItem.status.in_(["approved", "rejected"]))
                    .scalar()
                ) or 0

                cutoff = datetime.utcnow() - timedelta(days=30)
                overdue = (
                    db.session.query(db.func.count(ARBReviewItem.id))
                    .filter(
                        ARBReviewItem.status.notin_(["approved", "rejected"]),
                        ARBReviewItem.created_at < cutoff,
                    )
                    .scalar()
                ) or 0
                pending = total_arb - resolved

                # Timeliness: what fraction of pending items are NOT overdue?
                timeliness = (1 - overdue / pending) if pending > 0 else 1.0

                # Approval rate among resolved (low rejection rate is normal/healthy)
                approved = (
                    db.session.query(db.func.count(ARBReviewItem.id))
                    .filter(ARBReviewItem.status == "approved")
                    .scalar()
                ) or 0
                approval_rate = (approved / resolved) if resolved > 0 else 0.5

                # Composite: presence (fixed 40) + timeliness (40) + approval_rate (20)
                gov_score = 40 + round(timeliness * 40) + round(approval_rate * 20)
                scores["governance"] = min(100.0, float(gov_score))
        except Exception:
            logger.exception("health score: governance could not be measured")
            scores["governance"] = None

        # Weighted composite over the components that could actually be measured,
        # with the weights renormalised across them. Averaging a missing component
        # in as zero would drag the headline score down for a reason the reader
        # cannot see; treating the whole composite as unavailable when one part is
        # missing would hide the three that are known.
        weights = {
            "phase_maturity": 0.4,
            "risk_posture": 0.3,
            "capability_coverage": 0.2,
            "governance": 0.1,
        }
        available = {k: w for k, w in weights.items() if scores.get(k) is not None}
        total_weight = sum(available.values())
        # Renormalising over only the measured components is right when most
        # of the score is measured - it stops one missing input from dragging
        # the whole composite toward zero. It goes wrong at the other end: if
        # only the *governance* component (nominal weight 0.1) is measurable,
        # renormalising gives it 100% of the visible score, so one newly
        # created, unresolved ARB item can single-handedly report "Health
        # Score: 90" for a portfolio with zero solutions and zero measured
        # capability coverage. Confirmed live 6 Sep 2026
        # (Archie-E2E-Workflow-Test-Report.md E2E-M): "the score behaves as a
        # near-constant unrelated to its inputs."
        #
        # The threshold is 0.35, not 0.5: phase_maturity alone (0.4) is an
        # existing, deliberately-tested case
        # (test_health_phase_denominator_uses_only_recorded_valid_phases)
        # where a single substantial signal is trusted to stand for the
        # composite on its own - a portfolio's ADM phase spread is not a
        # throwaway metric the way one ARB item is. 0.35 sits strictly
        # between governance/risk_posture/capability_coverage alone (0.1,
        # 0.3, 0.2 - each still withheld) and phase_maturity alone (0.4 -
        # still reported), so it draws the line at "was more than the
        # single largest minor component measured", not at "was most of
        # the score measured".
        composite = (
            round(sum(scores[k] * w for k, w in available.items()) / total_weight, 1)
            if total_weight >= 0.35
            else None
        )

        return {
            "composite_score": composite,
            "components": scores,
            # Names the components that could not be measured, so the UI can say
            # which part of the score is missing rather than silently showing a
            # number derived from less than it appears.
            "unavailable_components": sorted(k for k in weights if scores.get(k) is None),
        }
