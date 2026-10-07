"""
Roadmap Builder Service

Enhanced roadmap management with work package dependencies and plateau modeling.
Provides backend functionality for building implementation roadmaps with dependency
graphs suitable for ReactFlow visualization.

Key Features:
- Work package CRUD with dependency management
- Dependency graph generation for ReactFlow
- Plateau state management and transitions
- Critical path analysis
- Roadmap timeline generation
- Integration with existing capability roadmap

Reuses:
- ImplementationWorkPackage model
- ImplementationPlateau model
- technology_roadmap_service.py patterns
- capability_roadmap_dashboard_service.py patterns
"""

import logging
from collections import defaultdict
from dataclasses import dataclass  # dead-code-ok
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import or_  # dead-code-ok

from app import db
from app.models.implementation_migration import (  # dead-code-ok
    Gap as ImplementationGap,
    WorkPackage as ImplementationWorkPackage,
)
from app.models.implementation_planning import (
    ImplementationPlateau,
)
from app.services import work_package_service
from app.utils.tenant import current_organization_id

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes for Dependency Graph (ReactFlow Compatible)
# =============================================================================


@dataclass
class GraphNode:
    """ReactFlow-compatible node for dependency graph."""

    id: str
    type: str  # work_package, plateau, milestone, gap
    position: Dict[str, float]
    data: Dict[str, Any]


@dataclass
class GraphEdge:
    """ReactFlow-compatible edge for dependency graph."""

    id: str
    source: str
    target: str
    type: str = "smoothstep"
    animated: bool = False
    label: Optional[str] = None
    style: Optional[Dict[str, Any]] = None


