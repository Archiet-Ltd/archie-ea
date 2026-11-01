"""
DEPRECATED: Import from app.modules.capabilities.services instead.
-> app.modules.capabilities.services.capability_service

Capability Governance Service

Implements real governance processes for capability management.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict

from app.models.unified_capability import UnifiedCapability

from app import db

logger = logging.getLogger(__name__)


class CapabilityGovernanceService:
    """Service for implementing real governance processes."""

    @staticmethod
    def create_governance_workflow(
        capability_id: int, owner: str = None, description: str = None, criticality: str = None
    ) -> Dict:
        """
        Create a governance workflow for a capability.

        Args:
            capability_id: ID of the capability
            owner: Owner of the workflow
            description: Description of the workflow
            criticality: Criticality level

        Returns:
            Dictionary with workflow details
        """
        try:
            capability = UnifiedCapability.query.get(capability_id)
            if not capability:
                return {"success": False, "error": "Capability not found"}

            # Create governance workflow stages
            workflow_data = {
                "capability_id": capability_id,
                "capability_name": capability.name,
                "business_owner": capability.business_owner,
                "owner": owner or capability.business_owner,
                "description": description or f"Governance workflow for {capability.name}",
                "criticality": criticality
                or capability.business_criticality
                or "business_critical",
                "stages": [
                    {
                        "name": "Strategic Review",
                        "responsible": owner or capability.business_owner,
                        "due_date": datetime.utcnow() + timedelta(days=30),
                        "status": "pending",
                        "requirements": [
                            "Validate strategic alignment",
                            "Assess business value",
                            "Review resource requirements",
                        ],
                    },
                    {
                        "name": "Stakeholder Approval",
                        "responsible": "Governance Committee",
                        "due_date": datetime.utcnow() + timedelta(days=45),
                        "status": "pending",
                        "requirements": [
                            "Stakeholder sign-off",
                            "Risk assessment",
                            "Compliance check",
                        ],
                    },
                    {
                        "name": "Implementation Planning",
                        "responsible": "IT Leadership",
                        "due_date": datetime.utcnow() + timedelta(days=60),
                        "status": "pending",
                        "requirements": [
                            "Technical feasibility",
                            "Resource allocation",
                            "Timeline definition",
                        ],
                    },
                    {
                        "name": "Business Validation",
                        "responsible": owner or capability.business_owner,
                        "due_date": datetime.utcnow() + timedelta(days=90),
                        "status": "pending",
                        "requirements": [
                            "Business outcome validation",
                            "ROI measurement",
                            "Performance metrics",
                        ],
                    },
                ],
                "created_at": datetime.utcnow(),
                "status": "active",
            }

            # Persist the workflow to database
            from app.models.capability_governance import GovernanceWorkflow

            workflow = GovernanceWorkflow(
                capability_id=capability_id,
                owner=workflow_data["owner"],
                description=workflow_data["description"],
                criticality=workflow_data["criticality"],
                status="active",
                workflow_data=workflow_data,
            )

            db.session.add(workflow)
            db.session.commit()
            db.session.refresh(workflow)

            return {
                "success": True,
                "workflow": workflow_data,
                "workflow_id": workflow.id,
                "message": f"Governance workflow created for {capability.name}",
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating governance workflow: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_governance_dashboard() -> Dict:
        """
        Get governance dashboard with all workflows and status.

        Returns:
            Dictionary with dashboard data
        """
        try:
            # Get all capabilities with governance status
            capabilities = UnifiedCapability.active().all()

            dashboard = {
                "total_capabilities": len(capabilities),
                "governance_status": {"compliant": 0, "in_progress": 0, "non_compliant": 0},
                "owner_distribution": {},
                "criticality_distribution": {
                    "mission_critical": 0,
                    "important": 0,
                    "supporting": 0,
                },
                "level_distribution": {"Level 1": 0, "Level 2": 0, "Level 3": 0},
                "recent_activities": [],
            }

            # Calculate distributions
            for cap in capabilities:
                # Owner distribution
                owner = cap.business_owner or "Unassigned"
                if owner not in dashboard["owner_distribution"]:
                    dashboard["owner_distribution"][owner] = 0
                dashboard["owner_distribution"][owner] += 1

                # Criticality distribution - map various values to standard keys
                criticality = cap.business_criticality or "supporting"
                # Map common criticality values to standard keys
                criticality_map = {
                    "high": "mission_critical",
                    "critical": "mission_critical",
                    "mission_critical": "mission_critical",
                    "medium": "important",
                    "important": "important",
                    "low": "supporting",
                    "supporting": "supporting",
                }
                normalized_criticality = criticality_map.get(
                    criticality.lower() if criticality else "supporting", "supporting"
                )
                dashboard["criticality_distribution"][normalized_criticality] += 1

                # Level distribution - handle None or invalid levels
                level = cap.level if cap.level and 1 <= cap.level <= 3 else 1
                level_key = f"Level {level}"
                if level_key not in dashboard["level_distribution"]:
                    dashboard["level_distribution"][level_key] = 0
                dashboard["level_distribution"][level_key] += 1

                # Governance status (simulate based on data completeness)
                if cap.business_owner and cap.business_criticality and cap.strategic_importance:
                    dashboard["governance_status"]["compliant"] += 1
                elif cap.business_owner or cap.business_criticality:
                    dashboard["governance_status"]["in_progress"] += 1
                else:
                    dashboard["governance_status"]["non_compliant"] += 1

            # Calculate compliance percentage
            total = (
                dashboard["governance_status"]["compliant"]
                + dashboard["governance_status"]["in_progress"]
                + dashboard["governance_status"]["non_compliant"]
            )
            if total > 0:
                dashboard["compliance_percentage"] = (
                    dashboard["governance_status"]["compliant"] / total
                ) * 100
            else:
                dashboard["compliance_percentage"] = 0

            return {"success": True, "dashboard": dashboard}

        except Exception as e:
            logger.error(f"Error generating governance dashboard: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def create_business_process(capability_id: int, process_type: str) -> Dict:
        """
        Create a business process for a capability.

        Args:
            capability_id: ID of the capability
            process_type: Type of process (strategic, operational, support)

        Returns:
            Dictionary with process details
        """
        try:
            capability = UnifiedCapability.query.get(capability_id)
            if not capability:
                return {"success": False, "error": "Capability not found"}

            # Define process templates
            process_templates = {
                "strategic": {
                    "name": "Strategic Planning Process",
                    "steps": [
                        "Business requirement analysis",
                        "Market research and analysis",
                        "Strategic option evaluation",
                        "Decision making and approval",
                        "Implementation planning",
                        "Performance monitoring",
                    ],
                    "frequency": "Annual",
                    "participants": [capability.business_owner, "CEO", "Board"],
                    "outputs": ["Strategic Plan", "KPIs", "Budget Allocation"],
                },
                "operational": {
                    "name": "Operational Management Process",
                    "steps": [
                        "Daily operations execution",
                        "Performance monitoring",
                        "Issue identification and resolution",
                        "Process optimization",
                        "Stakeholder communication",
                        "Continuous improvement",
                    ],
                    "frequency": "Daily",
                    "participants": [capability.business_owner, "Operations Team", "Support Staff"],
                    "outputs": ["Performance Reports", "Issue Logs", "Improvement Plans"],
                },
                "support": {
                    "name": "Support Service Process",
                    "steps": [
                        "Service request receipt",
                        "Prioritization and categorization",
                        "Resource allocation",
                        "Service delivery",
                        "Quality assurance",
                        "Customer feedback collection",
                    ],
                    "frequency": "On-demand",
                    "participants": [capability.business_owner, "Support Team", "End Users"],
                    "outputs": ["Service Tickets", "Resolution Reports", "Customer Satisfaction"],
                },
            }

            template = process_templates.get(process_type, process_templates["operational"])

            process = {
                "capability_id": capability_id,
                "capability_name": capability.name,
                "process_type": process_type,
                "process_name": template["name"],
                "steps": template["steps"],
                "frequency": template["frequency"],
                "participants": template["participants"],
                "outputs": template["outputs"],
                "created_at": datetime.utcnow(),
                "status": "active",
            }

            return {
                "success": True,
                "process": process,
                "message": f"Business process created for {capability.name}",
            }

        except Exception as e:
            logger.error(f"Error creating business process: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_capability_health_check(capability_id: int) -> Dict:
        """
        Perform health check on a capability.

        Args:
            capability_id: ID of the capability

        Returns:
            Dictionary with health check results
        """
        try:
            capability = UnifiedCapability.query.get(capability_id)
            if not capability:
                return {"success": False, "error": "Capability not found"}

            health_score = 100
            issues = []
            recommendations = []

            # Check business ownership
            if not capability.business_owner:
                health_score -= 30
                issues.append("No business owner assigned")
                recommendations.append("Assign a business owner from executive level")

            # Check criticality assessment
            if not capability.business_criticality:
                health_score -= 20
                issues.append("No business criticality assessment")
                recommendations.append("Assess business criticality level")

            # Check strategic importance
            if not capability.strategic_importance:
                health_score -= 15
                issues.append("No strategic importance defined")
                recommendations.append("Define strategic importance level")

            # Check description
            if not capability.description:
                health_score -= 10
                issues.append("No capability description")
                recommendations.append("Add comprehensive capability description")

            # Check level
            if not capability.level:
                health_score -= 10
                issues.append("No capability level defined")
                recommendations.append("Define capability level (1 - 3)")

            # Determine health status
            if health_score >= 90:
                status = "Excellent"
            elif health_score >= 70:
                status = "Good"
            elif health_score >= 50:
                status = "Needs Attention"
            else:
                status = "Critical"

            return {
                "success": True,
                "health_check": {
                    "capability_id": capability_id,
                    "capability_name": capability.name,
                    "health_score": health_score,
                    "status": status,
                    "issues": issues,
                    "recommendations": recommendations,
                    "checked_at": datetime.utcnow(),
                },
            }

        except Exception as e:
            logger.error(f"Error performing health check: {e}")
            return {"success": False, "error": str(e)}
