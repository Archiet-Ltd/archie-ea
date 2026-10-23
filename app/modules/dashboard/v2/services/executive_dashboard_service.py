"""Executive dashboard service — aggregates existing analytics for CTO/CIO view.

Queries real data from Solution, SolutionRisk, ARBReviewItem,
BusinessCapability, SolutionCapabilityMapping, ApplicationComponent,
VendorOrganization, and ArchiMateElement models.
"""

import logging
from datetime import datetime

from flask import g

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
            return {"counts": None, "total": None}

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
        """Counts for solutions, applications, vendors, ArchiMate elements.

        ``retiring_applications`` counts the organisation's applications whose
        lifecycle status is "retiring", the one definition of a retiring
        application (the retirement action sets it)."""
        stats = {
            "solutions": 0,
            "applications": 0,
            "vendors": 0,
            "archimate_elements": 0,
            "retiring_applications": 0,
        }
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

        try:
            from app.models.application_portfolio import ApplicationComponent

            org_id = getattr(g, "current_org_id", None)
            if org_id is not None:
                stats["retiring_applications"] = (
                    db.session.query(db.func.count(ApplicationComponent.id))
                    .filter(
                        ApplicationComponent.organization_id == org_id,
                        ApplicationComponent.lifecycle_status == "retiring",
                    )
                    .scalar()
                    or 0
                )
            else:
                stats["retiring_applications"] = 0
        except Exception as exc:
            logger.warning(
                "Executive dashboard: retiring application count unavailable: %s", exc
            )
            stats["retiring_applications"] = 0

        return stats

    # ------------------------------------------------------------------
    # Health score: one perspective component per measure
    # ------------------------------------------------------------------

    @staticmethod
    def _component(key, label, value, basis, source):
        """The contract every perspective component returns.

        ``value`` is a number when measured and ``None`` when not measured; it
        is never 0 or 100 standing in for "nothing to measure". ``basis`` is a
        plain sentence on what was counted, ``source`` names the store it came
        from, and ``as_of`` is the day the figure was read.
        """
        return {
            "key": key,
            "label": label,
            "value": value,
            "basis": basis,
            "source": source,
            "as_of": datetime.utcnow().date().isoformat(),
        }

    def _phase_maturity_component(self):
        """M23: share of phased solutions in phase C or later."""
        label = "phase maturity"
        source = "solution phases"
        try:
            from app.modules.dashboard.v2.services.solution_phase_measures import (
                ADVANCED_PHASES,
                recorded_phase_summary,
                share_in_advanced_phases,
            )

            summary = recorded_phase_summary()
            value = share_in_advanced_phases(summary)
            if value is None:
                return self._component(
                    "phase_maturity", label, None,
                    "No solution has a recorded phase yet.", source,
                )
            counts = summary["counts"]
            measured = sum(counts.values())
            advanced = sum(counts[phase] for phase in ADVANCED_PHASES)
            return self._component(
                "phase_maturity", label, value,
                f"{advanced} of {measured} solutions with a recorded phase are in phase C or later.",
                source,
            )
        except Exception:
            logger.exception("health score: phase maturity could not be measured")
            return self._component(
                "phase_maturity", label, None, "Solution phases could not be read.", source,
            )

    def _risk_posture_component(self):
        """Risk posture from the one risk summary; no second risk query.

        No open risk recorded is not a clean posture, and a failed read is not
        either: both are not measured.
        """
        label = "risk posture"
        source = "risk register"
        risk = self._get_risk_summary()
        counts, total = risk.get("counts"), risk.get("total")
        if counts is None or total is None:
            return self._component(
                "risk_posture", label, None, "The risk register could not be read.", source,
            )
        if total == 0:
            return self._component(
                "risk_posture", label, None, "No open risk is recorded yet.", source,
            )
        severe = counts.get("critical", 0) + counts.get("high", 0)
        return self._component(
            "risk_posture", label, round((1 - severe / total) * 100, 1),
            f"{total - severe} of {total} open risks are below high severity.", source,
        )

    def _capability_coverage_component(self):
        """M24: L1 capabilities with at least one mapped application."""
        label = "capability coverage"
        source = "business capabilities"
        cap = self._get_capability_coverage()
        value = cap.get("percentage")
        if value is None:
            return self._component(
                "capability_coverage", label, None,
                "No top-level capability is recorded yet.", source,
            )
        return self._component(
            "capability_coverage", label, value,
            f"{cap['covered']} of {cap['total']} top-level capabilities have a mapped application.",
            source,
        )

    def _governance_component(self):
        """Review-board throughput: timeliness of open items and approval rate.

        Not measured until at least one review item has been decided; there is
        no invented default approval rate and no floor.
        """
        label = "governance"
        source = "architecture review board"
        try:
            from datetime import timedelta

            from app.models.architecture_review_board import ARBReviewItem

            total = db.session.query(db.func.count(ARBReviewItem.id)).scalar() or 0
            resolved = (
                db.session.query(db.func.count(ARBReviewItem.id))
                .filter(ARBReviewItem.status.in_(["approved", "rejected"]))
                .scalar()
            ) or 0
            if total == 0:
                return self._component(
                    "governance", label, None, "No review item is recorded yet.", source,
                )
            if resolved == 0:
                return self._component(
                    "governance", label, None,
                    "No review item has been decided yet.", source,
                )
            cutoff = datetime.utcnow() - timedelta(days=30)
            overdue = (
                db.session.query(db.func.count(ARBReviewItem.id))
                .filter(
                    ARBReviewItem.status.notin_(["approved", "rejected"]),
                    ARBReviewItem.created_at < cutoff,
                )
                .scalar()
            ) or 0
            approved = (
                db.session.query(db.func.count(ARBReviewItem.id))
                .filter(ARBReviewItem.status == "approved")
                .scalar()
            ) or 0
            pending = total - resolved
            timeliness = (1 - overdue / pending) if pending > 0 else 1.0
            approval_rate = approved / resolved
            # Two parts, timeliness (2/3) and approval rate (1/3), scaled so a
            # perfect record is 100 and there is no floor.
            value = round((timeliness * 40 + approval_rate * 20) / 60 * 100, 1)
            return self._component(
                "governance", label, value,
                f"{resolved} of {total} review items decided, {approved} approved, "
                f"{overdue} open for more than 30 days.",
                source,
            )
        except Exception:
            logger.exception("health score: governance could not be measured")
            return self._component(
                "governance", label, None, "Review items could not be read.", source,
            )

    # Nominal weights of the four components; the composite renormalises them
    # over the components that were measured.
    HEALTH_WEIGHTS = {
        "phase_maturity": 0.4,
        "risk_posture": 0.3,
        "capability_coverage": 0.2,
        "governance": 0.1,
    }

    def _get_health_score(self):
        """Compute the composite architecture health score (0-100).

        Four perspective components, each a dict ``{"key", "label", "value",
        "basis", "source", "as_of"}`` whose ``value`` is a number or ``None``
        for not measured (nothing to measure, or the read failed). It is never
        0 or 100 standing in for a missing measure.

        The composite is the weighted average of the measured components with
        the nominal weights (phase maturity 40, risk posture 30, capability
        coverage 20, governance 10) renormalised over them. It is ``None`` when
        none are measured, and when the measured components carry less than 35%
        of the nominal weight, so one lightly weighted component cannot stand
        for the whole portfolio. ``left_out`` names, in plain words, the
        components the composite did not use.
        """
        components = {
            "phase_maturity": self._phase_maturity_component(),
            "risk_posture": self._risk_posture_component(),
            "capability_coverage": self._capability_coverage_component(),
            "governance": self._governance_component(),
        }
        weights = self.HEALTH_WEIGHTS
        available = {k: w for k, w in weights.items() if components[k]["value"] is not None}
        total_weight = sum(available.values())
        composite = (
            round(sum(components[k]["value"] * w for k, w in available.items()) / total_weight, 1)
            if total_weight >= 0.35
            else None
        )
        return {
            "composite_score": composite,
            "components": components,
            # Which parts could not be measured, as keys and as plain words, so
            # a screen can say what the score was not weighted from.
            "unavailable_components": sorted(k for k in weights if k not in available),
            "left_out": [components[k]["label"] for k in weights if k not in available],
        }
