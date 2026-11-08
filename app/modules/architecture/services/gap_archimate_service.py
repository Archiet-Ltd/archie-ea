"""
-> app.modules.architecture.services.governance_service

Gap ArchiMate Service

Converts auto-detected capability gaps to ArchiMate Gap records and manages
the relationship between gaps and work packages for roadmap planning.

ArchiMate 3.2 Implementation & Migration layer integration:
- Gap: Difference between current and target states
- WorkPackage: Unit of work to resolve gaps
- Deliverable: Outputs from work packages
- Plateau: Stable architectural state snapshots
"""

from app.services.archimate_backbone import sync_archimate_element
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from app import db
from app.models.implementation_migration import Gap, WorkPackage

# Gap type color mapping (consistent with UI)
GAP_TYPE_COLORS = {
    "coverage": "#6B7280",  # Gray
    "quality": "#EAB308",  # Yellow
    "retirement": "#EF4444",  # Red
    "modernization": "#A855F7",  # Purple
    "custom": "#3B82F6",  # Blue
}

# Priority to severity mapping
PRIORITY_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}

# Default resolution timeframes by priority (days)
PRIORITY_TIMEFRAMES = {
    "critical": 90,
    "high": 180,
    "medium": 365,
    "low": 545,
}


class GapArchiMateService:
    """
    Service for managing ArchiMate Gap and WorkPackage lifecycle.

    Provides:
    - Conversion from auto-detected capability gaps to ArchiMate Gaps
    - WorkPackage creation for gap resolution
    - Hierarchical work breakdown structure management
    - Gap-WorkPackage relationship management
    """

    def __init__(self):
        pass

    # =========================================================================
    # Gap Management
    # =========================================================================

    def find_existing_gap(
        self, source_capability_type: str, source_capability_id: int
    ) -> Optional[Gap]:
        """Find an existing ArchiMate Gap for a capability."""
        if not source_capability_type or not source_capability_id:
            return None
        return Gap.query.filter_by(
            source_capability_type=source_capability_type, source_capability_id=source_capability_id
        ).first()

    def convert_capability_gap_to_archimate(
        self, capability_gap_data: Dict, update_existing: bool = True
    ) -> Tuple[Gap, bool]:
        """
        Convert auto-detected capability gap to ArchiMate Gap model.

        Args:
            capability_gap_data: {
                'capability_id': 123,
                'capability_type': 'business',  # or 'technical', 'process'
                'name': 'Customer Management',
                'domain': 'CUST',
                'gap_types': ['coverage', 'quality'],
                'gap_details': ['No applications mapped', '3 tactical apps'],
                'priority': 'high',
                'strategic_importance': 'high',
                'applications': [...],
                'start_date': '2026 - 01 - 01',
                'end_date': '2026 - 06 - 30'
            }
            update_existing: If True, update existing gap; if False, skip

        Returns:
            Tuple of (Gap instance, is_new: bool)
        """
        cap_type = capability_gap_data.get("capability_type")
        cap_id = capability_gap_data.get("capability_id")

        # Check for existing gap
        existing_gap = self.find_existing_gap(cap_type, cap_id)

        if existing_gap:
            if update_existing:
                self._update_gap_from_data(existing_gap, capability_gap_data)
                return existing_gap, False
            return existing_gap, False

        # Create new gap
        gap = self._create_gap_from_data(capability_gap_data)
        db.session.add(gap)

        return gap, True

    def _create_gap_from_data(self, data: Dict) -> Gap:
        """Create a new Gap from capability gap data."""
        gap_types = data.get("gap_types", [])
        primary_gap_type = gap_types[0] if gap_types else "custom"
        priority = data.get("priority", "medium")

        # Calculate dates
        start_date = self._parse_date(data.get("start_date"))
        end_date = self._parse_date(data.get("end_date"))

        if not start_date:
            start_date = date.today()
        if not end_date:
            days = PRIORITY_TIMEFRAMES.get(priority, 365)
            end_date = start_date + timedelta(days=days)

        gap = Gap(
            name=f"Gap: {data.get('name', 'Unknown Capability')}",
            description=self._generate_gap_description(data),
            gap_type=primary_gap_type,
            gap_sub_types=gap_types[1:] if len(gap_types) > 1 else None,
            color=GAP_TYPE_COLORS.get(primary_gap_type, "#6B7280"),
            source_capability_type=data.get("capability_type"),
            source_capability_id=data.get("capability_id"),
            priority=priority,
            severity=PRIORITY_SEVERITY_MAP.get(priority, "medium"),
            impact=data.get("strategic_importance", "medium"),
            resolution_status="identified",
            estimated_start_date=start_date,
            target_resolution_date=end_date,
            owner=data.get("business_owner"),
            business_value=data.get("strategic_importance"),
        )

        return gap

    def _update_gap_from_data(self, gap: Gap, data: Dict):
        """Update existing gap with new data."""
        gap_types = data.get("gap_types", [])

        if gap_types:
            gap.set_gap_types(gap_types)
            gap.color = GAP_TYPE_COLORS.get(gap_types[0], gap.color)

        if data.get("priority"):
            gap.priority = data["priority"]
            gap.severity = PRIORITY_SEVERITY_MAP.get(data["priority"], gap.severity)

        if data.get("strategic_importance"):
            gap.impact = data["strategic_importance"]
            gap.business_value = data["strategic_importance"]

        # Update description with latest details
        gap.description = self._generate_gap_description(data)

    def _generate_gap_description(self, data: Dict) -> str:
        """Generate descriptive text for a gap."""
        lines = [f"Capability: {data.get('name', 'Unknown')}"]

        if data.get("domain"):
            lines.append(f"Domain: {data['domain']}")

        gap_details = data.get("gap_details", [])
        if gap_details:
            lines.append("\nGap Details:")
            for detail in gap_details:
                lines.append(f"  - {detail}")

        apps = data.get("applications", [])
        if apps:
            lines.append(f"\nAffected Applications: {len(apps)}")
            for app in apps[:5]:  # Show first 5
                status = app.get("lifecycle_status", "")
                risk = app.get("technical_risk", "")
                lines.append(f"  - {app.get('name', 'Unknown')} [{status}] [{risk}]")
            if len(apps) > 5:
                lines.append(f"  ... and {len(apps) - 5} more")

        return "\n".join(lines)

    def _parse_date(self, date_str: Optional[str]) -> Optional[date]:
        """Parse ISO date string to date object."""
        if not date_str:
            return None
        try:
            return date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return None

    def update_gap(self, gap_id: int, updates: Dict) -> Optional[Gap]:
        """
        Update a gap with new values.

        Args:
            gap_id: Gap ID
            updates: Dict of field -> value pairs

        Returns:
            Updated Gap or None if not found
        """
        gap = Gap.query.get(gap_id)
        if not gap:
            return None

        # Updateable fields
        allowed_fields = {
            "name",
            "description",
            "gap_type",
            "color",
            "priority",
            "severity",
            "impact",
            "resolution_status",
            "owner",
            "estimated_start_date",
            "target_resolution_date",
            "estimated_effort_days",
            "estimated_cost",
            "business_value",
        }

        # Critical is held in both fields, so choosing or removing it moves
        # them together (an explicit severity in the same update still wins).
        if updates.get("priority") and "severity" not in updates:
            updates = {**updates, "severity": PRIORITY_SEVERITY_MAP.get(updates["priority"], gap.severity)}

        for field, value in updates.items():
            if field in allowed_fields and hasattr(gap, field):
                # Handle date fields
                if field in ("estimated_start_date", "target_resolution_date"):
                    value = self._parse_date(value) if isinstance(value, str) else value
                setattr(gap, field, value)

        # Handle gap_types specially
        if "gap_types" in updates:
            gap.set_gap_types(updates["gap_types"])

        return gap

    def delete_gap(self, gap_id: int) -> bool:
        """Delete a gap and its associations."""
        gap = Gap.query.get(gap_id)
        if not gap:
            return False

        db.session.delete(gap)
        return True

    # =========================================================================
    # WorkPackage Management
    # =========================================================================

    def create_work_package_for_gap(
        self, gap: Gap, work_package_data: Optional[Dict] = None
    ) -> WorkPackage:
        """
        Create a WorkPackage to resolve a Gap.

        Args:
            gap: The Gap to resolve
            work_package_data: Optional override data

        Returns:
            Created WorkPackage
        """
        data = work_package_data or {}

        wp = WorkPackage(
            name=data.get("name", f"Resolve: {gap.name}"),
            summary=data.get("summary", f"Work package to resolve {gap.gap_type} gap"),
            description=data.get("description", gap.description),
            start_date=self._parse_date(data.get("start_date"))
            or gap.estimated_start_date
            or date.today(),
            target_date=self._parse_date(data.get("target_date")) or gap.target_resolution_date,
            priority=data.get("priority", gap.priority),
            status="planned",
            level=data.get("level", 1),
            color=data.get("color", gap.color),
            estimated_cost=data.get("estimated_cost", gap.estimated_cost),
        )

        # Link to gap
        wp.gaps.append(gap)

        db.session.add(wp)
        sync_archimate_element(wp)
        return wp

    def create_child_work_package(self, parent: WorkPackage, data: Dict) -> WorkPackage:
        """
        Create a child work package under a parent.

        Args:
            parent: Parent WorkPackage
            data: Child work package data

        Returns:
            Created child WorkPackage
        """
        child = WorkPackage(
            name=data.get("name", f"Task for {parent.name}"),
            summary=data.get("summary"),
            description=data.get("description"),
            parent_id=parent.id,
            level=(parent.level or 1) + 1,
            start_date=self._parse_date(data.get("start_date")) or parent.start_date,
            target_date=self._parse_date(data.get("target_date")) or parent.target_date,
            priority=data.get("priority", parent.priority),
            status="planned",
            color=data.get("color"),  # Will inherit from parent via get_effective_color()
            estimated_effort_hours=data.get("estimated_effort_hours"),
            estimated_cost=data.get("estimated_cost"),
        )

        # Inherit gap associations from parent
        for gap in parent.gaps:
            child.gaps.append(gap)

        db.session.add(child)
        sync_archimate_element(child)
        return child

    def create_standard_work_breakdown(self, gap: Gap, template: str = "default") -> WorkPackage:
        """
        Create a work package with standard work breakdown structure.

        Templates:
        - default: Requirements, Design, Implementation, Testing, Deployment
        - vendor_selection: RFP, Evaluation, POC, Contract, Implementation
        - modernization: Assessment, Planning, Migration, Validation, Cutover
        - retirement: Impact Analysis, Migration, Data Archive, Decommission

        Args:
            gap: Gap to resolve
            template: Work breakdown template name

        Returns:
            Root WorkPackage with children
        """
        templates = {
            "default": [
                ("Requirements Gathering", 0.1),
                ("Solution Design", 0.15),
                ("Implementation", 0.4),
                ("Testing & QA", 0.2),
                ("Deployment & Go-Live", 0.15),
            ],
            "vendor_selection": [
                ("Define Requirements", 0.15),
                ("RFP & Vendor Outreach", 0.15),
                ("Vendor Evaluation", 0.2),
                ("POC & Validation", 0.2),
                ("Contract Negotiation", 0.1),
                ("Implementation Planning", 0.2),
            ],
            "modernization": [
                ("Current State Assessment", 0.15),
                ("Target Architecture Design", 0.15),
                ("Migration Planning", 0.1),
                ("Implementation", 0.35),
                ("Testing & Validation", 0.15),
                ("Cutover & Stabilization", 0.1),
            ],
            "retirement": [
                ("Impact & Dependency Analysis", 0.2),
                ("Migration Planning", 0.15),
                ("Data Migration/Archive", 0.25),
                ("User Transition", 0.15),
                ("System Decommission", 0.15),
                ("Documentation & Closeout", 0.1),
            ],
        }

        # Select template based on gap type
        if template == "auto":
            if gap.gap_type == "retirement":
                template = "retirement"
            elif gap.gap_type == "modernization":
                template = "modernization"
            elif gap.gap_type == "coverage":
                template = "vendor_selection"
            else:
                template = "default"

        work_items = templates.get(template, templates["default"])

        # Create root work package
        root_wp = self.create_work_package_for_gap(gap)
        db.session.flush()  # Get ID for parent reference

        # Calculate timeline distribution
        if root_wp.start_date and root_wp.target_date:
            total_days = (root_wp.target_date - root_wp.start_date).days
            current_start = root_wp.start_date

            for name, weight in work_items:
                duration_days = int(total_days * weight)
                child_end = current_start + timedelta(days=duration_days)

                self.create_child_work_package(
                    root_wp,
                    {
                        "name": name,
                        "start_date": current_start.isoformat(),
                        "target_date": child_end.isoformat(),
                    },
                )

                current_start = child_end
        else:
            # No dates, just create the structure
            for name, _ in work_items:
                self.create_child_work_package(root_wp, {"name": name})

        return root_wp

    def update_work_package(self, wp_id: int, updates: Dict) -> Optional[WorkPackage]:
        """Update a work package."""
        wp = WorkPackage.query.get(wp_id)
        if not wp:
            return None

        allowed_fields = {
            "name",
            "summary",
            "description",
            "start_date",
            "target_date",
            "status",
            "priority",
            "level",
            "color",
            "percent_complete",
            "estimated_effort_hours",
            "actual_effort_hours",
            "estimated_cost",
            "actual_cost",
            "dependencies",
            # F-08(b), Capgemini dry-run: the Capability Roadmap's edit form
            # has always offered "Target Plateau" — it was accepted (200) and
            # silently dropped because this whitelist never included it.
            "plateau_id",
        }

        for field, value in updates.items():
            if field in allowed_fields and hasattr(wp, field):
                if field in ("start_date", "target_date", "completed_date"):
                    value = self._parse_date(value) if isinstance(value, str) else value
                setattr(wp, field, value)

        # Handle status changes
        if updates.get("status") == "completed" and not wp.completed_date:
            wp.completed_date = date.today()
            wp.percent_complete = 100

        return wp

    def delete_work_package(self, wp_id: int, cascade: bool = True) -> bool:
        """
        Delete a work package.

        Args:
            wp_id: WorkPackage ID
            cascade: If True, delete children too

        Returns:
            True if deleted
        """
        wp = WorkPackage.query.get(wp_id)
        if not wp:
            return False

        if cascade:
            # Delete children first (handled by cascade in model)
            pass

        db.session.delete(wp)
        return True

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def bulk_convert_gaps(self, capability_gaps: List[Dict], commit: bool = True) -> Dict:
        """
        Bulk convert auto-detected gaps to ArchiMate Gaps.

        Args:
            capability_gaps: List of capability gap data dicts
            commit: Whether to commit the transaction

        Returns:
            Summary dict with counts
        """
        created = 0
        updated = 0
        errors = []

        for gap_data in capability_gaps:
            try:
                gap, is_new = self.convert_capability_gap_to_archimate(gap_data)
                if is_new:
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                errors.append({"capability_id": gap_data.get("capability_id"), "error": str(e)})

        if commit:
            db.session.commit()

        return {
            "created": created,
            "updated": updated,
            "errors": errors,
            "total_processed": len(capability_gaps),
        }

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_gaps_for_roadmap(self, filters: Optional[Dict] = None) -> List[Gap]:
        """
        Get gaps formatted for roadmap display.

        Args:
            filters: Optional filters (gap_type, priority, resolution_status, etc.)

        Returns:
            List of Gap objects
        """
        # D3: exclude interface-register plateau-transition gaps -- this
        # feeds capability roadmap display, not the interface register.
        query = Gap.query.filter(Gap.gap_kind != "plateau_transition")

        if filters:
            if filters.get("gap_type"):
                query = query.filter(Gap.gap_type == filters["gap_type"])
            if filters.get("priority"):
                query = query.filter(Gap.priority == filters["priority"])
            if filters.get("resolution_status"):
                query = query.filter(Gap.resolution_status == filters["resolution_status"])
            if filters.get("source_capability_type"):
                query = query.filter(
                    Gap.source_capability_type == filters["source_capability_type"]
                )

        return query.order_by(Gap.priority.desc(), Gap.estimated_start_date).all()

    def get_work_packages_for_gap(self, gap_id: int) -> List[WorkPackage]:
        """Get all work packages for a gap."""
        gap = Gap.query.get(gap_id)
        if not gap:
            return []
        return list(gap.work_packages)

    def get_hierarchical_work_packages(
        self, root_only: bool = True, gap_id: Optional[int] = None
    ) -> List[WorkPackage]:
        """
        Get work packages in hierarchical structure.

        Args:
            root_only: If True, only return root-level packages (with children nested)
            gap_id: Optional filter by gap

        Returns:
            List of WorkPackage objects
        """
        query = WorkPackage.query

        if root_only:
            query = query.filter(WorkPackage.parent_id.is_(None))

        if gap_id:
            from app.models.relationship_tables import gap_work_packages

            query = query.join(
                gap_work_packages, WorkPackage.id == gap_work_packages.c.work_package_id
            ).filter(gap_work_packages.c.gap_id == gap_id)

        try:
            return query.order_by(WorkPackage.start_date, WorkPackage.sequence_order).all()
        except Exception:
            # Fallback: sequence_order may not exist in older DB schemas
            return query.order_by(WorkPackage.start_date).all()


# Create singleton instance
gap_archimate_service = GapArchiMateService()
