"""
Compliance Checker Service

Validates architecture decisions against regulatory requirements, quality attributes,
and project constraints.

This service is the "gatekeeper" that Enterprise Architects use to ensure all
architecture decisions comply with mandatory regulations and constraints.
"""

import logging
from datetime import datetime
from typing import Dict, List

from app import db
from app.models import ArchitectureModel
from app.models.compliance_models import (
    ComplianceRequirement,
    ProjectConstraint,
    RegulatoryFramework,
)
from app.models.implementation_migration import Gap
from app.services.gap_register_service import create_gap

logger = logging.getLogger(__name__)


class ComplianceCheckerService:
    """
    Validates architecture against compliance requirements and identifies gaps.
    """

    def __init__(self):
        """Initialize compliance checker"""
        self.validation_results = []

    def validate_architecture(
        self, architecture_id: int, region: str = "US", industry: str = "general"
    ) -> Dict:
        """
        Comprehensive compliance validation of an architecture model.

        Args:
            architecture_id: ID of ArchitectureModel to validate
            region: Geographic region (US, EU, Global)
            industry: Industry type (pharmaceutical, automotive, food, general)

        Returns:
            Dictionary with validation results and gaps
        """
        logger.info(f"Starting compliance validation for architecture {architecture_id}")

        architecture = db.session.get(ArchitectureModel, architecture_id)
        if not architecture:
            raise ValueError(f"Architecture {architecture_id} not found")

        results = {
            "architecture_id": architecture_id,
            "architecture_name": architecture.name,
            "validation_timestamp": datetime.utcnow().isoformat(),
            "region": region,
            "industry": industry,
            "overall_compliant": True,
            "compliance_score": 0.0,  # 0 - 100
            "checks_performed": 0,
            "checks_passed": 0,
            "checks_failed": 0,
            "gaps_identified": [],
            "regulatory_violations": [],
            "quality_violations": [],
            "constraint_violations": [],
            "recommendations": [],
        }

        # Get applicable frameworks
        from app.services.compliance.regulatory_framework_service import RegulatoryFrameworkService

        applicable_frameworks = RegulatoryFrameworkService.get_applicable_frameworks(
            region=region, industry=industry
        )

        logger.info(f"Found {len(applicable_frameworks)} applicable frameworks")

        # Check 1: Regulatory compliance
        regulatory_results = self._check_regulatory_compliance(architecture, applicable_frameworks)
        results["checks_performed"] += regulatory_results["checks_performed"]
        results["checks_passed"] += regulatory_results["checks_passed"]
        results["checks_failed"] += regulatory_results["checks_failed"]
        results["regulatory_violations"].extend(regulatory_results["violations"])

        # Check 2: Quality attributes (NFRs)
        quality_results = self._check_quality_attributes(architecture)
        results["checks_performed"] += quality_results["checks_performed"]
        results["checks_passed"] += quality_results["checks_passed"]
        results["checks_failed"] += quality_results["checks_failed"]
        results["quality_violations"].extend(quality_results["violations"])

        # Check 3: Project constraints
        constraint_results = self._check_project_constraints(architecture)
        results["checks_performed"] += constraint_results["checks_performed"]
        results["checks_passed"] += constraint_results["checks_passed"]
        results["checks_failed"] += constraint_results["checks_failed"]
        results["constraint_violations"].extend(constraint_results["violations"])

        # Calculate compliance score
        if results["checks_performed"] > 0:
            results["compliance_score"] = (
                results["checks_passed"] / results["checks_performed"]
            ) * 100
        else:
            results["compliance_score"] = 100.0

        results["overall_compliant"] = results["checks_failed"] == 0

        # Generate gaps
        gaps = self._generate_compliance_gaps(results)
        results["gaps_identified"] = gaps
        results["total_gaps"] = len(gaps)

        # Generate recommendations
        results["recommendations"] = self._generate_recommendations(results)

        logger.info(
            f"Validation complete: {results['compliance_score']:.1f}% compliant, "
            f"{results['total_gaps']} gaps identified"
        )

        return results

    def _check_regulatory_compliance(
        self, architecture: ArchitectureModel, frameworks: List[RegulatoryFramework]
    ) -> Dict:
        """
        Check compliance with regulatory frameworks.

        Validates:
        - All mandatory controls are addressed
        - Critical controls have implementations
        - Evidence is documented
        """
        results = {"checks_performed": 0, "checks_passed": 0, "checks_failed": 0, "violations": []}

        for framework in frameworks:
            # Get critical controls for this framework
            critical_controls = framework.controls.filter_by(priority="critical").all()

            for control in critical_controls:
                results["checks_performed"] += 1

                # Check if there's a compliance requirement addressing this control
                requirement = ComplianceRequirement.query.filter_by(control_id=control.id).first()

                if not requirement:
                    # Missing requirement for critical control
                    results["checks_failed"] += 1
                    results["violations"].append(
                        {
                            "type": "missing_requirement",
                            "severity": "critical",
                            "framework": framework.code,
                            "control": control.control_id,
                            "title": control.title,
                            "message": f"No requirement defined for critical control: {control.title}",
                            "remediation": f"Create compliance requirement for {framework.code} {control.control_id}",
                        }
                    )
                elif requirement.implementation_status != "completed":
                    # Requirement exists but not implemented
                    results["checks_failed"] += 1
                    results["violations"].append(
                        {
                            "type": "incomplete_implementation",
                            "severity": "high",
                            "framework": framework.code,
                            "control": control.control_id,
                            "title": requirement.title,
                            "message": f"Requirement not implemented: {requirement.title}",
                            "status": requirement.implementation_status,
                            "remediation": "Complete implementation and provide evidence",
                        }
                    )
                else:
                    # Passed
                    results["checks_passed"] += 1

        return results

    def _check_quality_attributes(self, architecture: ArchitectureModel) -> Dict:
        """
        Check quality attributes (NFRs) compliance.

        Validates:
        - Performance thresholds are met
        - Reliability targets are achieved
        - Security requirements are implemented
        """
        results = {"checks_performed": 0, "checks_passed": 0, "checks_failed": 0, "violations": []}

        # Get all quality attributes for elements in this architecture
        for element in architecture.elements:
            quality_attrs = element.quality_attributes

            for qa in quality_attrs:
                results["checks_performed"] += 1

                # Evaluate if quality attribute is met
                is_met = qa.evaluate_compliance()

                if is_met is None:
                    # Cannot evaluate (missing data)
                    results["violations"].append(
                        {
                            "type": "unmeasured_nfr",
                            "severity": "medium",
                            "element": element.name,
                            "attribute": qa.name,
                            "message": f"Quality attribute '{qa.name}' cannot be measured (missing current value)",
                            "remediation": "Implement monitoring and measurement",
                        }
                    )
                elif is_met:
                    results["checks_passed"] += 1
                else:
                    # Violated
                    results["checks_failed"] += 1
                    severity = "critical" if qa.priority == "critical" else "high"
                    results["violations"].append(
                        {
                            "type": "nfr_violation",
                            "severity": severity,
                            "element": element.name,
                            "attribute": qa.name,
                            "target": f"{qa.target_value} {qa.metric_unit}",
                            "current": f"{qa.current_value} {qa.metric_unit}",
                            "message": f"Quality attribute not met: {qa.name} (current: {qa.current_value}, target: {qa.target_value})",
                            "remediation": f"Optimize performance to meet {qa.target_value} {qa.metric_unit} threshold",
                        }
                    )

        return results

    def _check_project_constraints(self, architecture: ArchitectureModel) -> Dict:
        """
        Check project constraints compliance.

        Validates:
        - Budget constraints not exceeded
        - Timeline constraints feasible
        - Resource availability
        - Technical debt limitations
        """
        results = {"checks_performed": 0, "checks_passed": 0, "checks_failed": 0, "violations": []}

        # Get all constraints
        constraints = ProjectConstraint.query.filter_by(status="active").all()

        for constraint in constraints:
            results["checks_performed"] += 1

            if constraint.is_hard_constraint and constraint.is_violated:
                # Hard constraint violated
                results["checks_failed"] += 1
                results["violations"].append(
                    {
                        "type": "constraint_violation",
                        "severity": "critical" if constraint.priority == "critical" else "high",
                        "constraint": constraint.name,
                        "constraint_type": constraint.constraint_type,
                        "limit": constraint.limit_value,
                        "message": f"Hard constraint violated: {constraint.name}",
                        "violation_notes": constraint.violation_notes,
                        "remediation": f"Adjust architecture to comply with {constraint.name} or request waiver from {constraint.decision_authority}",
                    }
                )
            else:
                results["checks_passed"] += 1

        return results

    def _generate_compliance_gaps(self, validation_results: Dict) -> List[Dict]:
        """
        Generate compliance gap records from validation results.

        These gaps are saved to database for tracking and remediation.
        """
        gaps = []

        # Generate gaps from violations
        all_violations = (
            validation_results["regulatory_violations"]
            + validation_results["quality_violations"]
            + validation_results["constraint_violations"]
        )

        for violation in all_violations:
            gap = {
                "gap_type": violation["type"],
                "title": violation.get("title", violation["message"]),
                "description": violation["message"],
                "risk_level": violation["severity"],
                "impact_description": self._assess_impact(violation),
                "remediation_action": violation.get("remediation", "Review and address violation"),
                "status": "open",
            }
            gaps.append(gap)

        return gaps

    def _assess_impact(self, violation: Dict) -> str:
        """Assess business impact of a violation"""
        severity = violation["severity"]
        vtype = violation["type"]

        if severity == "critical":
            if vtype == "missing_requirement":
                return "Regulatory non-compliance risk. Potential fines, legal action, or business shutdown."
            elif vtype == "constraint_violation":
                return "Project failure risk. Budget overrun, missed deadlines, or scope reduction."
            elif vtype == "nfr_violation":
                return "System failure risk. Poor performance, outages, or user dissatisfaction."
        elif severity == "high":
            return "Significant risk. May impact business operations, customer satisfaction, or regulatory standing."
        elif severity == "medium":
            return "Moderate risk. Should be addressed but not blocking."
        else:
            return "Low risk. Can be addressed in future iterations."

    def _generate_recommendations(self, validation_results: Dict) -> List[str]:
        """Generate recommendations for Enterprise Architects"""
        recommendations = []

        # Based on compliance score
        score = validation_results["compliance_score"]

        if score < 70:
            recommendations.append(
                "⚠️ CRITICAL: Architecture is non-compliant. Major rework required before proceeding."
            )
        elif score < 85:
            recommendations.append(
                "⚠️ WARNING: Architecture has compliance gaps. Address critical violations before implementation."
            )
        elif score < 95:
            recommendations.append(
                "✓ ACCEPTABLE: Architecture is mostly compliant. Address minor gaps during implementation."
            )
        else:
            recommendations.append("✓ EXCELLENT: Architecture meets all compliance requirements.")

        # Regulatory-specific
        if validation_results["regulatory_violations"]:
            critical_reg = [
                v
                for v in validation_results["regulatory_violations"]
                if v["severity"] == "critical"
            ]
            if critical_reg:
                recommendations.append(
                    f"🔴 {len(critical_reg)} critical regulatory violations must be resolved immediately."
                )

        # Quality-specific
        if validation_results["quality_violations"]:
            performance_issues = [
                v
                for v in validation_results["quality_violations"]
                if "performance" in v.get("attribute", "").lower()
            ]
            if performance_issues:
                recommendations.append(
                    f"⚡ {len(performance_issues)} performance NFRs not met. Consider architecture optimization."
                )

        # Constraint-specific
        if validation_results["constraint_violations"]:
            budget_issues = [
                v
                for v in validation_results["constraint_violations"]
                if v.get("constraint_type") == "budget"
            ]
            if budget_issues:
                recommendations.append(
                    "💰 Budget constraints violated. Reduce scope or request additional funding."
                )

        return recommendations

    def save_gaps_to_database(self, architecture_id: int, gaps: List[Dict]) -> int:
        """
        Save identified gaps to database for tracking.

        Returns:
            Number of gaps saved
        """
        saved_count = 0

        # Get the architecture to determine organization_id
        architecture = db.session.get(ArchitectureModel, architecture_id)
        if not architecture:
            logger.warning(f"Architecture {architecture_id} not found")
            return 0
        org_id = architecture.organization_id

        for gap_data in gaps:
            # Check if gap already exists (avoid duplicates) - check in register
            existing = Gap.query.filter_by(name=gap_data["title"], organization_id=org_id).first()

            if existing:
                logger.info(f"Gap already exists: {gap_data['title']}")
                continue

            # Create gap in the one gap register (replaces ComplianceGap)
            create_gap(
                organization_id=org_id,
                name=gap_data["title"],
                description=gap_data.get("description", ""),
                gap_type="compliance",
                priority=gap_data.get("risk_level", "medium"),
                severity=gap_data.get("risk_level", "medium"),
                resolution_status="identified",
                current_state_ref=gap_data.get("impact_description", ""),
                target_state_ref=gap_data.get("remediation_action", ""),
                generation_source="compliance_checker_service",
                auto_generated=True,
            )
            saved_count += 1

        db.session.commit()

        logger.info(f"Saved {saved_count} compliance gaps to database")
        return saved_count

    def generate_compliance_report(self, architecture_id: int, region: str = "US") -> str:
        """
        Generate human-readable compliance report.

        Returns:
            Markdown-formatted report
        """
        results = self.validate_architecture(architecture_id, region=region)

        report = f"""# Compliance Validation Report

**Architecture:** {results['architecture_name']}
**Date:** {results['validation_timestamp']}
**Region:** {results['region']}
**Industry:** {results['industry']}

---

## Overall Compliance Score: {results['compliance_score']:.1f}%

{'✅ COMPLIANT' if results['overall_compliant'] else '❌ NON-COMPLIANT'}

**Checks Performed:** {results['checks_performed']}
**Checks Passed:** {results['checks_passed']} ✓
**Checks Failed:** {results['checks_failed']} ✗

---

## Summary

- **Regulatory Violations:** {len(results['regulatory_violations'])}
- **Quality Violations:** {len(results['quality_violations'])}
- **Constraint Violations:** {len(results['constraint_violations'])}
- **Total Gaps Identified:** {results['total_gaps']}

---

## Recommendations

"""

        for rec in results["recommendations"]:
            report += f"- {rec}\n"

        report += "\n---\n\n## Detailed Findings\n\n"

        # Regulatory violations
        if results["regulatory_violations"]:
            report += "### 🔴 Regulatory Violations\n\n"
            for violation in results["regulatory_violations"]:
                report += f"**{violation['severity'].upper()}**: {violation['message']}\n"
                report += f"- Framework: {violation.get('framework', 'N/A')}\n"
                report += f"- Control: {violation.get('control', 'N/A')}\n"
                report += f"- Remediation: {violation.get('remediation', 'N/A')}\n\n"

        # Quality violations
        if results["quality_violations"]:
            report += "### ⚡ Quality Attribute Violations\n\n"
            for violation in results["quality_violations"]:
                report += f"**{violation['severity'].upper()}**: {violation['message']}\n"
                report += f"- Element: {violation.get('element', 'N/A')}\n"
                report += f"- Remediation: {violation.get('remediation', 'N/A')}\n\n"

        # Constraint violations
        if results["constraint_violations"]:
            report += "### 💰 Constraint Violations\n\n"
            for violation in results["constraint_violations"]:
                report += f"**{violation['severity'].upper()}**: {violation['message']}\n"
                report += f"- Type: {violation.get('constraint_type', 'N/A')}\n"
                report += f"- Limit: {violation.get('limit', 'N/A')}\n"
                report += f"- Remediation: {violation.get('remediation', 'N/A')}\n\n"

        report += "\n---\n\n**Report Generated:** " + datetime.utcnow().strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        return report
