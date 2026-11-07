"""
Compliance Tracking Service

Compliance of each business capability, read from the controls mapped to the applications that
support it. The figure comes from ComplianceInheritanceService.assess_capabilities, the one
computation of a compliance percentage; this service lists and words it.
"""

from datetime import datetime
from typing import Dict, List

from app.services.compliance.compliance_inheritance_service import ComplianceInheritanceService

from .decorators import transactional

_NOT_ASSESSED_NEXT_STEP = "Map the controls its applications must meet to assess it."


class ComplianceTrackingService:
    """Lists every capability with its compliance figure, and the findings behind it."""

    @transactional
    def analyze_compliance_portfolio(self, include_risk_assessment: bool = True) -> Dict:
        capabilities = self._get_all_capabilities()
        assessments = ComplianceInheritanceService.assess_capabilities([c["id"] for c in capabilities])

        rows = [self._row(cap, assessments[cap["id"]]) for cap in capabilities]
        # Findings first, then lowest score, then the unassessed.
        rows.sort(key=lambda r: (r["score"] is None, -r["missing_count"], r["score"] or 0, r["capability_name"]))

        assessed = [r for r in rows if r["score"] is not None]
        not_compliant = [r for r in assessed if r["status"] == "Not compliant"]
        partly = [r for r in assessed if r["status"] == "Partly compliant"]
        compliant = [r for r in assessed if r["status"] == "Compliant"]
        not_in_place = ComplianceInheritanceService.controls_not_in_place()
        portfolio_metrics = {
            "total_capabilities": len(rows),
            "assessed_capabilities": len(assessed),
            "compliance_rate": ComplianceInheritanceService.portfolio_score(assessed),
            "compliance_figure": ComplianceInheritanceService.format_score(
                ComplianceInheritanceService.portfolio_score(assessed)
            ),
            "compliant_capabilities": len(compliant),
            "missing_controls": not_in_place["total"],
            "controls_not_in_place": not_in_place,
            "controls_not_in_place_text": ComplianceInheritanceService.not_in_place_text(not_in_place),
        }
        return {
            "total_capabilities": len(rows),
            "capability_compliance": rows,
            "critical_compliance": not_compliant,
            "high_compliance": partly,
            "medium_compliance": [],
            "low_compliance": compliant,
            "portfolio_metrics": portfolio_metrics,
            "recommendations": self._recommendations(rows),
            "assessment_date": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def _row(capability: Dict, assessment: Dict) -> Dict:
        figure = ComplianceInheritanceService.figure(assessment)
        status = ComplianceInheritanceService.status(assessment)
        missing = assessment["missing"]
        if assessment["score"] is None:
            next_step = _NOT_ASSESSED_NEXT_STEP
        elif missing:
            next_step = "%d control%s missing." % (missing, "" if missing == 1 else "s")
        else:
            next_step = None
        last_review = assessment["last_review"]
        return {
            "capability_id": capability["id"],
            "capability_name": capability["name"],
            "capability_domain": capability["domain"],
            "score": assessment["score"],
            "compliance_score": assessment["score"],
            "figure": figure,
            "status": status,
            "assessed_controls": assessment["assessed_controls"],
            "missing_count": missing,
            "next_step": next_step,
            "last_review": last_review.strftime("%d %b %Y") if last_review else None,
            "findings": assessment["findings"],
        }

    @staticmethod
    def _recommendations(rows: List[Dict]) -> List[str]:
        """One sentence per capability with a missing control, then one for the capabilities not yet assessed."""
        sentences = []
        for row in rows:
            if row["missing_count"]:
                codes = ", ".join(sorted({f["control_code"] for f in row["findings"] if f["missing"]}))
                sentences.append(
                    "Put %s in place for %s (%s %s)."
                    % (
                        "the missing control" if row["missing_count"] == 1 else "the missing controls",
                        row["capability_name"],
                        "control" if row["missing_count"] == 1 else "controls",
                        codes,
                    )
                )
        unassessed = [r for r in rows if r["score"] is None]
        if unassessed:
            sentences.append(
                "Map controls to the applications behind %d capabilit%s that %s not assessed yet."
                % (len(unassessed), "y" if len(unassessed) == 1 else "ies", "is" if len(unassessed) == 1 else "are")
            )
        return sentences

    def _get_all_capabilities(self) -> List[Dict]:
        """Every business capability of the organisation. Errors propagate so the page reports the failure
        instead of an empty portfolio."""
        from app.models.business_capability import BusinessCapability

        return [
            {"id": cap.id, "name": cap.name, "domain": cap.business_domain}
            for cap in BusinessCapability.query.order_by(BusinessCapability.name).all()
        ]
