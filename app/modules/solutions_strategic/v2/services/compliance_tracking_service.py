"""
Compliance Tracking Service

Compliance of each business capability, read from the controls mapped to the applications that
support it. The figure comes from ComplianceInheritanceService.assess_capabilities, the one
computation of a compliance percentage; this service lists and words it.
"""

from datetime import datetime
from typing import Dict, List

from app.services import result_footer_service as footers
from app.services.compliance.compliance_inheritance_service import ComplianceInheritanceService

from .decorators import transactional

_CONTROLS_NAMED = 3
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
        # The headline is the one count of controls not in place, each control once, read from the service that
        # owns it (it includes a control on an application that supports no capability). The rows count a control
        # under each capability its application supports, so their total can be higher; the page says so.
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
            "capability_rows_total": sum(r["missing_count"] for r in rows),
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
        missing = assessment["not_in_place"]
        if assessment["score"] is None:
            next_step = _NOT_ASSESSED_NEXT_STEP
        elif missing:
            next_step = "%d control%s not in place." % (missing, "" if missing == 1 else "s")
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
            "footer": ComplianceTrackingService._footer(capability, assessment),
        }

    @staticmethod
    def _footer(capability: Dict, assessment: Dict) -> Dict:
        """Evidence, source, confidence and date for one capability's figure, from the one result footer."""
        facts = [
            footers.fact(
                capability["name"],
                href=footers.page_link("archimate_crud.detail_element", layer="strategy",
                                       element_type="Capability", element_id=capability["id"]),
                source_kind="manual", as_of=capability.get("updated_at"), subject="Capability"),
        ]
        total = assessment["assessed_controls"]
        if not total:
            facts.append(footers.fact("No control is mapped to the applications that support it", missing=True))
            return footers.build_footer(facts)
        in_place = assessment["implemented"]
        facts.append(footers.fact(
            "%d control%s mapped, %d in place" % (total, "" if total == 1 else "s", in_place),
            href=footers.page_link("application_mgmt.compliance_frameworks_dashboard"),
            source_kind="manual", as_of=assessment["newest_record"], subject="Controls"))
        documented = assessment["with_evidence"]
        if documented:
            facts.append(footers.fact(
                "Evidence document recorded for %d of %d controls" % (documented, total),
                source_kind="manual", as_of=assessment["newest_record"], subject="Document"))
        else:
            facts.append(footers.fact("No evidence document is recorded for any of its %d control%s"
                                      % (total, "" if total == 1 else "s"), missing=True))
        return footers.build_footer(facts)

    @staticmethod
    def _recommendations(rows: List[Dict]) -> List[str]:
        """One sentence per capability with a control not in place, then one for the capabilities not yet assessed.

        A sentence names at most _CONTROLS_NAMED controls and counts the rest; the full list is on the
        compliance dashboard, which the page links to.
        """
        sentences = []
        for row in rows:
            if row["missing_count"]:
                codes = sorted({f["control_code"] for f in row["findings"]})
                named, rest = codes[:_CONTROLS_NAMED], len(codes) - _CONTROLS_NAMED
                listed = ", ".join(named) + (" and %d more" % rest if rest > 0 else "")
                sentences.append(
                    "Complete %s for %s (%s %s)."
                    % (
                        "the control not in place" if len(codes) == 1 else "the controls not in place",
                        row["capability_name"],
                        "control" if len(codes) == 1 else "controls",
                        listed,
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
            {"id": cap.id, "name": cap.name, "domain": cap.business_domain,
             "updated_at": cap.updated_at or cap.created_at}
            for cap in BusinessCapability.query.order_by(BusinessCapability.name).all()
        ]
