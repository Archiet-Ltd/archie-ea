"""
Risk Mitigation Service

Manages risk mitigation plans, owner assignments, and status tracking
for capability risks identified in the Risk Assessment dashboard.

Links capability risks (from RiskAssessmentService) to RiskAssessment
records for persistent mitigation tracking.
"""

from datetime import date, datetime
from typing import Dict, Optional


from app import db
from app.models.business_capabilities import BusinessCapability
from app.models.models import RiskAssessment
from app.services.decorators import transactional


class RiskMitigationService:
    """
    Service for risk mitigation planning and tracking.
    
    Key capabilities:
    - Create/update mitigation plans for capability risks
    - Assign risk owners and action owners
    - Track mitigation status and progress
    - Calculate residual risk after mitigation
    - Link risks to compliance requirements
    """
    
    @staticmethod
    @transactional
    def get_risk_details(capability_id: int) -> Optional[Dict]:
        """
        Get comprehensive risk details for a capability.
        
        Args:
            capability_id: ID of the BusinessCapability
        
        Returns:
            Dict with capability info + mitigation details if RiskAssessment exists
        """
        capability = BusinessCapability.query.get(capability_id)
        if not capability:
            return None
        
        # Check if RiskAssessment record exists
        risk_assessment = RiskAssessment.query.filter_by(
            capability_id=capability_id
        ).first()
        
        # Calculate risk metrics from RiskAssessmentService data
        from app.services.risk_assessment_service import RiskAssessmentService
        service = RiskAssessmentService()
        risk_data = service._analyze_capability_risks(capability, include_tech_debt=True)
        
        result = {
            "capability_id": capability.id,
            "capability_name": capability.name,
            "capability_domain": capability.business_domain or "Unknown",
            "strategic_importance": capability.strategic_importance,
            "risk_metrics": {
                "spof_risk": risk_data.get("spof_risk", 0),
                "technology_risk": risk_data.get("technology_risk", 0),
                "compliance_risk": risk_data.get("compliance_risk", 0),
                "dependency_risk": risk_data.get("dependency_risk", 0),
                "skill_risk": risk_data.get("skill_risk", 0),
                "overall_risk_score": risk_data.get("overall_risk_score", 0),
                "risk_level": risk_data.get("risk_level", "LOW"),
                "risk_factors": risk_data.get("risk_factors", []),
            },
            "mitigation": None
        }
        
        # Add mitigation details if record exists
        if risk_assessment:
            result["mitigation"] = {
                "id": risk_assessment.id,
                "risk_owner": risk_assessment.risk_owner,
                "action_owner": risk_assessment.action_owner,
                "status": risk_assessment.status,
                "response_strategy": risk_assessment.response_strategy,
                "mitigation_strategy": risk_assessment.mitigation_strategy,
                "contingency_plan": risk_assessment.contingency_plan,
                "mitigation_cost": float(risk_assessment.mitigation_cost) if risk_assessment.mitigation_cost else None,
                "mitigation_effort": risk_assessment.mitigation_effort,
                "residual_probability": risk_assessment.residual_probability,
                "residual_impact": risk_assessment.residual_impact,
                "residual_risk_score": risk_assessment.residual_risk_score,
                "identified_date": risk_assessment.identified_date.isoformat() if risk_assessment.identified_date else None,
                "review_date": risk_assessment.review_date.isoformat() if risk_assessment.review_date else None,
                "next_review_date": risk_assessment.next_review_date.isoformat() if risk_assessment.next_review_date else None,
                "risk_reduction_percentage": risk_assessment.risk_reduction_percentage
            }
        
        return result
    
    @staticmethod
    @transactional
    def create_or_update_mitigation(capability_id: int, mitigation_data: Dict) -> Dict:
        """
        Create or update mitigation plan for a capability risk.
        
        Args:
            capability_id: ID of the BusinessCapability
            mitigation_data: Dict with mitigation fields
        
        Returns:
            Dict with updated risk assessment details
        """
        capability = BusinessCapability.query.get(capability_id)
        if not capability:
            raise ValueError(f"Capability {capability_id} not found")
        
        # Check if RiskAssessment exists
        risk_assessment = RiskAssessment.query.filter_by(
            capability_id=capability_id
        ).first()
        
        # Calculate current risk score for the name/description
        from app.services.risk_assessment_service import RiskAssessmentService
        service = RiskAssessmentService()
        risk_data = service._analyze_capability_risks(capability, include_tech_debt=True)
        
        if not risk_assessment:
            # Create new RiskAssessment
            risk_assessment = RiskAssessment(
                name=f"Risk Assessment: {capability.name}",
                description=risk_data.get("risk_assessment", "Risk assessment for capability"),
                capability_id=capability_id,
                risk_type="operational",  # Default, can be overridden
                risk_category="threat",
                identified_date=date.today(),
                status="identified"
            )
            db.session.add(risk_assessment)
        
        # Update mitigation fields
        if "risk_owner" in mitigation_data:
            risk_assessment.risk_owner = mitigation_data["risk_owner"]
        
        if "action_owner" in mitigation_data:
            risk_assessment.action_owner = mitigation_data["action_owner"]
        
        if "status" in mitigation_data:
            risk_assessment.status = mitigation_data["status"]
        
        if "response_strategy" in mitigation_data:
            risk_assessment.response_strategy = mitigation_data["response_strategy"]
        
        if "mitigation_strategy" in mitigation_data:
            risk_assessment.mitigation_strategy = mitigation_data["mitigation_strategy"]
        
        if "contingency_plan" in mitigation_data:
            risk_assessment.contingency_plan = mitigation_data["contingency_plan"]
        
        if "mitigation_cost" in mitigation_data:
            risk_assessment.mitigation_cost = mitigation_data["mitigation_cost"]
        
        if "mitigation_effort" in mitigation_data:
            risk_assessment.mitigation_effort = mitigation_data["mitigation_effort"]
        
        if "target_date" in mitigation_data and mitigation_data["target_date"]:
            risk_assessment.next_review_date = datetime.strptime(
                mitigation_data["target_date"], "%Y-%m-%d"
            ).date()
        
        if "residual_probability" in mitigation_data:
            risk_assessment.residual_probability = mitigation_data["residual_probability"]
        
        if "residual_impact" in mitigation_data:
            risk_assessment.residual_impact = mitigation_data["residual_impact"]
        
        # Calculate residual risk score if both values provided
        if risk_assessment.residual_probability and risk_assessment.residual_impact:
            risk_assessment.calculate_residual_risk_score()
        
        # Set probability and impact from current risk score
        # Map overall_risk_score (0-100) to probability/impact (1-5)
        overall_score = risk_data.get("overall_risk_score", 0)
        if overall_score >= 80:
            risk_assessment.probability_score = 5
            risk_assessment.impact_score = 5
            risk_assessment.probability = "very_high"
            risk_assessment.impact = "critical"
        elif overall_score >= 60:
            risk_assessment.probability_score = 4
            risk_assessment.impact_score = 4
            risk_assessment.probability = "high"
            risk_assessment.impact = "high"
        elif overall_score >= 40:
            risk_assessment.probability_score = 3
            risk_assessment.impact_score = 3
            risk_assessment.probability = "medium"
            risk_assessment.impact = "medium"
        elif overall_score >= 20:
            risk_assessment.probability_score = 2
            risk_assessment.impact_score = 2
            risk_assessment.probability = "low"
            risk_assessment.impact = "low"
        else:
            risk_assessment.probability_score = 1
            risk_assessment.impact_score = 1
            risk_assessment.probability = "very_low"
            risk_assessment.impact = "negligible"
        
        # Calculate risk score
        risk_assessment.calculate_risk_score()
        
        # Update review date
        risk_assessment.review_date = date.today()
        
        db.session.commit()
        
        return RiskMitigationService.get_risk_details(capability_id)
    
    @staticmethod
    @transactional
    def get_all_mitigation_statuses() -> Dict[int, Dict]:
        """
        Get mitigation status for all capabilities with risk assessments.
        
        Returns:
            Dict mapping capability_id to mitigation status info
        """
        assessments = RiskAssessment.query.filter(
            RiskAssessment.capability_id.isnot(None)
        ).all()
        
        result = {}
        for assessment in assessments:
            result[assessment.capability_id] = {
                "status": assessment.status,
                "risk_owner": assessment.risk_owner,
                "action_owner": assessment.action_owner,
                "next_review_date": assessment.next_review_date.isoformat() if assessment.next_review_date else None,
                "has_mitigation_strategy": bool(assessment.mitigation_strategy),
                "risk_reduction": assessment.risk_reduction_percentage
            }
        
        return result