class RoadmapBuilderService:
    """
    Service for building and managing implementation roadmaps.

    Provides comprehensive roadmap planning capabilities:
    - Work package management with dependencies
    - Plateau-based architecture state transitions
    - Dependency graph generation for visualization
    - Critical path analysis
    - Timeline optimization
    """

    def __init__(self):
        pass

    # =========================================================================
    # Work Package Management
    # =========================================================================

    def create_work_package(
        self,
        name: str,
        description: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        priority: str = "medium",
        status: str = "planned",
        assigned_to: Optional[str] = None,
        estimated_cost: float = 0.0,
        dependencies: Optional[List[int]] = None,
        capability_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new work package in the one work package store.

        Args:
            name: Name of the work package
            description: Detailed description
            start_date: Planned start date
            end_date: Planned end date
            priority: Priority level (low, medium, high, critical)
            status: Current status (planned, in_progress, completed, cancelled)
            assigned_to: Person/team assigned
            estimated_cost: Estimated cost
            dependencies: List of work package IDs this depends on
            capability_id: Optional linked capability
            created_by: Unused; the signed-in user is recorded

        Returns:
            Dict with created work package details
        """
        org_id = current_organization_id()
        try:
            from flask_login import current_user

            work_package = work_package_service.create_work_package(
                organization_id=org_id,
                user_id=current_user.id if current_user.is_authenticated else None,
                name=name,
                description=description,
                start_date=start_date,
                end_date=end_date,
                priority=priority,
                status=status,
                assigned_to=assigned_to,
                estimated_cost=estimated_cost,
                capability_id=capability_id,
            )
            for dependency_id in dependencies or []:
                work_package_service.add_dependency(
                    work_package.id, dependency_id, organization_id=org_id
                )
            db.session.commit()

            return {
                "success": True,
                "work_package": work_package_service.to_dict(work_package),
                "message": f"Work package '{name}' created successfully",
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating work package: {e}")
            return {"success": False, "error": str(e)}

    def update_work_package(self, work_package_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing work package.

        Args:
            work_package_id: ID of work package to update
            updates: Dict of field updates

        Returns:
            Dict with updated work package
        """
        org_id = current_organization_id()
        try:
            from flask_login import current_user

            work_package = work_package_service.get_work_package(work_package_id, org_id)
            if not work_package:
                return {"success": False, "error": "Work package not found"}

            # Update allowed fields
            allowed_fields = [
                "name",
                "description",
                "start_date",
                "end_date",
                "priority",
                "status",
                "assigned_to",
                "estimated_cost",
                "actual_cost",
                "progress_percentage",
                "risk_level",
                "risk_mitigation",
                "prerequisites",
            ]
            fields = {k: v for k, v in updates.items() if k in allowed_fields}
            work_package_service.update_work_package(
                work_package_id,
                organization_id=org_id,
                user_id=current_user.id if current_user.is_authenticated else None,
                **fields,
            )
            if "work_dependencies" in updates:
                wanted = [int(d) for d in updates["work_dependencies"] or []]
                for current in work_package_service.dependency_ids(work_package):
                    if current not in wanted:
                        work_package_service.remove_dependency(
                            work_package_id, current, organization_id=org_id
                        )
                for dependency_id in wanted:
                    work_package_service.add_dependency(
                        work_package_id, dependency_id, organization_id=org_id
                    )
            db.session.commit()

            return {
                "success": True,
                "work_package": work_package_service.to_dict(work_package),
                "message": "Work package updated successfully",
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating work package: {e}")
            return {"success": False, "error": str(e)}

    def delete_work_package(self, work_package_id: int) -> Dict[str, Any]:
        """
        Delete a work package.

        Args:
            work_package_id: ID of work package to delete

        Returns:
            Dict with deletion result
        """
        org_id = current_organization_id()
        try:
            work_package = work_package_service.get_work_package(work_package_id, org_id)
            if not work_package:
                return {"success": False, "error": "Work package not found"}

            # delete_work_package removes this id from the dependencies of the
            # work packages that listed it.
            dependents = work_package_service.dependents_of(work_package_id, org_id)
            work_package_service.delete_work_package(work_package_id, organization_id=org_id)
            db.session.commit()

            return {
                "success": True,
                "message": f"Work package {work_package_id} deleted",
                "affected_dependents": len(dependents),
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting work package: {e}")
            return {"success": False, "error": str(e)}

    def get_work_package(self, work_package_id: int) -> Dict[str, Any]:
        """
        Get a work package by ID with dependency details.

        Args:
            work_package_id: ID of work package

        Returns:
            Dict with work package details
        """
        org_id = current_organization_id()
        work_package = work_package_service.get_work_package(work_package_id, org_id)
        if not work_package:
            return {"success": False, "error": "Work package not found"}

        # Get dependency details
        dependencies = []
        for dep_id in work_package_service.dependency_ids(work_package):
            dep = work_package_service.get_work_package(dep_id, org_id)
            if dep:
                dependencies.append(
                    {
                        "id": dep.id,
                        "name": dep.name,
                        "status": dep.status,
                        "end_date": dep.end_date.isoformat() if dep.end_date else None,
                    }
                )

        # Get dependents (packages that depend on this one)
        dependents = work_package_service.dependents_of(work_package_id, org_id)

        result = work_package_service.to_dict(work_package)
        result["dependencies_detail"] = dependencies
        result["dependents"] = [
            {"id": d.id, "name": d.name, "status": d.status} for d in dependents
        ]

        return {"success": True, "work_package": result}

    def list_work_packages(
        self,
        status_filter: Optional[str] = None,
        priority_filter: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        List this organisation's work packages with optional filters.

        Args:
            status_filter: Filter by status
            priority_filter: Filter by priority
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dict with list of work packages
        """
        from app.models.unified_work_package import UnifiedWorkPackage as UWP

        query = work_package_service.query_for(current_organization_id())

        if status_filter:
            query = query.filter(UWP.status == status_filter)
        if priority_filter:
            query = query.filter(UWP.priority == priority_filter)

        total = query.count()
        work_packages = (
            query.order_by(UWP.start_date.asc().nullsfirst(), UWP.id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return {
            "success": True,
            "work_packages": [work_package_service.to_dict(wp) for wp in work_packages],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def add_dependency(self, work_package_id: int, depends_on_id: int) -> Dict[str, Any]:
        """
        Add a dependency between work packages.

        Args:
            work_package_id: ID of work package that will depend on another
            depends_on_id: ID of work package to depend on

        Returns:
            Dict with result
        """
        if work_package_id == depends_on_id:
            return {"success": False, "error": "Cannot depend on itself"}

        org_id = current_organization_id()
        work_package = work_package_service.get_work_package(work_package_id, org_id)
        depends_on = work_package_service.get_work_package(depends_on_id, org_id)

        if not work_package:
            return {"success": False, "error": "Work package not found"}
        if not depends_on:
            return {"success": False, "error": "Dependency work package not found"}

        try:
            work_package_service.add_dependency(
                work_package_id, depends_on_id, organization_id=org_id
            )
            db.session.commit()
        except work_package_service.WorkPackageError:
            db.session.rollback()
            return {"success": False, "error": "Would create circular dependency"}

        return {
            "success": True,
            "message": f"Dependency added: {work_package.name} depends on {depends_on.name}",
            "dependencies": work_package_service.dependency_ids(work_package),
        }

    def remove_dependency(self, work_package_id: int, depends_on_id: int) -> Dict[str, Any]:
        """
        Remove a dependency between work packages.

        Args:
            work_package_id: ID of work package
            depends_on_id: ID of dependency to remove

        Returns:
            Dict with result
        """
        org_id = current_organization_id()
        work_package = work_package_service.get_work_package(work_package_id, org_id)
        if not work_package:
            return {"success": False, "error": "Work package not found"}

        work_package_service.remove_dependency(
            work_package_id, depends_on_id, organization_id=org_id
        )
        db.session.commit()

        return {
            "success": True,
            "message": "Dependency removed",
            "dependencies": work_package_service.dependency_ids(work_package),
        }

    # =========================================================================
    # Dependency Graph Generation (ReactFlow Compatible)
    # =========================================================================

    def generate_dependency_graph(
        self,
        include_plateaus: bool = True,
        include_gaps: bool = False,
        status_filter: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate dependency graph data compatible with ReactFlow.

        Args:
            include_plateaus: Include plateau nodes
            include_gaps: Include gap nodes
            status_filter: Optional list of statuses to include

        Returns:
            Dict with nodes and edges for ReactFlow
        """
        nodes = []
        edges = []

        # Get work packages
        query = ImplementationWorkPackage.query
        if status_filter:
            query = query.filter(ImplementationWorkPackage.status.in_(status_filter))

        work_packages = query.order_by(ImplementationWorkPackage.start_date.asc()).all()

        # Calculate layout positions using topological sort levels
        levels = self._calculate_topological_levels(work_packages)
        level_counts = defaultdict(int)

        # Create work package nodes
        for wp in work_packages:
            level = levels.get(wp.id, 0)
            y_offset = level_counts[level] * 150

            # The work package's own roadmap serialiser is the one mapping of
            # its fields (percent_complete, target_date, owner) to roadmap
            # names; reuse it here instead of reading attribute names the
            # model does not have.
            row = wp.to_roadmap_dict()
            node_data = {
                "label": wp.name,
                "status": wp.status,
                "priority": wp.priority,
                "progress": row["percent_complete"],
                "startDate": row["start_date"],
                "endDate": row["end_date"],
                "assignedTo": row["owner_name"],
                "estimatedCost": float(wp.estimated_cost) if wp.estimated_cost else 0,
                "isOverdue": row["is_overdue"],
                "workPackageId": wp.id,
            }

            nodes.append(
                {
                    "id": f"wp-{wp.id}",
                    "type": "workPackage",
                    "position": {"x": level * 300, "y": y_offset},
                    "data": node_data,
                }
            )

            level_counts[level] += 1

            # Create edges for dependencies
            if wp.dependencies:
                for dep_id in wp.dependencies:
                    edges.append(
                        {
                            "id": f"e-{dep_id}-{wp.id}",
                            "source": f"wp-{dep_id}",
                            "target": f"wp-{wp.id}",
                            "type": "smoothstep",
                            "animated": wp.status == "in_progress",
                            "style": self._get_edge_style(wp.status),
                        }
                    )

        # Include plateaus if requested
        if include_plateaus:
            plateau_nodes, plateau_edges = self._generate_plateau_graph_elements(
                len(nodes), max(levels.values()) + 1 if levels else 0
            )
            nodes.extend(plateau_nodes)
            edges.extend(plateau_edges)

        # Include gaps if requested
        if include_gaps:
            gap_nodes = self._generate_gap_graph_elements(len(nodes))
            nodes.extend(gap_nodes)

        return {
            "success": True,
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total_work_packages": len(work_packages),
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "max_depth": max(levels.values()) if levels else 0,
            },
        }

    def _calculate_topological_levels(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> Dict[int, int]:
        """Calculate topological levels for layout."""
        levels = {}
        wp_map = {wp.id: wp for wp in work_packages}

        def get_level(wp_id: int, visited: Set[int]) -> int:
            if wp_id in levels:
                return levels[wp_id]
            if wp_id in visited:
                return 0  # Cycle detected, return 0
            if wp_id not in wp_map:
                return 0

            visited.add(wp_id)
            wp = wp_map[wp_id]
            deps = wp.dependencies or []

            if not deps:
                level = 0
            else:
                level = max(get_level(dep_id, visited) for dep_id in deps) + 1

            levels[wp_id] = level
            return level

        for wp in work_packages:
            get_level(wp.id, set())

        return levels

    def _get_edge_style(self, status: str) -> Dict[str, Any]:
        """Get edge style based on status."""
        styles = {
            "completed": {"stroke": "#22c55e", "strokeWidth": 2},
            "in_progress": {"stroke": "#3b82f6", "strokeWidth": 2},
            "planned": {"stroke": "#94a3b8", "strokeWidth": 1},
            "cancelled": {"stroke": "#ef4444", "strokeWidth": 1, "strokeDasharray": "5,5"},
            "on_hold": {"stroke": "#f59e0b", "strokeWidth": 1, "strokeDasharray": "5,5"},
        }
        return styles.get(status, styles["planned"])

    def _generate_plateau_graph_elements(
        self, node_offset: int, level_offset: int
    ) -> Tuple[List[Dict], List[Dict]]:
        """Generate plateau nodes and edges."""
        nodes = []
        edges = []

        plateaus = ImplementationPlateau.query.order_by(
            ImplementationPlateau.start_date.asc()
        ).all()

        for idx, plateau in enumerate(plateaus):
            node_data = {
                "label": plateau.name,
                "plateauType": plateau.plateau_type,
                "startDate": plateau.start_date.isoformat() if plateau.start_date else None,
                "endDate": plateau.end_date.isoformat() if plateau.end_date else None,
                "businessValue": plateau.business_value,
                "complianceStatus": plateau.compliance_status,
                "plateauId": plateau.id,
            }

            nodes.append(
                {
                    "id": f"plateau-{plateau.id}",
                    "type": "plateau",
                    "position": {"x": (level_offset + 1) * 300, "y": idx * 200},
                    "data": node_data,
                }
            )

            # Create transition edges
            if plateau.transition_from_plateau_id:
                edges.append(
                    {
                        "id": f"pt-{plateau.transition_from_plateau_id}-{plateau.id}",
                        "source": f"plateau-{plateau.transition_from_plateau_id}",
                        "target": f"plateau-{plateau.id}",
                        "type": "smoothstep",
                        "label": "transitions to",
                        "style": {"stroke": "#8b5cf6", "strokeWidth": 2},
                    }
                )

        return nodes, edges

    def _generate_gap_graph_elements(self, node_offset: int) -> List[Dict]:
        """Generate gap nodes."""
        nodes = []

        gaps = ImplementationGap.query.filter(
            ImplementationGap.resolution_status.notin_(["resolved", "closed"])
        ).all()

        for idx, gap in enumerate(gaps):
            node_data = {
                "label": gap.name,
                "gapType": gap.gap_type,
                # Gap has no impact_level or urgency column; impact and
                # severity are the real fields (priority when severity is
                # unset), the same fix as the work package fields above.
                "impactLevel": gap.impact,
                "urgency": gap.severity or gap.priority,
                "status": gap.resolution_status,
                "gapId": gap.id,
            }

            nodes.append(
                {
                    "id": f"gap-{gap.id}",
                    "type": "gap",
                    "position": {"x": -200, "y": idx * 120},
                    "data": node_data,
                }
            )

        return nodes

    # =========================================================================
    # Plateau Management
    # =========================================================================

    def create_plateau(
        self,
        name: str,
        plateau_type: str = "interim",
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        description: Optional[str] = None,
        business_value: Optional[str] = None,
        transition_from_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new architecture plateau.

        Args:
            name: Name of the plateau
            plateau_type: Type (baseline, interim, target, future)
            start_date: Start date of plateau
            end_date: End date of plateau
            description: Description
            business_value: Expected business value
            transition_from_id: ID of plateau this transitions from
            created_by: Creator identifier

        Returns:
            Dict with created plateau
        """
        try:
            plateau = ImplementationPlateau(
                name=name,
                plateau_type=plateau_type,
                start_date=start_date,
                end_date=end_date,
                description=description,
                business_value=business_value,
                transition_from_plateau_id=transition_from_id,
                created_by=created_by,
                created_at=datetime.utcnow(),
            )

            db.session.add(plateau)
            db.session.commit()

            # Update transition_to on source plateau
            if transition_from_id:
                source_plateau = db.session.get(ImplementationPlateau, transition_from_id)
                if source_plateau:
                    source_plateau.transition_to_plateau_id = plateau.id
                    db.session.commit()

            return {
                "success": True,
                "plateau": plateau.to_dict(),
                "message": f"Plateau '{name}' created successfully",
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating plateau: {e}")
            return {"success": False, "error": str(e)}

    def list_plateaus(self) -> Dict[str, Any]:
        """
        List all plateaus in chronological order.

        Returns:
            Dict with list of plateaus
        """
        plateaus = ImplementationPlateau.query.order_by(
            ImplementationPlateau.start_date.asc()
        ).all()

        return {
            "success": True,
            "plateaus": [p.to_dict() for p in plateaus],
            "total": len(plateaus),
        }

    def get_plateau_timeline(self) -> Dict[str, Any]:
        """
        Get plateau timeline for visualization.

        Returns:
            Dict with timeline data
        """
        plateaus = ImplementationPlateau.query.order_by(
            ImplementationPlateau.start_date.asc()
        ).all()

        timeline = []
        for plateau in plateaus:
            timeline.append(
                {
                    "id": plateau.id,
                    "name": plateau.name,
                    "type": plateau.plateau_type,
                    "start": plateau.start_date.isoformat() if plateau.start_date else None,
                    "end": plateau.end_date.isoformat() if plateau.end_date else None,
                    "isCurrent": plateau.is_current(),
                    "transitionFrom": plateau.transition_from_plateau_id,
                    "transitionTo": plateau.transition_to_plateau_id,
                    "businessValue": plateau.business_value,
                }
            )

        return {
            "success": True,
            "timeline": timeline,
            "current_plateau": next((t for t in timeline if t["isCurrent"]), None),
        }

    # =========================================================================
    # Critical Path Analysis
    # =========================================================================

    def calculate_critical_path(self) -> Dict[str, Any]:
        """
        Calculate the critical path through work packages.

        Returns:
            Dict with critical path analysis
        """
        work_packages = ImplementationWorkPackage.query.filter(
            ImplementationWorkPackage.status.notin_(["completed", "cancelled"])
        ).all()

        if not work_packages:
            return {"success": True, "critical_path": [], "total_duration": 0}

        # Build adjacency list and calculate earliest/latest times
        wp_map = {wp.id: wp for wp in work_packages}
        earliest_start = {}
        earliest_finish = {}
        latest_start = {}
        latest_finish = {}

        # Forward pass - calculate earliest times
        def calc_earliest(wp_id: int) -> Tuple[date, date]:
            if wp_id in earliest_start:
                return earliest_start[wp_id], earliest_finish[wp_id]

            wp = wp_map.get(wp_id)
            if not wp:
                today = date.today()
                return today, today

            deps = wp.dependencies or []
            if not deps:
                es = wp.start_date or date.today()
            else:
                dep_finishes = []
                for dep_id in deps:
                    _, ef = calc_earliest(dep_id)
                    dep_finishes.append(ef)
                es = max(dep_finishes) if dep_finishes else (wp.start_date or date.today())

            duration = wp.duration_days or 30
            ef = es + timedelta(days=duration)

            earliest_start[wp_id] = es
            earliest_finish[wp_id] = ef
            return es, ef

        for wp in work_packages:
            calc_earliest(wp.id)

        # Find end nodes (no dependents)
        all_deps = set()
        for wp in work_packages:
            if wp.dependencies:
                all_deps.update(wp.dependencies)

        end_nodes = [wp for wp in work_packages if wp.id not in all_deps]

        if not end_nodes:
            # All nodes have dependents, take the ones with latest finish
            end_nodes = work_packages

        project_end = max(earliest_finish.get(wp.id, date.today()) for wp in end_nodes)

        # Backward pass - calculate latest times
        def calc_latest(wp_id: int, project_end: date) -> Tuple[date, date]:
            if wp_id in latest_finish:
                return latest_start[wp_id], latest_finish[wp_id]

            wp = wp_map.get(wp_id)
            if not wp:
                return project_end, project_end

            # Find dependents
            dependents = [
                w for w in work_packages if w.dependencies and wp_id in w.dependencies
            ]

            if not dependents:
                lf = project_end
            else:
                dep_starts = []
                for dep in dependents:
                    ls, _ = calc_latest(dep.id, project_end)
                    dep_starts.append(ls)
                lf = min(dep_starts) if dep_starts else project_end

            duration = wp.duration_days or 30
            ls = lf - timedelta(days=duration)

            latest_start[wp_id] = ls
            latest_finish[wp_id] = lf
            return ls, lf

        for wp in work_packages:
            calc_latest(wp.id, project_end)

        # Calculate slack and identify critical path
        critical_path = []
        slack_analysis = []

        for wp in work_packages:
            es = earliest_start.get(wp.id, date.today())
            ef = earliest_finish.get(wp.id, date.today())
            ls = latest_start.get(wp.id, date.today())
            lf = latest_finish.get(wp.id, date.today())
            slack = (ls - es).days

            analysis = {
                "id": wp.id,
                "name": wp.name,
                "earliest_start": es.isoformat(),
                "earliest_finish": ef.isoformat(),
                "latest_start": ls.isoformat(),
                "latest_finish": lf.isoformat(),
                "slack_days": slack,
                "is_critical": slack == 0,
                "duration_days": wp.duration_days or 30,
            }
            slack_analysis.append(analysis)

            if slack == 0:
                critical_path.append(analysis)

        # Sort critical path by earliest start
        critical_path.sort(key=lambda x: x["earliest_start"])

        total_duration = (project_end - min(earliest_start.values())).days if earliest_start else 0

        return {
            "success": True,
            "critical_path": critical_path,
            "slack_analysis": slack_analysis,
            "total_duration_days": total_duration,
            "project_end_date": project_end.isoformat(),
            "critical_path_count": len(critical_path),
            "total_work_packages": len(work_packages),
        }

    # =========================================================================
    # Roadmap Timeline Generation
    # =========================================================================

    def generate_roadmap_timeline(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        group_by: str = "status",
    ) -> Dict[str, Any]:
        """
        Generate roadmap timeline data for Gantt-style visualization.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter
            group_by: Grouping key (status, priority, assigned_to)

        Returns:
            Dict with timeline data
        """
        query = ImplementationWorkPackage.query

        if start_date:
            query = query.filter(
                or_(
                    ImplementationWorkPackage.start_date >= start_date,
                    ImplementationWorkPackage.target_date >= start_date,
                )
            )
        if end_date:
            query = query.filter(
                or_(
                    ImplementationWorkPackage.start_date <= end_date,
                    ImplementationWorkPackage.target_date <= end_date,
                )
            )

        work_packages = query.order_by(ImplementationWorkPackage.start_date.asc()).all()

        # A fixed set rather than getattr on a query-string attribute name:
        # an unrecognised value (or a bound method like "to_dict") falls back
        # to "status" instead of grouping by it or failing in jsonify.
        allowed_group_by = {"status", "priority", "assigned_to"}
        effective_group_by = group_by if group_by in allowed_group_by else "status"

        # Group work packages
        groups = defaultdict(list)
        for wp in work_packages:
            # The work package's own roadmap serialiser is the one mapping of
            # its fields (percent_complete, target_date, owner, dependencies)
            # to roadmap names; reuse it here instead of reading attribute
            # names the model does not have.
            row = wp.to_roadmap_dict()
            if effective_group_by == "assigned_to":
                group_key = row["owner_name"] or "unassigned"
            elif effective_group_by == "priority":
                group_key = wp.priority or "ungrouped"
            else:
                group_key = wp.status or "ungrouped"
            groups[group_key].append(
                {
                    "id": wp.id,
                    "name": wp.name,
                    "start": row["start_date"],
                    "end": row["end_date"],
                    "progress": row["percent_complete"],
                    "status": wp.status,
                    "priority": wp.priority,
                    "dependencies": row["dependencies"],
                    "isOverdue": row["is_overdue"],
                }
            )

        # Get plateaus for timeline overlay
        plateaus = ImplementationPlateau.query.order_by(
            ImplementationPlateau.start_date.asc()
        ).all()

        plateau_markers = [
            {
                "id": p.id,
                "name": p.name,
                "type": p.plateau_type,
                "start": p.start_date.isoformat() if p.start_date else None,
                "end": p.end_date.isoformat() if p.end_date else None,
            }
            for p in plateaus
        ]

        # Convert groups dict to array format expected by frontend JS
        groups_array = [{"name": key, "items": items} for key, items in groups.items()]

        return {
            "success": True,
            "groups": groups_array,
            "group_by": effective_group_by,
            "total_work_packages": len(work_packages),
            "plateau_markers": plateau_markers,
            "date_range": {
                "start": min(
                    (wp.start_date for wp in work_packages if wp.start_date), default=None
                ),
                "end": max(
                    (wp.target_date for wp in work_packages if wp.target_date), default=None
                ),
            },
        }

    # =========================================================================
    # Summary and Statistics
    # =========================================================================

    def get_roadmap_summary(self) -> Dict[str, Any]:
        """
        Get overall roadmap summary statistics.

        Returns:
            Dict with summary statistics
        """
        work_packages = ImplementationWorkPackage.query.all()

        status_counts = defaultdict(int)
        priority_counts = defaultdict(int)
        total_cost = 0
        actual_cost = 0
        overdue_count = 0

        for wp in work_packages:
            status_counts[wp.status] += 1
            priority_counts[wp.priority] += 1
            total_cost += float(wp.estimated_cost or 0)
            actual_cost += float(wp.actual_cost or 0)
            if wp.is_overdue():
                overdue_count += 1

        plateaus = ImplementationPlateau.query.all()
        current_plateau = next((p for p in plateaus if p.is_current()), None)

        gaps = ImplementationGap.query.filter(
            ImplementationGap.resolution_status.notin_(["resolved", "closed"])
        ).count()

        # Calculate critical path
        critical_path_result = self.calculate_critical_path()

        return {
            "success": True,
            "work_packages": {
                "total": len(work_packages),
                "by_status": dict(status_counts),
                "by_priority": dict(priority_counts),
                "overdue": overdue_count,
            },
            "cost": {
                "estimated_total": total_cost,
                "actual_total": actual_cost,
                "variance": total_cost - actual_cost,
            },
            "plateaus": {
                "total": len(plateaus),
                "current": current_plateau.name if current_plateau else None,
            },
            "gaps": {"open": gaps},
            "critical_path": {
                "length": critical_path_result.get("critical_path_count", 0),
                "total_duration_days": critical_path_result.get("total_duration_days", 0),
            },
        }
