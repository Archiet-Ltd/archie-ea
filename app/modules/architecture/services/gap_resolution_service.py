"""
Gap Resolution Service - Automatic WorkPackage Generation from Gaps

This service automatically generates WorkPackages to resolve identified gaps,
enabling gap-to-implementation traceability.

Features:
- AI-powered WorkPackage generation from Gap descriptions
- Automatic linking of WorkPackages to Gaps
- Gap resolution tracking via WorkPackage completion
- Priority-based WorkPackage sequencing
- Deliverable generation for gap resolution
"""

from app.services.archimate_backbone import sync_archimate_element
import json
import logging
from datetime import date, datetime  # dead-code-ok
from typing import Dict, List, Optional

from app import db
from app.models.implementation_migration import Deliverable, Gap, WorkPackage
from app.models.relationship_tables import gap_work_packages
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class GapResolutionService:
    """
    Service for automatically generating WorkPackages from Gaps.

    Enables gap-to-implementation traceability by creating executable
    WorkPackages that resolve identified gaps.
    """

    def __init__(self):
        self.llm_service = LLMService()

    def create_work_packages_from_gap(
        self, gap_id: int, auto_generate: bool = True, work_package_count: Optional[int] = None
    ) -> List[WorkPackage]:
        """
        Generate WorkPackages to resolve a Gap.

        Args:
            gap_id: ID of the Gap to resolve
            auto_generate: If True, uses AI to generate work packages.
                          If False, requires manual work package descriptions
            work_package_count: Optional number of work packages to generate.
                               If None, AI determines optimal count

        Returns:
            List of WorkPackage instances linked to the Gap

        Example:
            >>> service = GapResolutionService()
            >>> gap = Gap.query.filter_by(name="Missing CRM Integration").first()
            >>> work_packages = service.create_work_packages_from_gap(gap.id)
            >>> # Returns: [WorkPackage("Design Integration"), WorkPackage("Implement API"), ...]
        """
        gap = db.session.get(Gap, gap_id)
        if not gap:
            raise ValueError(f"Gap {gap_id} not found")

        if auto_generate:
            work_packages_data = self._generate_work_packages_from_gap(gap, work_package_count)
        else:
            raise ValueError("Manual work package generation not yet implemented")

        work_packages = []
        for i, wp_data in enumerate(work_packages_data):
            work_package = WorkPackage(
                name=wp_data["name"],
                description=wp_data.get("description", ""),
                summary=wp_data.get("summary", ""),
                architecture_id=gap.architecture_id,
                application_component_id=gap.application_component_id,
                status="planned",
                priority=gap.priority,
                start_date=self._parse_date(wp_data.get("start_date")),
                target_date=self._parse_date(wp_data.get("target_date")),
                estimated_effort_hours=wp_data.get("estimated_hours"),
                sequence_order=i + 1,
                context=gap.context,
                context_id=gap.context_id,
            )
            db.session.add(work_package)
            sync_archimate_element(work_package)
            db.session.flush()  # Get ID

            # Link WorkPackage to Gap through the relationship (not a Core insert), so
            # the session bridge sees the link and makes the ArchiMate relationship.
            work_package.gaps.append(gap)
            db.session.flush()
            # The association row also carries the gap's resolution role.
            db.session.execute(  # tenant-filtered: scoped via parent FK (gap_id, work_package_id)
                gap_work_packages.update()
                .where(gap_work_packages.c.gap_id == gap.id,
                       gap_work_packages.c.work_package_id == work_package.id)
                .values(resolution_role=wp_data.get("resolution_role", "primary"))
            )

            # Generate Deliverables for this WorkPackage
            if wp_data.get("deliverables"):
                for deliv_data in wp_data["deliverables"]:
                    deliverable = Deliverable(
                        name=deliv_data["name"],
                        description=deliv_data.get("description", ""),
                        work_package_id=work_package.id,
                        architecture_id=gap.architecture_id,
                        application_component_id=gap.application_component_id,
                        deliverable_type=deliv_data.get("type", "document"),
                        delivery_status="planned",
                        target_date=self._parse_date(deliv_data.get("target_date")),
                    )
                    db.session.add(deliverable)

            work_packages.append(work_package)

        # Update gap status
        gap.resolution_status = "planned"
        db.session.commit()

        return work_packages

    def get_gap_resolution_progress(self, gap_id: int) -> Dict:
        """
        Calculate gap resolution progress based on WorkPackage completion.

        Args:
            gap_id: ID of the Gap

        Returns:
            Dict with resolution metrics:
            {
                'gap_id': 1,
                'gap_name': 'Missing CRM Integration',
                'resolution_status': 'in_progress',
                'total_work_packages': 5,
                'completed_work_packages': 2,
                'in_progress_work_packages': 2,
                'planned_work_packages': 1,
                'completion_percentage': 40.0,
                'estimated_resolution_date': '2024 - 12 - 31',
                'is_on_track': True,
                'blockers': [...]
            }
        """
        gap = db.session.get(Gap, gap_id)
        if not gap:
            raise ValueError(f"Gap {gap_id} not found")

        # Get all WorkPackages linked to this Gap
        work_packages = (
            db.session.query(WorkPackage)
            .join(gap_work_packages)
            .filter(gap_work_packages.c.gap_id == gap_id)
            .all()
        )

        total = len(work_packages)
        completed = sum(1 for wp in work_packages if wp.status == "completed")
        in_progress = sum(1 for wp in work_packages if wp.status in ["in_progress", "active"])
        planned = sum(1 for wp in work_packages if wp.status == "planned")

        completion_percentage = (completed / total * 100) if total > 0 else 0

        # Find blockers (overdue or at-risk work packages)
        blockers = []
        for wp in work_packages:
            if wp.is_overdue():
                blockers.append(
                    {
                        "id": wp.id,
                        "name": wp.name,
                        "reason": "overdue",
                        "target_date": wp.target_date.isoformat() if wp.target_date else None,
                    }
                )
            elif wp.status == "at_risk":
                blockers.append({"id": wp.id, "name": wp.name, "reason": "at_risk"})

        # Estimate resolution date from latest work package
        latest_date = None
        for wp in work_packages:
            if wp.target_date:
                if not latest_date or wp.target_date > latest_date:
                    latest_date = wp.target_date

        # Determine resolution status
        if completion_percentage == 100:
            resolution_status = "resolved"
            if not gap.resolved_at:
                gap.resolved_at = date.today()
                gap.resolution_status = "resolved"
        elif completion_percentage > 0:
            resolution_status = "in_progress"
            if gap.resolution_status == "identified":
                gap.resolution_status = "in_progress"
        else:
            resolution_status = gap.resolution_status or "identified"

        # Check if on track
        is_on_track = len(blockers) == 0 and completion_percentage > 0

        db.session.commit()

        return {
            "gap_id": gap.id,
            "gap_name": gap.name,
            "resolution_status": resolution_status,
            "total_work_packages": total,
            "completed_work_packages": completed,
            "in_progress_work_packages": in_progress,
            "planned_work_packages": planned,
            "completion_percentage": round(completion_percentage, 1),
            "estimated_resolution_date": latest_date.isoformat() if latest_date else None,
            "is_on_track": is_on_track,
            "blockers": blockers,
        }

    def resolve_gap_from_work_packages(self, gap_id: int) -> bool:
        """
        Mark gap as resolved if all linked WorkPackages are completed.

        Args:
            gap_id: ID of the Gap

        Returns:
            True if gap was resolved, False otherwise
        """
        progress = self.get_gap_resolution_progress(gap_id)

        if progress["completion_percentage"] == 100:
            gap = db.session.get(Gap, gap_id)
            gap.resolution_status = "resolved"
            gap.resolved_at = date.today()
            db.session.commit()
            return True

        return False

    def _generate_work_packages_from_gap(
        self, gap: Gap, work_package_count: Optional[int]
    ) -> List[Dict]:
        """Use AI to generate WorkPackages from Gap description."""
        prompt = self._build_work_package_generation_prompt(gap, work_package_count)

        try:
            response = self.llm_service.generate_from_prompt(prompt)
            data = json.loads(response)
            return data.get("work_packages", [])
        except Exception as e:
            raise Exception(f"Work package generation failed: {str(e)}")

    def _build_work_package_generation_prompt(
        self, gap: Gap, work_package_count: Optional[int]
    ) -> str:
        """Build prompt for AI work package generation."""
        count_instruction = (
            f"\nGenerate exactly {work_package_count} work packages."
            if work_package_count
            else "\nGenerate an appropriate number of work packages (typically 3 - 7)."
        )

        return f"""You are a project planning expert. Generate WORK PACKAGES to resolve this architectural gap.

Gap Information:
Name: {gap.name}
Description: {gap.description or 'No description provided'}
Priority: {gap.priority}
Severity: {gap.severity if hasattr(gap, 'severity') else 'medium'}
Impact: {gap.impact if hasattr(gap, 'impact') else 'medium'}
Current State: {gap.current_state_ref or 'Not specified'}
Target State: {gap.target_state_ref or 'Not specified'}
Context: {gap.context}

{count_instruction}

Each work package should:
1. Be a discrete, executable unit of work
2. Have clear deliverables
3. Have realistic timeframes
4. Contribute directly to closing the gap
5. Be sequenced logically (dependencies considered)

Return JSON:
{{
  "work_packages": [
    {{
      "name": "Phase 1: Analysis and Design",
      "summary": "Analyze current state and design target solution",
      "description": "Conduct detailed analysis of current state, identify root causes, and design comprehensive solution architecture",
      "estimated_hours": 160,
      "start_date": "2024 - 01 - 15",
      "target_date": "2024 - 03 - 15",
      "resolution_role": "primary",
      "deliverables": [
        {{
          "name": "Gap Analysis Report",
          "description": "Detailed analysis of the gap with root cause analysis",
          "type": "document",
          "target_date": "2024 - 02 - 15"
        }},
        {{
          "name": "Solution Design Document",
          "description": "Architecture and design for resolving the gap",
          "type": "document",
          "target_date": "2024 - 03 - 15"
        }}
      ]
    }},
    {{
      "name": "Phase 2: Implementation",
      "summary": "Implement the designed solution",
      "description": "Execute the implementation according to the design, including development, testing, and integration",
      "estimated_hours": 320,
      "start_date": "2024 - 03 - 16",
      "target_date": "2024 - 06 - 30",
      "resolution_role": "primary",
      "deliverables": [
        {{
          "name": "Implementation Complete",
          "description": "Solution implemented and tested",
          "type": "software",
          "target_date": "2024 - 06 - 30"
        }}
      ]
    }}
  ]
}}
"""

    def _parse_date(self, date_str: Optional[str]) -> Optional[date]:
        """Parse date string to date object."""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
