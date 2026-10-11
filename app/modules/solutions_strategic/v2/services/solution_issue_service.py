"""
SolutionIssueService: Track and escalate implementation issues and blockers.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from app import db
from app.models.solution_governance import SolutionIssue
from app.utils.tenant_users import user_in_org


class SolutionIssueService:
    """Manage issues, blockers, and escalation."""
    
    SEVERITY_LEVELS = {
        'P1': 1,  # Critical - blocks go-live
        'P2': 2,  # High - major delay
        'P3': 3,  # Medium - minor delay
        'P4': 4   # Low - informational
    }
    
    def create_issue(
        self,
        solution_id: int,
        title: str,
        description: str,
        severity: str = 'P3',
        created_by_id: int = None,
        category: Optional[str] = None,
        workflow_task_id: Optional[int] = None,
        impact_area: Optional[str] = None,
        target_resolution_date: Optional[datetime] = None
    ) -> SolutionIssue:
        """
        Create a new issue.
        
        Args:
            solution_id: Solution with issue
            title: Issue title
            description: Issue description
            severity: P1/P2/P3/P4
            created_by_id: Who reported it
            category: Issue category
            workflow_task_id: Related task
            impact_area: What it affects
            target_resolution_date: Target fix date
        
        Returns:
            SolutionIssue: Created issue
        """
        issue = SolutionIssue(
            solution_id=solution_id,
            title=title,
            description=description,
            severity=severity,
            priority=self.SEVERITY_LEVELS.get(severity, 999),
            created_by_id=created_by_id,
            category=category,
            workflow_task_id=workflow_task_id,
            impact_area=impact_area,
            status='open',
            target_resolution_date=target_resolution_date
        )
        
        # Auto-escalate P1 issues immediately
        if severity == 'P1':
            issue.auto_escalate_if_not_resolved_by = datetime.utcnow().date() + timedelta(days=1)
        
        db.session.add(issue)
        db.session.commit()
        
        return issue
    
    def assign_issue(
        self,
        issue_id: int,
        assigned_to_id: int,
        organization_id: Optional[int] = None,
        solution_id: Optional[int] = None
    ) -> SolutionIssue:
        """
        Assign issue to a user.

        Args:
            issue_id: Issue to assign
            assigned_to_id: User to assign to
            organization_id: Caller's organisation; assigned_to_id is refused
                unless it names a user of this organisation
            solution_id: Solution the issue is expected to belong to (the URL's
                solution); a mismatch is reported the same way as a missing issue

        Returns:
            SolutionIssue: Updated issue
        """
        issue = db.session.query(SolutionIssue).get(issue_id)
        if not issue or (solution_id is not None and issue.solution_id != solution_id):
            raise ValueError(f"Issue {issue_id} not found")

        if assigned_to_id and not user_in_org(assigned_to_id, organization_id):
            raise ValueError("Invalid assigned_to_id")

        issue.assigned_to_id = assigned_to_id
        db.session.commit()

        return issue

    def escalate_issue(
        self,
        issue_id: int,
        escalated_to_id: int,
        escalation_reason: str,
        organization_id: Optional[int] = None,
        solution_id: Optional[int] = None
    ) -> SolutionIssue:
        """
        Escalate an issue to higher authority.

        Args:
            issue_id: Issue to escalate
            escalated_to_id: User to escalate to
            escalation_reason: Why escalating
            organization_id: Caller's organisation; escalated_to_id is refused
                unless it names a user of this organisation
            solution_id: Solution the issue is expected to belong to (the URL's
                solution); a mismatch is reported the same way as a missing issue

        Returns:
            SolutionIssue: Updated issue
        """
        issue = db.session.query(SolutionIssue).get(issue_id)
        if not issue or (solution_id is not None and issue.solution_id != solution_id):
            raise ValueError(f"Issue {issue_id} not found")

        if escalated_to_id and not user_in_org(escalated_to_id, organization_id):
            raise ValueError("Invalid escalated_to_id")

        issue.escalated_to_id = escalated_to_id
        issue.escalation_reason = escalation_reason
        issue.escalation_count += 1
        issue.escalated_at = datetime.utcnow()
        
        # If escalated, upgrade severity if not already P1
        if issue.severity != 'P1':
            issue.severity = 'P1'
            issue.priority = 1
        
        db.session.commit()
        
        return issue
    
    def update_resolution_plan(
        self,
        issue_id: int,
        resolution_plan: str,
        target_date: Optional[datetime] = None
    ) -> SolutionIssue:
        """
        Add resolution plan to issue.
        
        Args:
            issue_id: Issue to update
            resolution_plan: How to fix it
            target_date: Target fix date
        
        Returns:
            SolutionIssue: Updated issue
        """
        issue = db.session.query(SolutionIssue).get(issue_id)
        if not issue:
            raise ValueError(f"Issue {issue_id} not found")
        
        issue.resolution_plan = resolution_plan
        if target_date:
            issue.target_resolution_date = target_date
        
        issue.status = 'investigating'
        
        db.session.commit()
        
        return issue
    
    def resolve_issue(
        self,
        issue_id: int,
        resolved_by_id: int,
        resolution_notes: Optional[str] = None,
        solution_id: Optional[int] = None
    ) -> SolutionIssue:
        """
        Mark issue as resolved.

        Args:
            issue_id: Issue to resolve
            resolved_by_id: Who resolved it — the acting user, not a request value
            resolution_notes: How it was resolved
            solution_id: Solution the issue is expected to belong to (the URL's
                solution); a mismatch is reported the same way as a missing issue

        Returns:
            SolutionIssue: Updated issue
        """
        issue = db.session.query(SolutionIssue).get(issue_id)
        if not issue or (solution_id is not None and issue.solution_id != solution_id):
            raise ValueError(f"Issue {issue_id} not found")

        issue.status = 'resolved'
        issue.resolved_by_id = resolved_by_id
        issue.resolved_at = datetime.utcnow()
        
        if resolution_notes:
            issue.description = f"{issue.description}\n\n--- RESOLUTION ---\n{resolution_notes}"
        
        db.session.commit()
        
        return issue
    
    def get_open_issues(self, solution_id: int) -> List[Dict]:
        """
        Get all open issues for solution.
        
        Args:
            solution_id: Solution to check
        
        Returns:
            List of open issues sorted by priority
        """
        issues = db.session.query(SolutionIssue).filter(
            SolutionIssue.solution_id == solution_id,
            SolutionIssue.status.in_(['open', 'investigating', 'on_hold'])
        ).order_by(SolutionIssue.priority).all()
        
        return [i.to_dict() for i in issues]
    
    def check_auto_escalation(self) -> List[Dict]:
        """
        Check for issues that need auto-escalation (not resolved by target date).
        
        Returns:
            List of issues that should be escalated
        """
        today = datetime.utcnow().date()
        
        escalatable = db.session.query(SolutionIssue).filter(
            SolutionIssue.status.in_(['open', 'investigating']),
            SolutionIssue.auto_escalate_if_not_resolved_by <= today,
            SolutionIssue.escalated_to_id == None  # Not already escalated
        ).all()
        
        results = []
        for issue in escalatable:
            results.append({
                'issue_id': issue.id,
                'title': issue.title,
                'severity': issue.severity,
                'days_overdue': (today - issue.auto_escalate_if_not_resolved_by).days,
                'should_escalate': True
            })
        
        return results
    
    def get_issue_summary(self, solution_id: int) -> Dict[str, Any]:
        """
        Get issue summary dashboard for solution.
        
        Args:
            solution_id: Solution to summarize
        
        Returns:
            Dict with issue metrics
        """
        all_issues = db.session.query(SolutionIssue).filter(
            SolutionIssue.solution_id == solution_id
        ).all()
        
        open_issues = [i for i in all_issues if i.status in ['open', 'investigating']]
        p1_issues = [i for i in open_issues if i.severity == 'P1']
        p2_issues = [i for i in open_issues if i.severity == 'P2']
        escalated = [i for i in open_issues if i.escalated_to_id]
        
        resolved = [i for i in all_issues if i.status == 'resolved']
        
        return {
            'solution_id': solution_id,
            'total_issues': len(all_issues),
            'open_issues': len(open_issues),
            'critical_p1': len(p1_issues),
            'high_p2': len(p2_issues),
            'escalated': len(escalated),
            'resolved': len(resolved),
            'critical_issues': [i.to_dict() for i in p1_issues],
            'high_priority_issues': [i.to_dict() for i in p2_issues[:5]]
        }
    
    def get_blockers_by_date(self, solution_id: int) -> Dict[str, Any]:
        """
        Identify issues that will block go-live if not resolved.
        
        Args:
            solution_id: Solution to analyze
        
        Returns:
            Dict with blocker analysis
        """
        blocking_issues = db.session.query(SolutionIssue).filter(
            SolutionIssue.solution_id == solution_id,
            SolutionIssue.severity.in_(['P1', 'P2']),
            SolutionIssue.status != 'resolved'
        ).all()
        
        upcoming_blockers = []
        for issue in blocking_issues:
            if issue.target_resolution_date:
                days_until = (issue.target_resolution_date - datetime.utcnow().date()).days
                upcoming_blockers.append({
                    'issue_id': issue.id,
                    'title': issue.title,
                    'severity': issue.severity,
                    'target_date': issue.target_resolution_date.isoformat(),
                    'days_until_target': days_until,
                    'will_miss_target': days_until < 0
                })
        
        return {
            'solution_id': solution_id,
            'blocking_issues_count': len(blocking_issues),
            'upcoming_blockers': sorted(upcoming_blockers, key=lambda x: x['days_until_target'])
        }
