"""
Consolidation Service

Provides comprehensive application consolidation management including:
- Duplicate candidate detection using multiple methods
- Consolidation opportunity lifecycle management
- Savings estimation and tracking
- Analytics and forecasting

No external NLP libraries required - uses simple token-based matching.
"""

import json
import logging
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from flask import has_request_context

from .. import db
from ..models.application_portfolio import ApplicationComponent
from ..models.consolidation import (
    ConsolidationCandidate,
    ConsolidationOpportunity,
    SavingsRealization,
)
from ..utils.tenant import current_organization_id

logger = logging.getLogger(__name__)


def _written_by_org():
    """The organisation a new row is written for: the active one, never request input."""
    if not has_request_context():
        return {}
    org_id = current_organization_id()
    return {} if org_id is None else {"organization_id": org_id}


def _mine(model):
    """A query on ``model`` limited to the active organisation.

    The tenant filter on these models skips a row already held by the session, so
    each read names the organisation too. Inside a request with no organisation it
    matches nothing; outside a request (command line, scheduler) it is unscoped.
    """
    if not has_request_context():
        return model.query
    org_id = current_organization_id()
    if org_id is None:
        return model.query.filter(db.false())
    return model.query.filter(model.organization_id == org_id)


class ConsolidationService:
    """
    Consolidation Service

    Manages the full lifecycle of application consolidation from
    candidate detection through savings realization tracking.
    """

    # Minimum similarity thresholds
    NAME_SIMILARITY_THRESHOLD = 0.85

    # Methods that once proposed a pair from a shared domain or vendor alone
    UNSOUND_METHODS = ("function_overlap", "vendor_match")

    # Savings estimation factors
    INFRASTRUCTURE_SAVINGS_FACTOR = 0.30  # 30% of hosting/infrastructure costs
    SUPPORT_SAVINGS_FACTOR = 0.40  # 40% reduction in support costs

    # =========================================================================
    # Candidate Detection Methods
    # =========================================================================

    @staticmethod
    def detect_duplicate_candidates() -> Dict[str, Any]:
        """
        Scan the organisation's applications and propose pairs that overlap.

        A pair is proposed only with evidence a person can read: the two support
        the same business capability, share a business function, or carry a
        near-identical name. A shared domain, category or vendor alone is not
        overlap. The evidence is stored with the candidate as its reason.

        A candidate still waiting for review, proposed by a method that once
        paired on a shared domain or vendor alone, and no longer supported, is
        dismissed with that explanation, so an earlier unfounded proposal clears
        itself on the next run.

        Returns:
            dict with detection results and statistics
        """
        try:
            applications = ApplicationComponent.query.all()
            total_apps = len(applications)

            if total_apps < 2:
                return {
                    "success": True,
                    "candidates_found": 0,
                    "message": "Not enough applications to compare",
                    "by_method": {},
                }

            capabilities = ConsolidationService._capabilities_by_application()
            candidates_found = 0
            candidates_dismissed = 0
            by_method = {"name_match": 0, "capability_overlap": 0, "function_overlap": 0}

            for i, app1 in enumerate(applications):
                for app2 in applications[i + 1 :]:
                    existing = _mine(ConsolidationCandidate).filter(
                        db.or_(
                            db.and_(
                                ConsolidationCandidate.primary_application_id == app1.id,
                                ConsolidationCandidate.duplicate_application_id == app2.id,
                            ),
                            db.and_(
                                ConsolidationCandidate.primary_application_id == app2.id,
                                ConsolidationCandidate.duplicate_application_id == app1.id,
                            ),
                        )
                    ).first()

                    detection_result = ConsolidationService._detect_similarity(
                        app1, app2, capabilities
                    )

                    if existing:
                        if (
                            detection_result is None
                            and existing.status == "pending_review"
                            and existing.detection_method in ConsolidationService.UNSOUND_METHODS
                        ):
                            existing.status = "rejected"
                            existing.notes = (
                                "Dismissed: the two applications share no capability, "
                                "business function or name, so nothing supports the pairing."
                            )
                            existing.reviewed_at = datetime.utcnow()
                            candidates_dismissed += 1
                        continue

                    if detection_result:
                        method, score, reason = detection_result
                        candidate = ConsolidationCandidate(
                            **_written_by_org(),
                            primary_application_id=app1.id,
                            duplicate_application_id=app2.id,
                            similarity_score=score,
                            detection_method=method,
                            notes=reason,
                            status="pending_review",
                            detected_at=datetime.utcnow(),
                        )
                        db.session.add(candidate)
                        candidates_found += 1
                        by_method[method] = by_method.get(method, 0) + 1

            db.session.commit()

            logger.info(
                "Duplicate detection completed: %d candidates found, %d dismissed",
                candidates_found,
                candidates_dismissed,
            )
            return {
                "success": True,
                "candidates_found": candidates_found,
                "candidates_dismissed": candidates_dismissed,
                "applications_scanned": total_apps,
                "by_method": by_method,
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Duplicate detection failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "candidates_found": 0,
            }

    @staticmethod
    def _capabilities_by_application() -> Dict[int, Dict[int, str]]:
        """{application id: {capability id: capability name}} for the active organisation."""
        from ..models.application_capability import ApplicationCapabilityMapping
        from ..models.business_capabilities import BusinessCapability

        query = (
            db.session.query(
                ApplicationCapabilityMapping.application_component_id,
                BusinessCapability.id,
                BusinessCapability.name,
            )
            .join(
                BusinessCapability,
                BusinessCapability.id == ApplicationCapabilityMapping.business_capability_id,
            )
            .filter(ApplicationCapabilityMapping.is_active.is_(True))
        )
        if has_request_context():
            org_id = current_organization_id()
            if org_id is None:
                return {}
            query = query.filter(ApplicationCapabilityMapping.organization_id == org_id)
        found: Dict[int, Dict[int, str]] = {}
        for app_id, cap_id, cap_name in query.all():
            found.setdefault(app_id, {})[cap_id] = cap_name
        return found

    @staticmethod
    def _detect_similarity(
        app1: ApplicationComponent,
        app2: ApplicationComponent,
        capabilities: Optional[Dict[int, Dict[int, str]]] = None,
    ) -> Optional[Tuple[str, float, str]]:
        """
        Decide whether two applications overlap.

        Returns (detection_method, similarity_score, reason) or None when there is
        no overlap. The score is the share of what the two have in common, never a
        stand-in for "same domain".
        """
        if capabilities is None:
            capabilities = ConsolidationService._capabilities_by_application()

        # Method 1: shared business capability
        caps1 = capabilities.get(app1.id, {})
        caps2 = capabilities.get(app2.id, {})
        shared = set(caps1) & set(caps2)
        if shared:
            score = len(shared) / len(set(caps1) | set(caps2))
            names = ", ".join(sorted(caps1[c] for c in shared))
            return (
                "capability_overlap",
                round(score, 2),
                "Both support the business capability " + names + ".",
            )

        # Method 2: shared business function
        shared_functions = ConsolidationService._shared_business_functions(app1, app2)
        if shared_functions:
            return (
                "function_overlap",
                shared_functions[1],
                "Both perform the business function " + ", ".join(shared_functions[0]) + ".",
            )

        # Method 3: near-identical name
        name_score = ConsolidationService._calculate_name_similarity(app1.name, app2.name)
        if name_score >= ConsolidationService.NAME_SIMILARITY_THRESHOLD:
            return (
                "name_match",
                round(name_score, 2),
                "The names " + app1.name + " and " + app2.name + " are near identical.",
            )

        return None

    @staticmethod
    def _shared_business_functions(
        app1: ApplicationComponent, app2: ApplicationComponent
    ) -> Optional[Tuple[List[str], float]]:
        """(shared function names, share of all functions they have in common) or None."""
        funcs1 = app1.get_business_functions() if hasattr(app1, "get_business_functions") else []
        funcs2 = app2.get_business_functions() if hasattr(app2, "get_business_functions") else []
        first = {f.strip().lower(): f.strip() for f in funcs1 or [] if isinstance(f, str) and f.strip()}
        second = {f.strip().lower() for f in funcs2 or [] if isinstance(f, str) and f.strip()}
        shared = set(first) & second
        if not shared:
            return None
        return sorted(first[k] for k in shared), round(len(shared) / len(set(first) | second), 2)

    @staticmethod
    def _calculate_name_similarity(name1: str, name2: str) -> float:
        """
        Calculate name similarity using token-based matching.

        Uses both sequence matching and word overlap for comprehensive comparison.
        """
        if not name1 or not name2:
            return 0.0

        name1_normalized = name1.lower().strip()
        name2_normalized = name2.lower().strip()

        # Exact match
        if name1_normalized == name2_normalized:
            return 1.0

        # Sequence matcher (handles typos and variations)
        sequence_ratio = SequenceMatcher(None, name1_normalized, name2_normalized).ratio()

        # Word overlap (Jaccard similarity)
        words1 = set(name1_normalized.split())
        words2 = set(name2_normalized.split())

        if words1 and words2:
            intersection = words1.intersection(words2)
            union = words1.union(words2)
            jaccard_ratio = len(intersection) / len(union) if union else 0.0
        else:
            jaccard_ratio = 0.0

        # Substring matching
        contains_ratio = 0.0
        if name1_normalized in name2_normalized or name2_normalized in name1_normalized:
            shorter = min(len(name1_normalized), len(name2_normalized))
            longer = max(len(name1_normalized), len(name2_normalized))
            contains_ratio = shorter / longer if longer > 0 else 0.0

        # Combined score
        combined_score = (sequence_ratio * 0.5) + (jaccard_ratio * 0.3) + (contains_ratio * 0.2)
        return min(combined_score, 1.0)

    @staticmethod
    def get_all_candidates(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all consolidation candidates, optionally filtered by status.

        Args:
            status_filter: Optional status to filter by (pending_review, approved, rejected, merged)

        Returns:
            List of candidate dictionaries
        """
        try:
            query = _mine(ConsolidationCandidate).order_by(
                ConsolidationCandidate.similarity_score.desc()
            )

            if status_filter:
                query = query.filter(ConsolidationCandidate.status == status_filter)

            candidates = query.all()
            return [c.to_dict() for c in candidates]

        except Exception as e:
            logger.error(f"Error getting candidates: {e}")
            return []

    @staticmethod
    def review_candidate(
        candidate_id: int,
        action: str,
        notes: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Review a consolidation candidate (approve or reject).

        Args:
            candidate_id: ID of the candidate to review
            action: Review action - 'approve' or 'reject'
            notes: Optional review notes
            user_id: ID of the reviewing user

        Returns:
            dict with result
        """
        try:
            candidate = _mine(ConsolidationCandidate).filter(ConsolidationCandidate.id == candidate_id).first()
            if not candidate:
                return {"success": False, "error": "Candidate not found"}

            if action == "approve":
                candidate.status = "approved"
            elif action == "reject":
                candidate.status = "rejected"
            else:
                return {"success": False, "error": f"Invalid action: {action}"}

            candidate.notes = notes
            candidate.reviewed_by = user_id
            candidate.reviewed_at = datetime.utcnow()

            db.session.commit()

            logger.info(f"Candidate {candidate_id} {action}d by user {user_id}")
            return {"success": True, "candidate": candidate.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error reviewing candidate: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Opportunity Management Methods
    # =========================================================================

    @staticmethod
    def create_opportunity_from_candidates(
        candidate_ids: List[int], data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a consolidation opportunity from approved candidates.

        Args:
            candidate_ids: List of candidate IDs to include
            data: Opportunity data including name, description, target_application_id, etc.

        Returns:
            dict with created opportunity or error
        """
        try:
            # Validate candidates
            candidates = _mine(ConsolidationCandidate).filter(
                ConsolidationCandidate.id.in_(candidate_ids)
            ).all()

            if not candidates:
                return {"success": False, "error": "No valid candidates found"}

            # Collect all unique application IDs
            app_ids = set()
            for c in candidates:
                app_ids.add(c.primary_application_id)
                app_ids.add(c.duplicate_application_id)

            # Determine target application (use provided or first primary)
            target_app_id = (
                data.get("target_application_id") or candidates[0].primary_application_id
            )
            source_app_ids = [aid for aid in app_ids if aid != target_app_id]

            # Create opportunity
            opportunity = ConsolidationOpportunity(
                **_written_by_org(),
                name=data.get(
                    "name", f"Consolidation Opportunity {datetime.now().strftime('%Y%m%d%H%M')}"
                ),
                description=data.get("description", ""),
                status=data.get("status", "identified"),
                priority=data.get("priority", "medium"),
                target_application_id=target_app_id,
                source_applications=json.dumps(source_app_ids),
                risk_level=data.get("risk_level", "medium"),
                complexity=data.get("complexity", "moderate"),
                business_impact=data.get("business_impact", ""),
                owner_id=data.get("owner_id"),
            )

            if data.get("start_date"):
                opportunity.start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()

            if data.get("target_completion_date"):
                opportunity.target_completion_date = datetime.strptime(
                    data["target_completion_date"], "%Y-%m-%d"
                ).date()

            db.session.add(opportunity)

            # Mark candidates as merged
            for candidate in candidates:
                candidate.status = "merged"

            db.session.commit()

            # Calculate initial savings estimate
            ConsolidationService.calculate_savings(opportunity.id)

            logger.info(f"Created consolidation opportunity: {opportunity.name}")
            return {"success": True, "opportunity": opportunity.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating opportunity: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_all_opportunities(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all consolidation opportunities, optionally filtered by status.

        Args:
            status_filter: Optional status to filter by

        Returns:
            List of opportunity dictionaries
        """
        try:
            query = _mine(ConsolidationOpportunity).order_by(
                ConsolidationOpportunity.created_at.desc()
            )

            if status_filter:
                query = query.filter(ConsolidationOpportunity.status == status_filter)

            opportunities = query.all()
            return [o.to_dict() for o in opportunities]

        except Exception as e:
            logger.error(f"Error getting opportunities: {e}")
            return []

    @staticmethod
    def get_opportunity(opportunity_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a single opportunity with full details.

        Args:
            opportunity_id: ID of the opportunity

        Returns:
            Opportunity dictionary or None
        """
        try:
            opportunity = _mine(ConsolidationOpportunity).filter(ConsolidationOpportunity.id == opportunity_id).first()
            if not opportunity:
                return None

            result = opportunity.to_dict()

            # Add source application details
            source_ids = opportunity.get_source_application_ids()
            source_apps = (
                ApplicationComponent.query.filter(ApplicationComponent.id.in_(source_ids)).all()
                if source_ids
                else []
            )

            result["source_applications"] = [
                {"id": app.id, "name": app.name, "vendor_name": app.vendor_name}
                for app in source_apps
            ]

            # Add savings realizations
            result["savings_realizations"] = [s.to_dict() for s in opportunity.savings_realizations]

            # Calculate total realized savings
            result["total_realized_savings"] = sum(
                s.realized_savings for s in opportunity.savings_realizations
            )

            return result

        except Exception as e:
            logger.error(f"Error getting opportunity: {e}")
            return None

    @staticmethod
    def update_opportunity(opportunity_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a consolidation opportunity.

        Args:
            opportunity_id: ID of the opportunity to update
            data: Updated data

        Returns:
            dict with result
        """
        try:
            opportunity = _mine(ConsolidationOpportunity).filter(ConsolidationOpportunity.id == opportunity_id).first()
            if not opportunity:
                return {"success": False, "error": "Opportunity not found"}

            # Update fields
            updatable_fields = [
                "name",
                "description",
                "status",
                "priority",
                "target_application_id",
                "estimated_annual_savings",
                "estimated_one_time_savings",
                "implementation_cost",
                "risk_level",
                "complexity",
                "business_impact",
                "owner_id",
            ]

            for field in updatable_fields:
                if field in data:
                    setattr(opportunity, field, data[field])

            # Handle date fields
            if "start_date" in data:
                opportunity.start_date = (
                    datetime.strptime(data["start_date"], "%Y-%m-%d").date()
                    if data["start_date"]
                    else None
                )

            if "target_completion_date" in data:
                opportunity.target_completion_date = (
                    datetime.strptime(data["target_completion_date"], "%Y-%m-%d").date()
                    if data["target_completion_date"]
                    else None
                )

            if "actual_completion_date" in data:
                opportunity.actual_completion_date = (
                    datetime.strptime(data["actual_completion_date"], "%Y-%m-%d").date()
                    if data["actual_completion_date"]
                    else None
                )

            # Handle JSON fields
            if "source_application_ids" in data:
                opportunity.set_source_application_ids(data["source_application_ids"])

            if "technical_dependencies" in data:
                opportunity.set_technical_dependencies_list(data["technical_dependencies"])

            db.session.commit()

            # Recalculate savings if financial data changed
            if any(f in data for f in ["estimated_annual_savings", "implementation_cost"]):
                ConsolidationService._update_calculated_fields(opportunity)

            logger.info(f"Updated opportunity {opportunity_id}")
            return {"success": True, "opportunity": opportunity.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating opportunity: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def calculate_savings(opportunity_id: int) -> Dict[str, Any]:
        """
        Calculate estimated savings for an opportunity based on source applications.

        Savings calculation:
        - License savings = sum of license costs from source applications
        - Infrastructure savings = 30% of hosting/infrastructure costs
        - Support savings = 40% reduction in support costs
        - ROI = (annual_savings - implementation_cost) / implementation_cost * 100
        - Payback period = implementation_cost / (annual_savings / 12) in months

        Args:
            opportunity_id: ID of the opportunity

        Returns:
            dict with calculated savings
        """
        try:
            opportunity = _mine(ConsolidationOpportunity).filter(ConsolidationOpportunity.id == opportunity_id).first()
            if not opportunity:
                return {"success": False, "error": "Opportunity not found"}

            # Get source applications
            source_ids = opportunity.get_source_application_ids()
            source_apps = (
                ApplicationComponent.query.filter(ApplicationComponent.id.in_(source_ids)).all()
                if source_ids
                else []
            )

            # Calculate savings by category
            license_savings = 0.0
            infrastructure_savings = 0.0
            support_savings = 0.0
            maintenance_savings = 0.0
            one_time_savings = 0.0

            for app in source_apps:
                # License savings - full cost elimination
                if app.license_cost:
                    license_savings += app.license_cost
                elif app.license_cost_annual:
                    license_savings += app.license_cost_annual

                # Infrastructure savings - partial reduction
                if app.infrastructure_cost:
                    infrastructure_savings += (
                        app.infrastructure_cost * ConsolidationService.INFRASTRUCTURE_SAVINGS_FACTOR
                    )
                elif app.infrastructure_cost_monthly:
                    infrastructure_savings += (
                        app.infrastructure_cost_monthly
                        * 12
                        * ConsolidationService.INFRASTRUCTURE_SAVINGS_FACTOR
                    )

                # Support savings - partial reduction
                if app.support_cost:
                    support_savings += (
                        app.support_cost * ConsolidationService.SUPPORT_SAVINGS_FACTOR
                    )

                # Maintenance savings - full elimination
                if app.maintenance_cost:
                    maintenance_savings += app.maintenance_cost

                # One-time savings from avoiding future implementations
                if app.implementation_cost:
                    one_time_savings += app.implementation_cost * 0.5  # 50% of future impl avoided

            # Total annual savings
            annual_savings = (
                license_savings + infrastructure_savings + support_savings + maintenance_savings
            )

            # If no cost data, estimate based on typical enterprise app costs
            if annual_savings == 0 and len(source_apps) > 0:
                annual_savings = len(source_apps) * 50000  # $50k per app estimate

            # Update opportunity
            opportunity.estimated_annual_savings = annual_savings
            opportunity.estimated_one_time_savings = one_time_savings

            # Calculate ROI and payback
            ConsolidationService._update_calculated_fields(opportunity)

            db.session.commit()

            logger.info(
                f"Calculated savings for opportunity {opportunity_id}: ${annual_savings:,.2f}/year"
            )
            return {
                "success": True,
                "annual_savings": annual_savings,
                "one_time_savings": one_time_savings,
                "breakdown": {
                    "license": license_savings,
                    "infrastructure": infrastructure_savings,
                    "support": support_savings,
                    "maintenance": maintenance_savings,
                },
                "roi_percentage": opportunity.roi_percentage,
                "payback_period_months": opportunity.payback_period_months,
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error calculating savings: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _update_calculated_fields(opportunity: ConsolidationOpportunity):
        """Update ROI and payback period based on current values."""
        annual_savings = opportunity.estimated_annual_savings or 0
        impl_cost = opportunity.implementation_cost or 0

        # Calculate ROI
        if impl_cost > 0:
            opportunity.roi_percentage = ((annual_savings - impl_cost) / impl_cost) * 100
        else:
            opportunity.roi_percentage = 0 if annual_savings == 0 else 100

        # Calculate payback period in months
        if annual_savings > 0:
            monthly_savings = annual_savings / 12
            opportunity.payback_period_months = (
                int(impl_cost / monthly_savings) if monthly_savings > 0 else 0
            )
        else:
            opportunity.payback_period_months = 0

        db.session.commit()

    # =========================================================================
    # Savings Tracking Methods
    # =========================================================================

    @staticmethod
    def record_realized_savings(opportunity_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Record actual realized savings for an opportunity.

        Args:
            opportunity_id: ID of the opportunity
            data: Savings data including period_start, period_end, realized_savings, category

        Returns:
            dict with result
        """
        try:
            opportunity = _mine(ConsolidationOpportunity).filter(ConsolidationOpportunity.id == opportunity_id).first()
            if not opportunity:
                return {"success": False, "error": "Opportunity not found"}

            savings = SavingsRealization(
                **_written_by_org(),
                opportunity_id=opportunity_id,
                period_start=datetime.strptime(data["period_start"], "%Y-%m-%d").date(),
                period_end=datetime.strptime(data["period_end"], "%Y-%m-%d").date(),
                realized_savings=data.get("realized_savings", 0.0),
                savings_category=data.get("savings_category", "license"),
                notes=data.get("notes", ""),
                verified_by=data.get("verified_by"),
            )

            db.session.add(savings)
            db.session.commit()

            logger.info(
                f"Recorded ${savings.realized_savings:,.2f} savings for opportunity {opportunity_id}"
            )
            return {"success": True, "savings": savings.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error recording savings: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_savings_by_opportunity(opportunity_id: int) -> List[Dict[str, Any]]:
        """
        Get all savings realizations for an opportunity.

        Args:
            opportunity_id: ID of the opportunity

        Returns:
            List of savings dictionaries
        """
        try:
            savings = (
                _mine(SavingsRealization).filter_by(opportunity_id=opportunity_id)
                .order_by(SavingsRealization.period_start.desc())
                .all()
            )

            return [s.to_dict() for s in savings]

        except Exception as e:
            logger.error(f"Error getting savings: {e}")
            return []

    @staticmethod
    def get_total_realized_savings(year: Optional[int] = None) -> Dict[str, Any]:
        """
        Get total realized savings, optionally filtered by year.

        Args:
            year: Optional year to filter by

        Returns:
            dict with total and breakdown by category
        """
        try:
            query = _mine(SavingsRealization)

            if year:
                start_of_year = date(year, 1, 1)
                end_of_year = date(year, 12, 31)
                query = query.filter(
                    SavingsRealization.period_start >= start_of_year,
                    SavingsRealization.period_end <= end_of_year,
                )

            savings = query.all()

            total = sum(s.realized_savings for s in savings)
            by_category = {}

            for s in savings:
                category = s.savings_category or "other"
                by_category[category] = by_category.get(category, 0) + s.realized_savings

            return {
                "total": total,
                "by_category": by_category,
                "count": len(savings),
                "year": year,
            }

        except Exception as e:
            logger.error(f"Error getting total savings: {e}")
            return {"total": 0, "by_category": {}, "count": 0, "year": year}

    # =========================================================================
    # Analytics Methods
    # =========================================================================

    @staticmethod
    def get_consolidation_statistics() -> Dict[str, Any]:
        """
        Get summary statistics for consolidation activities.

        Returns:
            dict with comprehensive statistics
        """
        try:
            # Candidate statistics
            total_candidates = _mine(ConsolidationCandidate).count()
            candidates_by_status = {}
            for status in ["pending_review", "approved", "rejected", "merged"]:
                count = _mine(ConsolidationCandidate).filter_by(status=status).count()
                candidates_by_status[status] = count

            # Opportunity statistics
            total_opportunities = _mine(ConsolidationOpportunity).count()
            opportunities_by_status = {}
            for status in [
                "identified",
                "analysis",
                "approved",
                "in_progress",
                "completed",
                "cancelled",
            ]:
                count = _mine(ConsolidationOpportunity).filter_by(status=status).count()
                opportunities_by_status[status] = count

            # Estimated savings in pipeline
            pipeline_opportunities = _mine(ConsolidationOpportunity).filter(
                ConsolidationOpportunity.status.in_(
                    ["identified", "analysis", "approved", "in_progress"]
                )
            ).all()

            estimated_pipeline_savings = sum(
                o.estimated_annual_savings or 0 for o in pipeline_opportunities
            )

            # Realized savings YTD
            current_year = datetime.now().year
            ytd_savings = ConsolidationService.get_total_realized_savings(year=current_year)

            # Completed opportunities
            completed = _mine(ConsolidationOpportunity).filter_by(status="completed").all()
            total_completed_savings = sum(o.estimated_annual_savings or 0 for o in completed)

            return {
                "candidates": {
                    "total": total_candidates,
                    "by_status": candidates_by_status,
                },
                "opportunities": {
                    "total": total_opportunities,
                    "by_status": opportunities_by_status,
                },
                "savings": {
                    "estimated_pipeline": estimated_pipeline_savings,
                    "realized_ytd": ytd_savings["total"],
                    "completed_total": total_completed_savings,
                },
            }

        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {
                "candidates": {"total": 0, "by_status": {}},
                "opportunities": {"total": 0, "by_status": {}},
                "savings": {"estimated_pipeline": 0, "realized_ytd": 0, "completed_total": 0},
            }

    @staticmethod
    def get_savings_forecast() -> Dict[str, Any]:
        """
        Project future savings based on opportunities in the pipeline.

        Returns:
            dict with forecasted savings by year
        """
        try:
            # Get opportunities with target completion dates
            opportunities = _mine(ConsolidationOpportunity).filter(
                ConsolidationOpportunity.status.in_(["approved", "in_progress"]),
                ConsolidationOpportunity.target_completion_date.isnot(None),
            ).all()

            forecast_by_year = {}
            current_year = datetime.now().year

            for opp in opportunities:
                completion_year = opp.target_completion_date.year

                # First year: pro-rated based on completion month
                first_year_months = 12 - opp.target_completion_date.month + 1
                first_year_savings = (opp.estimated_annual_savings or 0) * (first_year_months / 12)

                # Add to forecast
                if completion_year not in forecast_by_year:
                    forecast_by_year[completion_year] = 0
                forecast_by_year[completion_year] += first_year_savings

                # Full year savings for subsequent years (up to 5 years out)
                for year in range(completion_year + 1, current_year + 6):
                    if year not in forecast_by_year:
                        forecast_by_year[year] = 0
                    forecast_by_year[year] += opp.estimated_annual_savings or 0

            # Sort by year
            sorted_forecast = dict(sorted(forecast_by_year.items()))

            return {
                "forecast_by_year": sorted_forecast,
                "opportunities_included": len(opportunities),
            }

        except Exception as e:
            logger.error(f"Error generating forecast: {e}")
            return {"forecast_by_year": {}, "opportunities_included": None}

    @staticmethod
    def get_consolidation_dashboard_data() -> Dict[str, Any]:
        """
        Get all data needed for a consolidation dashboard view.

        Returns:
            dict with comprehensive dashboard data
        """
        try:
            # Get statistics
            stats = ConsolidationService.get_consolidation_statistics()

            # Get recent candidates (pending review)
            recent_candidates = ConsolidationService.get_all_candidates(
                status_filter="pending_review"
            )[:10]

            # Get active opportunities
            active_opportunities = ConsolidationService.get_all_opportunities(
                status_filter="in_progress"
            )

            # Get forecast
            forecast = ConsolidationService.get_savings_forecast()

            # Get top opportunities by savings
            top_opportunities = (
                _mine(ConsolidationOpportunity).filter(
                    ConsolidationOpportunity.status.in_(
                        ["identified", "analysis", "approved", "in_progress"]
                    )
                )
                .order_by(ConsolidationOpportunity.estimated_annual_savings.desc())
                .limit(5)
                .all()
            )

            # Get recent savings realizations
            recent_savings = (
                _mine(SavingsRealization).order_by(SavingsRealization.created_at.desc())
                .limit(10)
                .all()
            )

            return {
                "statistics": stats,
                "pending_candidates": recent_candidates,
                "active_opportunities": active_opportunities,
                "top_opportunities": [o.to_dict() for o in top_opportunities],
                "recent_savings": [s.to_dict() for s in recent_savings],
                "forecast": forecast,
            }

        except Exception as e:  # fabricated-ok: error path returns empty collections (no items), not fabricated counts
            logger.error(f"Error getting dashboard data: {e}")
            return {
                "statistics": {},
                "pending_candidates": [],
                "active_opportunities": [],
                "top_opportunities": [],
                "recent_savings": [],
                "forecast": {},
            }
