"""
ADR (Architecture Decision Record) Service

Manages architecture decisions with ARB approval workflow.
Provides CRUD operations, approval routing, and decision tracking.

Reuses:
- ArchitectureDecision model (app/models/architecture_decision.py -- the
  TENANT-SCOPED mapping; see the import note below)
- ARB workflow services (app/services/arb_workflow_service.py)
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from app import db
# architecture_decision (singular), NOT architecture_decisions (plural).
#
# The `architecture_decisions` table is mapped twice and both mappings are live in
# the registry: the singular module's class carries TenantMixin, the plural one is a
# bare db.Model. TenantMixin is what installs the ORM-event tenant filter and the
# organization_id auto-set, so the plural mapping is not filtered on SELECT and
# returns every organisation's rows without raising.
#
# This service is served over HTTP by app/modules/architecture/routes/adr_routes.py.
# With the plural import, ADRService.list_adrs() returned other tenants' governance
# records and get_adr() fetched them by id -- proven in
# tests/test_adr_service_tenancy.py, which failed on exactly that before this line
# changed. ADRs state what an organisation decided and what it rejected; a list that
# silently includes another company's decisions is worse than an empty one, because
# it reads as authoritative.
#
# The scoped mapping is a strict superset: all 20 attributes this service uses are
# among its 32.
from app.models.architecture_decision import ArchitectureDecision

logger = logging.getLogger(__name__)


class ADRService:
    """Service for managing Architecture Decision Records."""
    
    @staticmethod
    def create_adr(
        solution_id: int,
        title: str,
        context: str,
        decision: str,
        rationale: str,
        decision_type: str = "technology_choice",
        alternatives: Optional[List[Dict]] = None,
        constraints: Optional[List[Dict]] = None,
        consequences: Optional[str] = None,
        decided_by_id: Optional[int] = None
    ) -> ArchitectureDecision:
        """Create a new ADR.
        
        Args:
            solution_id: Solution ID this ADR relates to
            title: ADR title
            context: Why this decision was needed
            decision: What was decided
            rationale: Why this option was chosen
            decision_type: Type of decision (technology_choice, vendor_selection, etc.)
            alternatives: List of alternatives considered
            constraints: List of constraints
            consequences: Consequences of this decision
            decided_by_id: User ID who made the decision
            
        Returns:
            Created ArchitectureDecision instance
        """
        adr = ArchitectureDecision(
            solution_id=solution_id,
            title=title,
            context=context,
            decision=decision,
            rationale=rationale,
            decision_type=decision_type,
            alternatives=alternatives or [],
            constraints=constraints or [],
            consequences=consequences,
            status="proposed",
            decided_by_id=decided_by_id,
            decided_at=datetime.utcnow() if decided_by_id else None
        )
        
        db.session.add(adr)
        db.session.commit()
        
        logger.info(f"Created ADR {adr.id}: {title}")
        return adr
    
    @staticmethod
    def get_adr(adr_id: int) -> Optional[ArchitectureDecision]:
        """Get ADR by ID, tenant-scoped.

        Deliberately not Query.get(): per AGENTS.md it is tenant-scoped only on an
        identity-map MISS. On a hit it returns the cached object with no SQL, so
        do_orm_execute never runs and no tenant predicate is applied -- which is
        precisely the shape that hands one organisation another's record.
        """
        return db.session.execute(
            db.select(ArchitectureDecision).where(ArchitectureDecision.id == adr_id)
        ).scalar_one_or_none()
    
    @staticmethod
    def list_adrs(
        solution_id: Optional[int] = None,
        status: Optional[str] = None,
        decision_type: Optional[str] = None
    ) -> List[ArchitectureDecision]:
        """List ADRs with optional filters.
        
        Args:
            solution_id: Filter by solution ID
            status: Filter by status (proposed, approved, rejected, superseded)
            decision_type: Filter by decision type
            
        Returns:
            List of ADRs matching filters
        """
        query = ArchitectureDecision.query
        
        if solution_id is not None:
            query = query.filter_by(solution_id=solution_id)
        
        if status:
            query = query.filter_by(status=status)
        
        if decision_type:
            query = query.filter_by(decision_type=decision_type)
        
        return query.order_by(ArchitectureDecision.created_at.desc()).all()
    
    @staticmethod
    def approve_adr(adr_id: int, approved_by_id: int) -> ArchitectureDecision:
        """Approve an ADR.
        
        Args:
            adr_id: ADR ID to approve
            approved_by_id: User ID who approved
            
        Returns:
            Approved ArchitectureDecision instance
        """
        adr = ArchitectureDecision.query.get_or_404(adr_id)
        
        adr.status = "approved"
        adr.approved_by_id = approved_by_id
        adr.approved_at = datetime.utcnow()
        
        db.session.commit()
        logger.info(f"ADR {adr_id} approved by user {approved_by_id}")
        
        return adr
    
    @staticmethod
    def reject_adr(adr_id: int, rejection_reason: str) -> ArchitectureDecision:
        """Reject an ADR.
        
        Args:
            adr_id: ADR ID to reject
            rejection_reason: Reason for rejection
            
        Returns:
            Rejected ArchitectureDecision instance
        """
        adr = ArchitectureDecision.query.get_or_404(adr_id)
        
        adr.status = "rejected"
        adr.rejection_reason = rejection_reason
        
        db.session.commit()
        logger.info(f"ADR {adr_id} rejected")
        
        return adr
    
    @staticmethod
    def supersede_adr(old_adr_id: int, new_adr_id: int) -> ArchitectureDecision:
        """Mark an ADR as superseded by another ADR.
        
        Args:
            old_adr_id: ADR to supersede
            new_adr_id: New ADR that supersedes it
            
        Returns:
            Superseded ArchitectureDecision instance
        """
        old_adr = ArchitectureDecision.query.get_or_404(old_adr_id)
        
        old_adr.status = "superseded"
        old_adr.superseded_by_id = new_adr_id
        
        db.session.commit()
        logger.info(f"ADR {old_adr_id} superseded by ADR {new_adr_id}")
        
        return old_adr
    
    @staticmethod
    def get_adr_statistics() -> Dict:
        """Get ADR statistics for dashboard.
        
        Returns:
            Dict with statistics:
            - total_adrs: Total number of ADRs
            - by_status: Count by status
            - by_type: Count by decision type
            - recent_adrs: 5 most recent ADRs
        """
        total = ArchitectureDecision.query.count()
        
        # Count by status
        by_status = {}
        for status in ['proposed', 'approved', 'rejected', 'superseded']:
            count = ArchitectureDecision.query.filter_by(status=status).count()
            by_status[status] = count
        
        # Count by type
        by_type = {}
        for dtype in ['technology_choice', 'vendor_selection', 'pattern_selection', 'integration_approach']:
            count = ArchitectureDecision.query.filter_by(decision_type=dtype).count()
            by_type[dtype] = count
        
        # Recent ADRs
        recent = (
            ArchitectureDecision.query
            .order_by(ArchitectureDecision.created_at.desc())
            .limit(5)
            .all()
        )
        
        return {
            'total_adrs': total,
            'by_status': by_status,
            'by_type': by_type,
            'recent_adrs': [adr.to_dict() for adr in recent]
        }
    
    @staticmethod
    def get_adr_templates() -> List[Dict]:
        """Get ADR templates for different decision types.
        
        Returns:
            List of template dictionaries
        """
        return [
            {
                'type': 'technology_choice',
                'title': 'Technology Choice: [Technology Name]',
                'context': 'We need to select a technology for [purpose]...',
                'decision': 'We will use [selected technology]...',
                'rationale': 'This technology was selected because...',
                'alternatives_template': [
                    {
                        'name': 'Alternative 1',
                        'pros': ['Pro 1', 'Pro 2'],
                        'cons': ['Con 1', 'Con 2'],
                        'rejected_reason': 'Why rejected'
                    }
                ]
            },
            {
                'type': 'vendor_selection',
                'title': 'Vendor Selection: [Vendor Name]',
                'context': 'We need to select a vendor for [capability]...',
                'decision': 'We will partner with [selected vendor]...',
                'rationale': 'This vendor was selected because...',
                'alternatives_template': [
                    {
                        'name': 'Vendor 1',
                        'pros': ['Cost-effective', 'Good support'],
                        'cons': ['Limited features', 'Smaller market share'],
                        'rejected_reason': 'Why rejected'
                    }
                ]
            },
            {
                'type': 'pattern_selection',
                'title': 'Pattern Selection: [Pattern Name]',
                'context': 'We need to select an architecture pattern for [system]...',
                'decision': 'We will implement [selected pattern]...',
                'rationale': 'This pattern was selected because...',
                'alternatives_template': [
                    {
                        'name': 'Pattern 1',
                        'pros': ['Scalable', 'Well-documented'],
                        'cons': ['Complex', 'Higher cost'],
                        'rejected_reason': 'Why rejected'
                    }
                ]
            },
            {
                'type': 'integration_approach',
                'title': 'Integration Approach: [System A] ↔ [System B]',
                'context': 'We need to integrate [systems]...',
                'decision': 'We will use [integration approach]...',
                'rationale': 'This approach was selected because...',
                'alternatives_template': [
                    {
                        'name': 'Approach 1',
                        'pros': ['Real-time', 'Low latency'],
                        'cons': ['Higher complexity', 'More maintenance'],
                        'rejected_reason': 'Why rejected'
                    }
                ]
            }
        ]
