"""
ArchiMate Roadmap Generator Service - Phase 1

Automatic generation of ArchiMate 3.2 Implementation & Migration roadmaps
from identified gaps. This service orchestrates gap analysis, work package
generation, timeline creation, and plateau planning.

Features:
- Bulk gap-to-work-package generation
- Intelligent timeline sequencing based on priority and dependencies
- Plateau generation for stable states
- Progress tracking and reporting
"""
from app.services.archimate_backbone import sync_archimate_element

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app import db
from app.models.implementation_migration import (  # dead-code-ok
    Gap,
    Plateau,
    WorkPackage,
)
from app.services.archimate.gap_resolution_service import GapResolutionService
from app.services.llm_service import LLMService


class RoadmapGenerator:
    """
    Service for automatically generating ArchiMate Implementation & Migration roadmaps.

    Phase 1 focuses on gap-to-work-package generation with intelligent sequencing.

    Usage:
        generator = RoadmapGenerator()
        roadmap = generator.generate_roadmap_from_gaps(gap_ids=[1, 2, 3])
    """

    def __init__(self):
        self.gap_resolution_service = GapResolutionService()
        self.llm_service = LLMService()

    def generate_roadmap_from_gaps(
        self,
        gap_ids: Optional[List[int]] = None,
        architecture_id: Optional[int] = None,
        priority_filter: Optional[str] = None,
        include_plateaus: bool = True,
        timeline_months: int = 18,
    ) -> Dict[str, Any]:
        """
        Generate a complete roadmap from identified gaps.

        Args:
            gap_ids: Specific gap IDs to include. If None, uses all unresolved gaps
            architecture_id: Filter gaps by architecture model
            priority_filter: Filter by priority ('critical', 'high', 'medium', 'low')
            include_plateaus: Whether to generate plateaus
            timeline_months: Timeline duration in months

        Returns:
            Dict containing:
            {
                'success': True,
                'roadmap': {
                    'gaps_processed': [...],
                    'work_packages': [...],
                    'plateaus': [...],
                    'timeline': {
                        'start_date': '2024 - 01 - 01',
                        'end_date': '2025 - 06 - 30',
                        'phases': [...]
                    },
                    'statistics': {
                        'total_gaps': 10,
                        'total_work_packages': 35,
                        'estimated_total_hours': 5600,
                        'critical_path_duration_days': 450
                    }
                }
            }
        """
        try:
            # Step 1: Get gaps to process
            gaps = self._get_gaps_to_process(gap_ids, architecture_id, priority_filter)

            if not gaps:
                return {
                    "success": False,
                    "error": "No gaps found matching the criteria",
                    "roadmap": None,
                }

            # Step 2: Sort gaps by priority for sequencing
            sorted_gaps = self._sort_gaps_by_priority(gaps)

            # Step 3: Generate work packages for each gap
            all_work_packages = []
            gaps_processed = []

            for gap in sorted_gaps:
                try:
                    work_packages = self.gap_resolution_service.create_work_packages_from_gap(
                        gap_id=gap.id, auto_generate=True
                    )
                    all_work_packages.extend(work_packages)
                    gaps_processed.append(
                        {
                            "id": gap.id,
                            "name": gap.name,
                            "priority": gap.priority,
                            "work_packages_generated": len(work_packages),
                        }
                    )
                except Exception as e:
                    gaps_processed.append(
                        {"id": gap.id, "name": gap.name, "priority": gap.priority, "error": str(e)}
                    )

            # Step 4: Sequence work packages on timeline
            timeline = self._create_timeline(all_work_packages, timeline_months)

            # Step 5: Generate plateaus if requested
            plateaus = []
            if include_plateaus and all_work_packages:
                plateaus = self._generate_plateaus(all_work_packages, timeline)

            # Step 6: Calculate statistics
            statistics = self._calculate_statistics(gaps, all_work_packages, timeline)

            # Build response
            roadmap = {
                "gaps_processed": gaps_processed,
                "work_packages": [self._serialize_work_package(wp) for wp in all_work_packages],
                "plateaus": [self._serialize_plateau(p) for p in plateaus],
                "timeline": timeline,
                "statistics": statistics,
            }

            return {"success": True, "roadmap": roadmap}

        except Exception as e:
            return {"success": False, "error": str(e), "roadmap": None}

    def generate_single_gap_roadmap(self, gap_id: int) -> Dict[str, Any]:
        """
        Generate a mini-roadmap for a single gap.

        Useful for preview or quick planning of individual gaps.
        """
        return self.generate_roadmap_from_gaps(gap_ids=[gap_id], include_plateaus=False)

    def preview_roadmap(
        self, gap_ids: Optional[List[int]] = None, architecture_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Preview roadmap without actually creating work packages.

        Returns estimated structure and statistics.
        """
        gaps = self._get_gaps_to_process(gap_ids, architecture_id, None)

        if not gaps:
            return {"success": False, "error": "No gaps found", "preview": None}

        # Estimate work packages per gap based on complexity
        preview_items = []
        total_estimated_wps = 0
        total_estimated_hours = 0

        for gap in gaps:
            # Estimate 3 - 7 work packages based on severity
            estimated_wps = self._estimate_work_package_count(gap)
            estimated_hours = estimated_wps * 160  # Average 160 hours per WP

            total_estimated_wps += estimated_wps
            total_estimated_hours += estimated_hours

            preview_items.append(
                {
                    "gap_id": gap.id,
                    "gap_name": gap.name,
                    "priority": gap.priority,
                    "severity": gap.severity,
                    "estimated_work_packages": estimated_wps,
                    "estimated_hours": estimated_hours,
                    "estimated_duration_weeks": estimated_hours // 40,
                }
            )

        # Estimate timeline
        avg_parallelism = 2  # Assume 2 streams can run in parallel
        estimated_total_weeks = (total_estimated_hours // 40) // avg_parallelism

        return {
            "success": True,
            "preview": {
                "gaps": preview_items,
                "totals": {
                    "gap_count": len(gaps),
                    "estimated_work_packages": total_estimated_wps,
                    "estimated_total_hours": total_estimated_hours,
                    "estimated_timeline_weeks": estimated_total_weeks,
                    "estimated_plateaus": max(2, len(gaps) // 3),
                },
            },
        }

    def _get_gaps_to_process(
        self,
        gap_ids: Optional[List[int]],
        architecture_id: Optional[int],
        priority_filter: Optional[str],
    ) -> List[Gap]:
        """Retrieve gaps based on filters."""
        # D3: exclude interface-register plateau-transition gaps -- they are
        # not capability gaps and must not be swept into AI-generated
        # capability roadmaps/work packages.
        query = Gap.query.filter(
            Gap.resolution_status.in_(["identified", "planned"]),
            Gap.gap_kind != "plateau_transition",
        )

        if gap_ids:
            query = query.filter(Gap.id.in_(gap_ids))

        if architecture_id:
            query = query.filter(Gap.architecture_id == architecture_id)

        if priority_filter:
            query = query.filter(Gap.priority == priority_filter)

        return query.order_by(Gap.priority.desc(), Gap.created_at.asc()).all()

    def _sort_gaps_by_priority(self, gaps: List[Gap]) -> List[Gap]:
        """Sort gaps by priority for sequencing."""
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        return sorted(gaps, key=lambda g: (priority_order.get(g.priority, 4), g.id))

    def _create_timeline(
        self, work_packages: List[WorkPackage], timeline_months: int
    ) -> Dict[str, Any]:
        """Create timeline structure from work packages."""
        if not work_packages:
            start_date = date.today()
            end_date = start_date + timedelta(days=timeline_months * 30)
            return {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "phases": [],
            }

        # Find actual date range from work packages
        start_dates = [wp.start_date for wp in work_packages if wp.start_date]
        end_dates = [wp.target_date for wp in work_packages if wp.target_date]

        if start_dates:
            start_date = min(start_dates)
        else:
            start_date = date.today()

        if end_dates:
            end_date = max(end_dates)
        else:
            end_date = start_date + timedelta(days=timeline_months * 30)

        # Group work packages into phases based on sequencing
        phases = self._group_into_phases(work_packages)

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "phases": phases,
        }

    def _group_into_phases(self, work_packages: List[WorkPackage]) -> List[Dict]:
        """Group work packages into logical phases."""
        if not work_packages:
            return []

        # Group by quarter
        phases_by_quarter = defaultdict(list)

        for wp in work_packages:
            if wp.start_date:
                quarter = f"Q{(wp.start_date.month - 1) // 3 + 1} {wp.start_date.year}"
            else:
                quarter = "Unscheduled"
            phases_by_quarter[quarter].append(wp)

        phases = []
        for quarter, wps in sorted(phases_by_quarter.items()):
            phases.append(
                {
                    "name": quarter,
                    "work_package_count": len(wps),
                    "work_packages": [
                        {"id": wp.id, "name": wp.name, "status": wp.status} for wp in wps
                    ],
                }
            )

        return phases

    def _generate_plateaus(self, work_packages: List[WorkPackage], timeline: Dict) -> List[Plateau]:
        """Generate plateaus (stable states) based on work package completion."""
        if not work_packages:
            return []

        plateaus = []

        # Group work packages by target date to identify natural plateau points
        by_date = defaultdict(list)
        for wp in work_packages:
            if wp.target_date:
                # Round to end of quarter
                quarter_end = self._get_quarter_end(wp.target_date)
                by_date[quarter_end].append(wp)

        # Create plateau at each major milestone
        for i, (plateau_date, wps) in enumerate(sorted(by_date.items()), 1):
            plateau = Plateau(
                name=f"Plateau {i}: {plateau_date.strftime('%B %Y')}",
                description=f"Stable state after completing {len(wps)} work packages",
                target_date=plateau_date,
                sequence_order=i,
                architecture_id=work_packages[0].architecture_id if work_packages else None,
            )
            db.session.add(plateau)
            sync_archimate_element(plateau)
            db.session.flush()

            # Link work packages to plateau through the relationship, so the session
            # bridge sees the link and makes the ArchiMate relationship.
            for wp in wps:
                wp.plateaus.append(plateau)

            plateaus.append(plateau)

        db.session.commit()
        return plateaus

    def _get_quarter_end(self, d: date) -> date:
        """Get the end date of the quarter containing the given date."""
        quarter = (d.month - 1) // 3
        if quarter == 0:
            return date(d.year, 3, 31)
        elif quarter == 1:
            return date(d.year, 6, 30)
        elif quarter == 2:
            return date(d.year, 9, 30)
        else:
            return date(d.year, 12, 31)

    def _calculate_statistics(
        self, gaps: List[Gap], work_packages: List[WorkPackage], timeline: Dict
    ) -> Dict[str, Any]:
        """Calculate roadmap statistics."""
        total_hours = sum(wp.estimated_effort_hours or 0 for wp in work_packages)

        # Calculate critical path (simplified - assumes sequential for critical items)
        critical_wps = [wp for wp in work_packages if wp.priority == "critical"]
        critical_hours = sum(wp.estimated_effort_hours or 0 for wp in critical_wps)
        critical_days = critical_hours // 8 if critical_hours else 0

        # Count by status
        by_status = defaultdict(int)
        for wp in work_packages:
            by_status[wp.status] += 1

        # Count by priority
        by_priority = defaultdict(int)
        for wp in work_packages:
            by_priority[wp.priority] += 1

        return {
            "total_gaps": len(gaps),
            "total_work_packages": len(work_packages),
            "estimated_total_hours": total_hours,
            "critical_path_duration_days": critical_days,
            "by_status": dict(by_status),
            "by_priority": dict(by_priority),
            "average_hours_per_gap": total_hours // len(gaps) if gaps else 0,
        }

    def _estimate_work_package_count(self, gap: Gap) -> int:
        """Estimate number of work packages needed for a gap."""
        base = 4  # Default

        # Adjust based on severity
        severity_adjustment = {"critical": 2, "high": 1, "medium": 0, "low": -1}
        adjustment = severity_adjustment.get(gap.severity, 0)

        # Adjust based on description length (proxy for complexity)
        if gap.description and len(gap.description) > 500:
            adjustment += 1

        return max(2, min(8, base + adjustment))

    def _serialize_work_package(self, wp: WorkPackage) -> Dict:
        """Serialize work package to dict."""
        return {
            "id": wp.id,
            "name": wp.name,
            "description": wp.description,
            "summary": wp.summary,
            "status": wp.status,
            "priority": wp.priority,
            "start_date": wp.start_date.isoformat() if wp.start_date else None,
            "target_date": wp.target_date.isoformat() if wp.target_date else None,
            "estimated_effort_hours": wp.estimated_effort_hours,
            "sequence_order": wp.sequence_order,
        }

    def _serialize_plateau(self, plateau: Plateau) -> Dict:
        """Serialize plateau to dict."""
        return {
            "id": plateau.id,
            "name": plateau.name,
            "description": plateau.description,
            "target_date": plateau.target_date.isoformat() if plateau.target_date else None,
            "sequence_order": plateau.sequence_order,
        }


# Convenience function for quick roadmap generation
def generate_roadmap(gap_ids: Optional[List[int]] = None, **kwargs) -> Dict[str, Any]:
    """
    Convenience function for generating a roadmap.

    Usage:
        from app.services.archimate.roadmap_generator import generate_roadmap
        result = generate_roadmap(gap_ids=[1, 2, 3])
    """
    generator = RoadmapGenerator()
    return generator.generate_roadmap_from_gaps(gap_ids=gap_ids, **kwargs)
