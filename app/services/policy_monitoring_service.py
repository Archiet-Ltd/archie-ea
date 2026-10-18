"""
Policy Monitoring Service

Comprehensive service for architecture policy management, compliance scanning,
violation detection, and exemption workflow management.

Features:
- Policy CRUD operations
- Application compliance scanning
- Policy rule evaluation
- Violation lifecycle management
- Exemption workflow
- Compliance reporting and dashboard data
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import Integer, desc, func

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.policy_monitoring import (
    ArchitecturePolicy,
    ComplianceStatus,
    PolicyExemption,
    PolicyViolation,
)
from ..utils.tenant_sql import current_org_id

logger = logging.getLogger(__name__)


class PolicyMonitoringService:
    """
    Service class for policy monitoring and compliance management.

    Provides methods for:
    - Policy management (CRUD operations)
    - Compliance scanning
    - Violation management
    - Exemption workflow
    - Compliance reporting
    - Dashboard data aggregation
    """

    # =========================================================================
    # Policy Management
    # =========================================================================

    @staticmethod
    def get_all_policies(
        category: Optional[str] = None, is_active: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get all policies with optional filters.

        Args:
            category: Filter by policy category (technology, security, data, etc.)
            is_active: Filter by active status

        Returns:
            List of policy dictionaries with violation counts
        """
        try:
            query = ArchitecturePolicy.query

            if category:
                query = query.filter(ArchitecturePolicy.category == category)

            if is_active is not None:
                query = query.filter(ArchitecturePolicy.is_active == is_active)

            policies = query.order_by(ArchitecturePolicy.name).all()

            result = []
            for policy in policies:
                policy_dict = policy.to_dict()
                # Add violation count
                violation_count = PolicyViolation.query.filter(
                    PolicyViolation.policy_id == policy.id,
                    PolicyViolation.status.in_(["open", "acknowledged"]),
                ).count()
                policy_dict["open_violation_count"] = violation_count
                result.append(policy_dict)

            return result

        except Exception as e:
            logger.error(f"Error getting policies: {e}")
            return []

    @staticmethod
    def get_policy(policy_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a single policy with violation count.

        Args:
            policy_id: The policy ID

        Returns:
            Policy dictionary with violation count or None if not found
        """
        try:
            policy = ArchitecturePolicy.query.get(policy_id)
            if not policy:
                return None

            policy_dict = policy.to_dict()

            # Add violation counts by status
            violation_stats = (
                db.session.query(PolicyViolation.status, func.count(PolicyViolation.id))
                .filter(PolicyViolation.policy_id == policy_id)
                .group_by(PolicyViolation.status)
                .all()
            )

            policy_dict["violation_stats"] = {status: count for status, count in violation_stats}
            policy_dict["total_violations"] = sum(count for _, count in violation_stats)

            return policy_dict

        except Exception as e:
            logger.error(f"Error getting policy {policy_id}: {e}")
            return None

    @staticmethod
    def create_policy(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new architecture policy.

        Args:
            data: Policy data dictionary

        Returns:
            Dictionary with success status and created policy or error
        """
        try:
            # Validate required fields
            if not data.get("name"):
                return {"success": False, "error": "Policy name is required"}

            # Parse dates if provided as strings
            effective_date = data.get("effective_date")
            if effective_date and isinstance(effective_date, str):
                effective_date = datetime.strptime(effective_date, "%Y-%m-%d").date()

            expiry_date = data.get("expiry_date")
            if expiry_date and isinstance(expiry_date, str):
                expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()

            # Serialize rule_definition if it's a dict
            rule_definition = data.get("rule_definition")
            if rule_definition and isinstance(rule_definition, dict):
                rule_definition = json.dumps(rule_definition)

            policy = ArchitecturePolicy(
                name=data.get("name"),
                description=data.get("description"),
                category=data.get("category"),
                policy_type=data.get("policy_type"),
                scope=data.get("scope"),
                rule_definition=rule_definition,
                severity=data.get("severity", "medium"),
                enforcement_level=data.get("enforcement_level", "warning"),
                effective_date=effective_date,
                expiry_date=expiry_date,
                owner_id=data.get("owner_id"),
                is_active=data.get("is_active", True),
                exemption_allowed=data.get("exemption_allowed", True),
                exemption_approval_required=data.get("exemption_approval_required", True),
            )

            db.session.add(policy)
            db.session.commit()

            logger.info(f"Created policy: {policy.name}")
            return {"success": True, "policy": policy.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating policy: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_policy(policy_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing policy.

        Args:
            policy_id: The policy ID to update
            data: Updated policy data

        Returns:
            Dictionary with success status and updated policy or error
        """
        try:
            policy = ArchitecturePolicy.query.get(policy_id)
            if not policy:
                return {"success": False, "error": "Policy not found"}

            # Update fields
            updatable_fields = [
                "name",
                "description",
                "category",
                "policy_type",
                "scope",
                "severity",
                "enforcement_level",
                "owner_id",
                "is_active",
                "exemption_allowed",
                "exemption_approval_required",
            ]

            for field in updatable_fields:
                if field in data:
                    setattr(policy, field, data[field])

            # Handle rule_definition
            if "rule_definition" in data:
                rule_def = data["rule_definition"]
                if isinstance(rule_def, dict):
                    policy.rule_definition = json.dumps(rule_def)
                else:
                    policy.rule_definition = rule_def

            # Handle dates
            if "effective_date" in data:
                effective_date = data["effective_date"]
                if effective_date and isinstance(effective_date, str):
                    policy.effective_date = datetime.strptime(effective_date, "%Y-%m-%d").date()
                else:
                    policy.effective_date = effective_date

            if "expiry_date" in data:
                expiry_date = data["expiry_date"]
                if expiry_date and isinstance(expiry_date, str):
                    policy.expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
                else:
                    policy.expiry_date = expiry_date

            db.session.commit()

            logger.info(f"Updated policy: {policy.name}")
            return {"success": True, "policy": policy.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating policy {policy_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def deactivate_policy(policy_id: int) -> Dict[str, Any]:
        """
        Soft delete (deactivate) a policy.

        Args:
            policy_id: The policy ID to deactivate

        Returns:
            Dictionary with success status
        """
        try:
            policy = ArchitecturePolicy.query.get(policy_id)
            if not policy:
                return {"success": False, "error": "Policy not found"}

            policy.is_active = False
            db.session.commit()

            logger.info(f"Deactivated policy: {policy.name}")
            return {"success": True, "message": f"Policy '{policy.name}' deactivated"}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deactivating policy {policy_id}: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Compliance Scanning
    # =========================================================================

    @staticmethod
    def scan_application_compliance(application_id: int) -> Dict[str, Any]:
        """
        Check an application against all active policies.

        Args:
            application_id: The application ID to scan

        Returns:
            Dictionary with scan results and violations found
        """
        try:
            application = ApplicationComponent.query.get(application_id)
            if not application:
                return {"success": False, "error": "Application not found"}
            # Tenant scoping: ensure application belongs to current organisation
            if application.organization_id != current_org_id():
                return {"success": False, "error": "Application not found"}

            # Get all active and effective policies
            active_policies = ArchitecturePolicy.query.filter(
                ArchitecturePolicy.is_active == True
            ).all()

            effective_policies = [p for p in active_policies if p.is_effective()]

            violations_found = []
            compliant_count = 0

            for policy in effective_policies:
                result = PolicyMonitoringService.evaluate_policy_rule(policy, application)

                if not result["compliant"]:
                    # Check if violation already exists
                    existing_violation = PolicyViolation.query.filter(
                        PolicyViolation.policy_id == policy.id,
                        PolicyViolation.entity_type == "application",
                        PolicyViolation.entity_id == application.id,
                        PolicyViolation.status.in_(["open", "acknowledged"]),
                    ).first()

                    if not existing_violation:
                        # Create new violation
                        violation = PolicyViolation(
                            policy_id=policy.id,
                            entity_type="application",
                            entity_id=application.id,
                            entity_name=application.name,
                            violation_details=result.get("message", "Policy violation detected"),
                            severity=policy.severity,
                            status="open",
                            detected_at=datetime.utcnow(),
                        )
                        db.session.add(violation)
                        violations_found.append(
                            {
                                "policy_name": policy.name,
                                "severity": policy.severity,
                                "message": result.get("message"),
                            }
                        )
                else:
                    compliant_count += 1

            # Update or create compliance status
            compliance_status = ComplianceStatus.query.filter(
                ComplianceStatus.entity_type == "application",
                ComplianceStatus.entity_id == application.id,
            ).first()

            if not compliance_status:
                compliance_status = ComplianceStatus(
                    entity_type="application",
                    entity_id=application.id,
                    entity_name=application.name,
                )
                db.session.add(compliance_status)

            # Get current exemption count
            exemption_count = PolicyViolation.query.filter(
                PolicyViolation.entity_type == "application",
                PolicyViolation.entity_id == application.id,
                PolicyViolation.status == "exempted",
            ).count()

            compliance_status.total_policies = len(effective_policies)
            compliance_status.compliant_count = compliant_count
            compliance_status.violation_count = len(violations_found)
            compliance_status.exemption_count = exemption_count
            compliance_status.last_scan_at = datetime.utcnow()
            compliance_status.calculate_compliance_percentage()
            compliance_status.risk_score = PolicyMonitoringService._calculate_risk_score(
                application.id, "application"
            )

            db.session.commit()

            return {
                "success": True,
                "application_id": application.id,
                "application_name": application.name,
                "policies_checked": len(effective_policies),
                "violations_found": len(violations_found),
                "compliant_count": compliant_count,
                "compliance_percentage": compliance_status.compliance_percentage,
                "violations": violations_found,
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error scanning application {application_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def scan_all_applications() -> Dict[str, Any]:
        """
        Run compliance scan on all applications.

        Returns:
            Dictionary with overall scan results
        """
        try:
            applications = ApplicationComponent.query.filter_by(organization_id=current_org_id()).all()

            results = {
                "success": True,
                "applications_scanned": 0,
                "total_violations_found": 0,
                "applications_with_violations": 0,
                "scan_results": [],
            }

            for app in applications:
                scan_result = PolicyMonitoringService.scan_application_compliance(app.id)

                if scan_result.get("success"):
                    results["applications_scanned"] += 1
                    results["total_violations_found"] += scan_result.get("violations_found", 0)

                    if scan_result.get("violations_found", 0) > 0:
                        results["applications_with_violations"] += 1

                    results["scan_results"].append(
                        {
                            "application_id": app.id,
                            "application_name": app.name,
                            "violations": scan_result.get("violations_found", 0),
                            "compliance_percentage": scan_result.get("compliance_percentage", 0),
                        }
                    )

            # Update enterprise-level compliance
            PolicyMonitoringService.calculate_enterprise_compliance()

            logger.info(
                f"Completed scan of {results['applications_scanned']} applications, "
                f"found {results['total_violations_found']} violations"
            )

            return results

        except Exception as e:
            logger.error(f"Error scanning all applications: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def evaluate_policy_rule(
        policy: ArchitecturePolicy, entity: ApplicationComponent
    ) -> Dict[str, Any]:
        """
        Evaluate a single policy rule against an entity.

        Args:
            policy: The policy to evaluate
            entity: The application entity to check

        Returns:
            Dictionary with compliant status and message
        """
        try:
            if not policy.rule_definition:
                return {"compliant": True, "message": "No rule defined"}

            # Parse rule definition
            try:
                rule = json.loads(policy.rule_definition)
            except json.JSONDecodeError:
                logger.warning(f"Invalid rule definition for policy {policy.id}")
                return {"compliant": True, "message": "Invalid rule definition"}

            rule_type = rule.get("type", "")
            conditions = rule.get("conditions", [])

            # Evaluate based on rule type
            if rule_type == "technology_check":
                return PolicyMonitoringService._evaluate_technology_check(
                    entity, conditions, rule.get("message", "Technology policy violation")
                )
            elif rule_type == "security_check":
                return PolicyMonitoringService._evaluate_security_check(
                    entity, conditions, rule.get("message", "Security policy violation")
                )
            elif rule_type == "data_check":
                return PolicyMonitoringService._evaluate_data_check(
                    entity, conditions, rule.get("message", "Data policy violation")
                )
            elif rule_type == "integration_check":
                return PolicyMonitoringService._evaluate_integration_check(
                    entity, conditions, rule.get("message", "Integration policy violation")
                )
            else:
                return {"compliant": True, "message": f"Unknown rule type: {rule_type}"}

        except Exception as e:
            logger.error(f"Error evaluating policy rule: {e}")
            return {"compliant": True, "message": f"Rule evaluation error: {e}"}

    @staticmethod
    def _evaluate_technology_check(
        entity: ApplicationComponent, conditions: List[Dict], message: str
    ) -> Dict[str, Any]:
        """Evaluate technology-related policy conditions."""
        for condition in conditions:
            field = condition.get("field")
            operator = condition.get("operator")
            value = condition.get("value")

            entity_value = getattr(entity, field, None)

            # Parse JSON fields
            if field in [
                "technology_stack",
                "programming_languages",
                "frameworks",
                "database_platforms",
            ]:
                if entity_value:
                    try:
                        entity_value = json.loads(entity_value)
                    except (json.JSONDecodeError, TypeError):
                        entity_value = [entity_value] if entity_value else []
                else:
                    entity_value = []

            # Evaluate operators
            if operator == "not_contains":
                if isinstance(value, list):
                    for v in value:
                        if isinstance(entity_value, list):
                            if v.lower() in [ev.lower() for ev in entity_value if ev]:
                                return {"compliant": False, "message": f"{message} (found: {v})"}
                        elif entity_value and v.lower() in str(entity_value).lower():
                            return {"compliant": False, "message": f"{message} (found: {v})"}

            elif operator == "contains":
                if isinstance(value, list):
                    for v in value:
                        if isinstance(entity_value, list):
                            if v.lower() not in [ev.lower() for ev in entity_value if ev]:
                                return {"compliant": False, "message": f"{message} (missing: {v})"}
                        elif not entity_value or v.lower() not in str(entity_value).lower():
                            return {"compliant": False, "message": f"{message} (missing: {v})"}

            elif operator == "equals":
                if entity_value != value:
                    return {"compliant": False, "message": message}

            elif operator == "not_equals":
                if entity_value == value:
                    return {"compliant": False, "message": message}

        return {"compliant": True, "message": "Compliant"}

    @staticmethod
    def _evaluate_security_check(
        entity: ApplicationComponent, conditions: List[Dict], message: str
    ) -> Dict[str, Any]:
        """Evaluate security-related policy conditions."""
        for condition in conditions:
            field = condition.get("field")
            operator = condition.get("operator")
            value = condition.get("value")

            entity_value = getattr(entity, field, None)

            # Check required security controls
            if operator == "required" and value is True:
                if not entity_value:
                    return {"compliant": False, "message": f"{message} ({field} is required)"}

            elif operator == "equals":
                if entity_value != value:
                    return {"compliant": False, "message": message}

            elif operator == "min_level":
                # Security level comparison
                levels = {"low": 1, "medium": 2, "high": 3, "critical": 4}
                entity_level = levels.get(str(entity_value).lower(), 0)
                required_level = levels.get(str(value).lower(), 0)
                if entity_level < required_level:
                    return {
                        "compliant": False,
                        "message": f"{message} (requires {value}, has {entity_value})",
                    }

        return {"compliant": True, "message": "Compliant"}

    @staticmethod
    def _evaluate_data_check(
        entity: ApplicationComponent, conditions: List[Dict], message: str
    ) -> Dict[str, Any]:
        """Evaluate data classification policy conditions."""
        for condition in conditions:
            field = condition.get("field")
            operator = condition.get("operator")
            value = condition.get("value")

            entity_value = getattr(entity, field, None)

            if operator == "requires_classification":
                if not entity_value:
                    return {
                        "compliant": False,
                        "message": f"{message} (data classification required)",
                    }
                if isinstance(value, list) and entity_value not in value:
                    return {
                        "compliant": False,
                        "message": f"{message} (invalid classification: {entity_value})",
                    }

            elif operator == "equals":
                if entity_value != value:
                    return {"compliant": False, "message": message}

        return {"compliant": True, "message": "Compliant"}

    @staticmethod
    def _evaluate_integration_check(
        entity: ApplicationComponent, conditions: List[Dict], message: str
    ) -> Dict[str, Any]:
        """Evaluate integration pattern policy conditions."""
        for condition in conditions:
            field = condition.get("field")
            operator = condition.get("operator")
            value = condition.get("value")

            entity_value = getattr(entity, field, None)

            if operator == "allowed_patterns":
                if isinstance(value, list) and entity_value and entity_value not in value:
                    return {
                        "compliant": False,
                        "message": f"{message} (pattern {entity_value} not allowed)",
                    }

            elif operator == "requires_api":
                if value is True and not entity_value:
                    return {
                        "compliant": False,
                        "message": f"{message} (API documentation required)",
                    }

            elif operator == "max_integrations":
                if entity_value and int(entity_value) > int(value):
                    return {
                        "compliant": False,
                        "message": f"{message} (too many integrations: {entity_value})",
                    }

        return {"compliant": True, "message": "Compliant"}

    @staticmethod
    def _calculate_risk_score(entity_id: int, entity_type: str) -> float:
        """Calculate risk score based on violation severity (0 - 100)."""
        severity_weights = {"critical": 40, "high": 25, "medium": 10, "low": 5}

        violations = PolicyViolation.query.filter(
            PolicyViolation.entity_type == entity_type,
            PolicyViolation.entity_id == entity_id,
            PolicyViolation.status.in_(["open", "acknowledged"]),
        ).all()

        total_score = 0
        for violation in violations:
            total_score += severity_weights.get(violation.severity, 5)

        return min(total_score, 100)

    # =========================================================================
    # Violation Management
    # =========================================================================

    @staticmethod
    def get_violations(
        status_filter: Optional[str] = None, severity_filter: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get all violations with optional filters.

        Args:
            status_filter: Filter by status (open, acknowledged, etc.)
            severity_filter: Filter by severity (critical, high, etc.)
            limit: Maximum number of violations to return

        Returns:
            List of violation dictionaries
        """
        try:
            query = PolicyViolation.query

            if status_filter:
                query = query.filter(PolicyViolation.status == status_filter)

            if severity_filter:
                query = query.filter(PolicyViolation.severity == severity_filter)

            violations = query.order_by(desc(PolicyViolation.detected_at)).limit(limit).all()

            return [v.to_dict() for v in violations]

        except Exception as e:
            logger.error(f"Error getting violations: {e}")
            return []

    @staticmethod
    def get_violations_for_entity(entity_type: str, entity_id: int) -> List[Dict[str, Any]]:
        """
        Get all violations for a specific entity.

        Args:
            entity_type: Type of entity (application, capability, etc.)
            entity_id: ID of the entity

        Returns:
            List of violation dictionaries
        """
        try:
            violations = (
                PolicyViolation.query.filter(
                    PolicyViolation.entity_type == entity_type,
                    PolicyViolation.entity_id == entity_id,
                )
                .order_by(desc(PolicyViolation.detected_at))
                .all()
            )

            return [v.to_dict() for v in violations]

        except Exception as e:
            logger.error(f"Error getting violations for {entity_type}:{entity_id}: {e}")
            return []

    @staticmethod
    def acknowledge_violation(violation_id: int, user_id: int) -> Dict[str, Any]:
        """
        Mark a violation as acknowledged.

        Args:
            violation_id: The violation ID
            user_id: ID of the user acknowledging

        Returns:
            Dictionary with success status
        """
        try:
            violation = PolicyViolation.query.get(violation_id)
            if not violation:
                return {"success": False, "error": "Violation not found"}

            violation.status = "acknowledged"
            violation.acknowledged_by = user_id
            violation.acknowledged_at = datetime.utcnow()

            db.session.commit()

            logger.info(f"Violation {violation_id} acknowledged by user {user_id}")
            return {"success": True, "violation": violation.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error acknowledging violation {violation_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def remediate_violation(violation_id: int, notes: str) -> Dict[str, Any]:
        """
        Mark a violation as remediated.

        Args:
            violation_id: The violation ID
            notes: Remediation notes

        Returns:
            Dictionary with success status
        """
        try:
            violation = PolicyViolation.query.get(violation_id)
            if not violation:
                return {"success": False, "error": "Violation not found"}

            violation.status = "remediated"
            violation.remediated_at = datetime.utcnow()
            violation.remediation_notes = notes

            db.session.commit()

            logger.info(f"Violation {violation_id} marked as remediated")
            return {"success": True, "violation": violation.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error remediating violation {violation_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def mark_false_positive(violation_id: int, reason: str, user_id: int) -> Dict[str, Any]:
        """
        Mark a violation as a false positive.

        Args:
            violation_id: The violation ID
            reason: Reason for marking as false positive
            user_id: ID of the user marking

        Returns:
            Dictionary with success status
        """
        try:
            violation = PolicyViolation.query.get(violation_id)
            if not violation:
                return {"success": False, "error": "Violation not found"}

            violation.status = "false_positive"
            violation.remediation_notes = f"Marked as false positive: {reason}"
            violation.acknowledged_by = user_id
            violation.acknowledged_at = datetime.utcnow()

            db.session.commit()

            logger.info(f"Violation {violation_id} marked as false positive")
            return {"success": True, "violation": violation.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error marking violation {violation_id} as false positive: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Exemption Workflow
    # =========================================================================

    @staticmethod
    def request_exemption(violation_id: int, user_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Request an exemption for a violation.

        Args:
            violation_id: The violation ID
            user_id: ID of the requesting user
            data: Exemption request data

        Returns:
            Dictionary with success status and exemption details
        """
        try:
            violation = PolicyViolation.query.get(violation_id)
            if not violation:
                return {"success": False, "error": "Violation not found"}

            # Check if exemption is allowed
            policy = violation.policy
            if not policy.exemption_allowed:
                return {"success": False, "error": "Exemptions not allowed for this policy"}

            # Check for existing pending exemption
            existing = PolicyExemption.query.filter(
                PolicyExemption.violation_id == violation_id,
                PolicyExemption.status == "pending",
            ).first()

            if existing:
                return {"success": False, "error": "Pending exemption already exists"}

            # Parse expiry date
            expiry_date = data.get("expiry_date")
            if expiry_date and isinstance(expiry_date, str):
                expiry_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()

            exemption = PolicyExemption(
                violation_id=violation_id,
                requested_by=user_id,
                reason=data.get("reason"),
                business_justification=data.get("business_justification"),
                mitigation_plan=data.get("mitigation_plan"),
                expiry_date=expiry_date,
                status="pending",
            )

            db.session.add(exemption)
            db.session.commit()

            logger.info(f"Exemption requested for violation {violation_id} by user {user_id}")
            return {"success": True, "exemption": exemption.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error requesting exemption: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def approve_exemption(exemption_id: int, approver_id: int) -> Dict[str, Any]:
        """
        Approve an exemption request.

        Args:
            exemption_id: The exemption ID
            approver_id: ID of the approving user

        Returns:
            Dictionary with success status
        """
        try:
            exemption = PolicyExemption.query.get(exemption_id)
            if not exemption:
                return {"success": False, "error": "Exemption not found"}

            if exemption.status != "pending":
                return {"success": False, "error": "Exemption is not pending"}

            exemption.status = "approved"
            exemption.approved_by = approver_id
            exemption.approved_at = datetime.utcnow()

            # Update violation status
            violation = exemption.violation
            violation.status = "exempted"
            violation.exemption_reason = exemption.reason
            violation.exemption_approved_by = approver_id
            violation.exemption_expiry = exemption.expiry_date

            db.session.commit()

            logger.info(f"Exemption {exemption_id} approved by user {approver_id}")
            return {"success": True, "exemption": exemption.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error approving exemption {exemption_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def reject_exemption(exemption_id: int, approver_id: int, reason: str) -> Dict[str, Any]:
        """
        Reject an exemption request.

        Args:
            exemption_id: The exemption ID
            approver_id: ID of the rejecting user
            reason: Reason for rejection

        Returns:
            Dictionary with success status
        """
        try:
            exemption = PolicyExemption.query.get(exemption_id)
            if not exemption:
                return {"success": False, "error": "Exemption not found"}

            if exemption.status != "pending":
                return {"success": False, "error": "Exemption is not pending"}

            exemption.status = "rejected"
            exemption.approved_by = approver_id
            exemption.approved_at = datetime.utcnow()
            exemption.rejection_reason = reason

            db.session.commit()

            logger.info(f"Exemption {exemption_id} rejected by user {approver_id}")
            return {"success": True, "exemption": exemption.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error rejecting exemption {exemption_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_pending_exemptions() -> List[Dict[str, Any]]:
        """
        Get all pending exemption requests.

        Returns:
            List of pending exemption dictionaries
        """
        try:
            exemptions = (
                PolicyExemption.query.filter(PolicyExemption.status == "pending")
                .order_by(PolicyExemption.requested_at)
                .all()
            )

            result = []
            for exemption in exemptions:
                exemption_dict = exemption.to_dict()
                # Add violation and policy details
                if exemption.violation:
                    exemption_dict["violation_details"] = exemption.violation.violation_details
                    exemption_dict["entity_name"] = exemption.violation.entity_name
                    if exemption.violation.policy:
                        exemption_dict["policy_name"] = exemption.violation.policy.name
                        exemption_dict["policy_severity"] = exemption.violation.policy.severity
                result.append(exemption_dict)

            return result

        except Exception as e:
            logger.error(f"Error getting pending exemptions: {e}")
            return []

    @staticmethod
    def check_expired_exemptions() -> Dict[str, Any]:
        """
        Find and flag expired exemptions.

        Returns:
            Dictionary with count of expired exemptions
        """
        try:
            today = datetime.utcnow().date()

            # Find violations with expired exemptions
            expired_violations = PolicyViolation.query.filter(
                PolicyViolation.status == "exempted",
                PolicyViolation.exemption_expiry < today,
            ).all()

            expired_count = 0
            for violation in expired_violations:
                violation.status = "open"  # Reopen the violation
                violation.remediation_notes = (
                    f"Exemption expired on {violation.exemption_expiry}. "
                    f"Previous exemption reason: {violation.exemption_reason}"
                )
                expired_count += 1

            if expired_count > 0:
                db.session.commit()

            logger.info(f"Checked expired exemptions: {expired_count} violations reopened")
            return {"success": True, "expired_count": expired_count}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error checking expired exemptions: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Compliance Reporting
    # =========================================================================

    @staticmethod
    def get_compliance_status(
        entity_type: Optional[str] = None, entity_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get compliance status for entities.

        Args:
            entity_type: Filter by entity type
            entity_id: Filter by specific entity ID

        Returns:
            List of compliance status dictionaries
        """
        try:
            query = ComplianceStatus.query

            if entity_type:
                query = query.filter(ComplianceStatus.entity_type == entity_type)

            if entity_id is not None:
                query = query.filter(ComplianceStatus.entity_id == entity_id)

            statuses = query.order_by(desc(ComplianceStatus.risk_score)).all()
            return [s.to_dict() for s in statuses]

        except Exception as e:
            logger.error(f"Error getting compliance status: {e}")
            return []

    @staticmethod
    def calculate_enterprise_compliance() -> Dict[str, Any]:
        """
        Calculate overall enterprise compliance.

        Returns:
            Dictionary with enterprise compliance metrics
        """
        try:
            # Aggregate from all application compliance statuses
            app_statuses = ComplianceStatus.query.filter(
                ComplianceStatus.entity_type == "application"
            ).all()

            if not app_statuses:
                # M1: no applications scanned yet is "not measured", not "fully
                # compliant" -- a fabricated 100% here is indistinguishable from
                # a real scan finding zero violations. Callers must render this
                # None as an em dash, never as a number.
                return {
                    "success": True,
                    "total_policies": 0,
                    "compliance_percentage": None,
                    "risk_score": None,
                }

            total_policies = sum(s.total_policies for s in app_statuses)
            total_compliant = sum(s.compliant_count + s.exemption_count for s in app_statuses)
            total_violations = sum(s.violation_count for s in app_statuses)
            avg_risk = sum(s.risk_score for s in app_statuses) / len(app_statuses)

            compliance_pct = (
                (total_compliant / total_policies * 100) if total_policies > 0 else None
            )

            # Update or create enterprise-level status
            enterprise_status = ComplianceStatus.query.filter(
                ComplianceStatus.entity_type == "enterprise",
                ComplianceStatus.entity_id == None,
            ).first()

            if not enterprise_status:
                enterprise_status = ComplianceStatus(
                    entity_type="enterprise",
                    entity_id=None,
                    entity_name="Enterprise",
                )
                db.session.add(enterprise_status)

            enterprise_status.total_policies = total_policies
            enterprise_status.compliant_count = total_compliant
            enterprise_status.violation_count = total_violations
            enterprise_status.compliance_percentage = compliance_pct
            enterprise_status.risk_score = avg_risk
            enterprise_status.last_scan_at = datetime.utcnow()

            db.session.commit()

            return {
                "success": True,
                "total_policies": total_policies,
                "compliant_count": total_compliant,
                "violation_count": total_violations,
                "compliance_percentage": None if compliance_pct is None else round(compliance_pct, 2),
                "risk_score": round(avg_risk, 2),
                "applications_evaluated": len(app_statuses),
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error calculating enterprise compliance: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_compliance_trend(days: int = 90) -> List[Dict[str, Any]]:
        """
        Get compliance trend over time.

        Args:
            days: Number of days to look back

        Returns:
            List of trend data points
        """
        try:
            start_date = datetime.utcnow() - timedelta(days=days)

            # Get violations created by date
            violations_by_date = (
                db.session.query(
                    func.date(PolicyViolation.detected_at).label("date"),
                    func.count(PolicyViolation.id).label("count"),
                )
                .filter(PolicyViolation.detected_at >= start_date)
                .group_by(func.date(PolicyViolation.detected_at))
                .order_by(func.date(PolicyViolation.detected_at))
                .all()
            )

            # Get remediations by date
            remediations_by_date = (
                db.session.query(
                    func.date(PolicyViolation.remediated_at).label("date"),
                    func.count(PolicyViolation.id).label("count"),
                )
                .filter(
                    PolicyViolation.remediated_at >= start_date,
                    PolicyViolation.status == "remediated",
                )
                .group_by(func.date(PolicyViolation.remediated_at))
                .order_by(func.date(PolicyViolation.remediated_at))
                .all()
            )

            trend_data = []
            violations_dict = {str(d.date): d.count for d in violations_by_date}
            remediations_dict = {str(d.date): d.count for d in remediations_by_date}

            current_date = start_date.date()
            end_date = datetime.utcnow().date()

            while current_date <= end_date:
                date_str = str(current_date)
                trend_data.append(
                    {
                        "date": date_str,
                        "violations_detected": violations_dict.get(date_str, 0),
                        "violations_remediated": remediations_dict.get(date_str, 0),
                    }
                )
                current_date += timedelta(days=1)

            return trend_data

        except Exception as e:
            logger.error(f"Error getting compliance trend: {e}")
            return []

    @staticmethod
    def get_policy_effectiveness() -> List[Dict[str, Any]]:
        """
        Get policy effectiveness metrics (which policies have most violations).

        Returns:
            List of policy effectiveness data
        """
        try:
            # Get violation counts by policy
            policy_stats = (
                db.session.query(
                    ArchitecturePolicy.id,
                    ArchitecturePolicy.name,
                    ArchitecturePolicy.category,
                    ArchitecturePolicy.severity,
                    func.count(PolicyViolation.id).label("total_violations"),
                    func.sum(func.cast(PolicyViolation.status == "remediated", Integer)).label(
                        "remediated_count"
                    ),
                )
                .outerjoin(PolicyViolation, PolicyViolation.policy_id == ArchitecturePolicy.id)
                .filter(ArchitecturePolicy.is_active == True)
                .group_by(
                    ArchitecturePolicy.id,
                    ArchitecturePolicy.name,
                    ArchitecturePolicy.category,
                    ArchitecturePolicy.severity,
                )
                .order_by(desc("total_violations"))
                .all()
            )

            result = []
            for stat in policy_stats:
                total = stat.total_violations or 0
                remediated = stat.remediated_count or 0
                remediation_rate = (remediated / total * 100) if total > 0 else 0

                result.append(
                    {
                        "policy_id": stat.id,
                        "policy_name": stat.name,
                        "category": stat.category,
                        "severity": stat.severity,
                        "total_violations": total,
                        "remediated_count": remediated,
                        "open_count": total - remediated,
                        "remediation_rate": round(remediation_rate, 2),
                    }
                )

            return result

        except Exception as e:
            logger.error(f"Error getting policy effectiveness: {e}")
            return []

    # =========================================================================
    # Dashboard Data
    # =========================================================================

    @staticmethod
    def get_policy_monitoring_dashboard() -> Dict[str, Any]:
        """
        Get all data needed for the policy monitoring dashboard.

        Returns:
            Dictionary with comprehensive dashboard data
        """
        try:
            # Policy counts by category
            policy_by_category = (
                db.session.query(
                    ArchitecturePolicy.category,
                    func.count(ArchitecturePolicy.id).label("count"),
                )
                .filter(ArchitecturePolicy.is_active == True)
                .group_by(ArchitecturePolicy.category)
                .all()
            )

            # Violation counts by status
            violations_by_status = (
                db.session.query(
                    PolicyViolation.status,
                    func.count(PolicyViolation.id).label("count"),
                )
                .group_by(PolicyViolation.status)
                .all()
            )

            # Violation counts by severity
            violations_by_severity = (
                db.session.query(
                    PolicyViolation.severity,
                    func.count(PolicyViolation.id).label("count"),
                )
                .filter(PolicyViolation.status.in_(["open", "acknowledged"]))
                .group_by(PolicyViolation.severity)
                .all()
            )

            # Get enterprise compliance
            enterprise_compliance = PolicyMonitoringService.calculate_enterprise_compliance()

            # Recent violations (last 10)
            recent_violations = (
                PolicyViolation.query.order_by(desc(PolicyViolation.detected_at)).limit(10).all()
            )

            # Pending exemptions
            pending_exemptions = PolicyMonitoringService.get_pending_exemptions()

            # Top violating policies
            top_policies = PolicyMonitoringService.get_policy_effectiveness()[:5]

            # Critical/High open violations
            critical_violations = PolicyViolation.query.filter(
                PolicyViolation.status.in_(["open", "acknowledged"]),
                PolicyViolation.severity.in_(["critical", "high"]),
            ).count()

            return {
                "success": True,
                "summary": {
                    "total_policies": sum(c.count for c in policy_by_category),
                    "policies_by_category": {c.category: c.count for c in policy_by_category},
                    "total_violations": sum(s.count for s in violations_by_status),
                    "violations_by_status": {s.status: s.count for s in violations_by_status},
                    "violations_by_severity": {s.severity: s.count for s in violations_by_severity},
                    "compliance_percentage": enterprise_compliance.get("compliance_percentage", 0),
                    "enterprise_risk_score": enterprise_compliance.get("risk_score", 0),
                    "critical_violations": critical_violations,
                    "pending_exemptions": len(pending_exemptions),
                },
                "recent_violations": [v.to_dict() for v in recent_violations],
                "pending_exemptions": pending_exemptions[:5],
                "top_violating_policies": top_policies,
                "last_updated": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error getting dashboard data: {e}")
            return {"success": False, "error": str(e)}
