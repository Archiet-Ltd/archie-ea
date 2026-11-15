"""
Strategic Service

Provides business logic for strategic planning and roadmap management.
Implements CRUD operations and analytics for:
- Strategic Initiatives
- StrategicMilestones
- Roadmap Items

All methods return dictionaries for easy JSON serialization.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from .. import db
from ..models.strategic import RoadmapItem, StrategicInitiative, StrategicMilestone

logger = logging.getLogger(__name__)


class StrategicService:
    """
    Service class for strategic planning operations.

    Provides comprehensive CRUD operations and analytics for:
    - Strategic Initiatives
    - StrategicMilestones
    - Roadmap Items
    """

    # =========================================================================
    # Initiative CRUD Operations
    # =========================================================================

    @staticmethod
    def get_all_initiatives(status_filter: Optional[str] = None) -> Dict[str, Any]:
        """
        Get all strategic initiatives with optional status filter.

        Args:
            status_filter: Optional status to filter by (draft, planning, in_progress, completed, cancelled)

        Returns:
            Dictionary with success status and list of initiatives
        """
        try:
            query = StrategicInitiative.query

            if status_filter:
                query = query.filter(StrategicInitiative.status == status_filter)

            initiatives = query.order_by(StrategicInitiative.created_at.desc()).all()

            return {
                "success": True,
                "initiatives": [i.to_dict() for i in initiatives],
                "total_count": len(initiatives),
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting initiatives: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting initiatives: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_initiative(initiative_id: int) -> Dict[str, Any]:
        """
        Get a single strategic initiative with its milestones.

        Args:
            initiative_id: ID of the initiative

        Returns:
            Dictionary with success status and initiative data
        """
        try:
            initiative = db.session.get(StrategicInitiative, initiative_id)

            if not initiative:
                return {"success": False, "error": f"Initiative {initiative_id} not found"}

            return {"success": True, "initiative": initiative.to_dict(include_milestones=True)}

        except SQLAlchemyError as e:
            logger.error(f"Database error getting initiative {initiative_id}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting initiative {initiative_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def create_initiative(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new strategic initiative.

        Args:
            data: Dictionary containing initiative fields:
                - name (required): Initiative name
                - description: Initiative description
                - status: Current status
                - priority: Priority level
                - start_date: Start date (ISO format or date object)
                - target_completion_date: Target completion date
                - budget_allocated: Allocated budget
                - owner_id: Owner user ID
                - business_value_score: Business value (1 - 10)
                - risk_level: Risk level
                - strategic_alignment: List of strategic goals

        Returns:
            Dictionary with success status and created initiative
        """
        try:
            # Validate required fields
            if not data.get("name"):
                return {"success": False, "error": "Initiative name is required"}

            initiative = StrategicInitiative(
                name=data["name"],
                description=data.get("description"),
                record_kind=data.get("record_kind"),
                status=data.get("status", "draft"),
                priority=data.get("priority", "medium"),
                budget_allocated=data.get("budget_allocated", 0.0),
                budget_spent=data.get("budget_spent", 0.0),
                owner_id=data.get("owner_id"),
                business_value_score=data.get("business_value_score", 5),
                risk_level=data.get("risk_level", "medium"),
            )

            # Handle date fields
            if data.get("start_date"):
                initiative.start_date = StrategicService._parse_date(data["start_date"])
            if data.get("target_completion_date"):
                initiative.target_completion_date = StrategicService._parse_date(
                    data["target_completion_date"]
                )

            # Handle strategic alignment
            if data.get("strategic_alignment"):
                if isinstance(data["strategic_alignment"], list):
                    initiative.set_strategic_alignment_list(data["strategic_alignment"])
                else:
                    initiative.strategic_alignment = data["strategic_alignment"]

            db.session.add(initiative)
            db.session.commit()

            logger.info(f"Created strategic initiative: {initiative.name} (ID: {initiative.id})")

            return {
                "success": True,
                "initiative": initiative.to_dict(),
                "message": f'Initiative "{initiative.name}" created successfully',
            }

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error creating initiative: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating initiative: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_initiative(initiative_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing strategic initiative.

        Args:
            initiative_id: ID of the initiative to update
            data: Dictionary containing fields to update

        Returns:
            Dictionary with success status and updated initiative
        """
        try:
            initiative = db.session.get(StrategicInitiative, initiative_id)

            if not initiative:
                return {"success": False, "error": f"Initiative {initiative_id} not found"}

            # Update simple fields
            updatable_fields = [
                "name",
                "description",
                "status",
                "priority",
                "budget_allocated",
                "budget_spent",
                "owner_id",
                "business_value_score",
                "risk_level",
            ]

            for field in updatable_fields:
                if field in data:
                    setattr(initiative, field, data[field])

            # Handle date fields
            if "start_date" in data:
                initiative.start_date = StrategicService._parse_date(data["start_date"])
            if "target_completion_date" in data:
                initiative.target_completion_date = StrategicService._parse_date(
                    data["target_completion_date"]
                )
            if "actual_completion_date" in data:
                initiative.actual_completion_date = StrategicService._parse_date(
                    data["actual_completion_date"]
                )

            # Handle strategic alignment
            if "strategic_alignment" in data:
                if isinstance(data["strategic_alignment"], list):
                    initiative.set_strategic_alignment_list(data["strategic_alignment"])
                else:
                    initiative.strategic_alignment = data["strategic_alignment"]

            initiative.updated_at = datetime.utcnow()
            db.session.commit()

            logger.info(f"Updated strategic initiative: {initiative.name} (ID: {initiative.id})")

            return {
                "success": True,
                "initiative": initiative.to_dict(),
                "message": f'Initiative "{initiative.name}" updated successfully',
            }

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error updating initiative {initiative_id}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating initiative {initiative_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_initiative(initiative_id: int) -> Dict[str, Any]:
        """
        Delete a strategic initiative and its milestones.

        Args:
            initiative_id: ID of the initiative to delete

        Returns:
            Dictionary with success status
        """
        try:
            initiative = db.session.get(StrategicInitiative, initiative_id)

            if not initiative:
                return {"success": False, "error": f"Initiative {initiative_id} not found"}

            name = initiative.name
            db.session.delete(initiative)
            db.session.commit()

            logger.info(f"Deleted strategic initiative: {name} (ID: {initiative_id})")

            return {"success": True, "message": f'Initiative "{name}" deleted successfully'}

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error deleting initiative {initiative_id}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting initiative {initiative_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_initiative_statistics() -> Dict[str, Any]:
        """
        Get summary statistics for all initiatives.

        Returns:
            Dictionary with statistics by status, budget totals, and completion rates
        """
        try:
            # Count by status
            status_counts = (
                db.session.query(StrategicInitiative.status, func.count(StrategicInitiative.id))
                .group_by(StrategicInitiative.status)
                .all()
            )

            status_distribution = {status: count for status, count in status_counts}

            # Budget totals
            budget_stats = db.session.query(
                func.sum(StrategicInitiative.budget_allocated),
                func.sum(StrategicInitiative.budget_spent),
            ).first()

            total_allocated = budget_stats[0] or 0.0
            total_spent = budget_stats[1] or 0.0

            # Count by priority
            priority_counts = (
                db.session.query(StrategicInitiative.priority, func.count(StrategicInitiative.id))
                .group_by(StrategicInitiative.priority)
                .all()
            )

            priority_distribution = {priority: count for priority, count in priority_counts}

            # Count by risk level
            risk_counts = (
                db.session.query(StrategicInitiative.risk_level, func.count(StrategicInitiative.id))
                .group_by(StrategicInitiative.risk_level)
                .all()
            )

            risk_distribution = {risk: count for risk, count in risk_counts}

            # Overdue initiatives
            today = datetime.now().date()
            overdue_count = StrategicInitiative.query.filter(
                StrategicInitiative.target_completion_date < today,
                StrategicInitiative.status.notin_(["completed", "cancelled"]),
            ).count()

            # Total count
            total_count = StrategicInitiative.query.count()

            return {
                "success": True,
                "statistics": {
                    "total_initiatives": total_count,
                    "status_distribution": status_distribution,
                    "priority_distribution": priority_distribution,
                    "risk_distribution": risk_distribution,
                    "budget_allocated_total": total_allocated,
                    "budget_spent_total": total_spent,
                    "budget_remaining_total": total_allocated - total_spent,
                    "budget_utilization_percentage": round(total_spent / total_allocated * 100, 2)
                    if total_allocated > 0
                    else 0,
                    "overdue_count": overdue_count,
                    "completion_rate": round(
                        status_distribution.get("completed", 0) / total_count * 100, 2
                    )
                    if total_count > 0
                    else 0,
                },
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting initiative statistics: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting initiative statistics: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # StrategicMilestone CRUD Operations
    # =========================================================================

    @staticmethod
    def get_milestones_for_initiative(initiative_id: int) -> Dict[str, Any]:
        """
        Get all milestones for a specific initiative.

        Args:
            initiative_id: ID of the initiative

        Returns:
            Dictionary with success status and list of milestones
        """
        try:
            initiative = db.session.get(StrategicInitiative, initiative_id)

            if not initiative:
                return {"success": False, "error": f"Initiative {initiative_id} not found"}

            milestones = (
                StrategicMilestone.query.filter_by(initiative_id=initiative_id)
                .order_by(StrategicMilestone.due_date.asc())
                .all()
            )

            return {
                "success": True,
                "initiative_id": initiative_id,
                "initiative_name": initiative.name,
                "milestones": [m.to_dict() for m in milestones],
                "total_count": len(milestones),
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting milestones for initiative {initiative_id}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting milestones for initiative {initiative_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def create_milestone(initiative_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new milestone for an initiative.

        Args:
            initiative_id: ID of the parent initiative
            data: Dictionary containing milestone fields:
                - name (required): StrategicMilestone name
                - description: StrategicMilestone description
                - due_date: Due date
                - status: Current status
                - deliverables: List of deliverables
                - dependencies: List of milestone IDs

        Returns:
            Dictionary with success status and created milestone
        """
        try:
            initiative = db.session.get(StrategicInitiative, initiative_id)

            if not initiative:
                return {"success": False, "error": f"Initiative {initiative_id} not found"}

            if not data.get("name"):
                return {"success": False, "error": "StrategicMilestone name is required"}

            milestone = StrategicMilestone(
                initiative_id=initiative_id,
                name=data["name"],
                description=data.get("description"),
                status=data.get("status", "pending"),
            )

            # Handle due date
            if data.get("due_date"):
                milestone.due_date = StrategicService._parse_date(data["due_date"])

            # Handle deliverables
            if data.get("deliverables"):
                if isinstance(data["deliverables"], list):
                    milestone.set_deliverables_list(data["deliverables"])
                else:
                    milestone.deliverables = data["deliverables"]

            # Handle dependencies
            if data.get("dependencies"):
                if isinstance(data["dependencies"], list):
                    milestone.set_dependencies_list(data["dependencies"])
                else:
                    milestone.dependencies = data["dependencies"]

            db.session.add(milestone)
            db.session.commit()

            logger.info(f"Created milestone: {milestone.name} for initiative {initiative_id}")

            return {
                "success": True,
                "milestone": milestone.to_dict(),
                "message": f'StrategicMilestone "{milestone.name}" created successfully',
            }

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error creating milestone: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating milestone: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_milestone(milestone_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing milestone.

        Args:
            milestone_id: ID of the milestone to update
            data: Dictionary containing fields to update

        Returns:
            Dictionary with success status and updated milestone
        """
        try:
            milestone = db.session.get(StrategicMilestone, milestone_id)

            if not milestone:
                return {"success": False, "error": f"StrategicMilestone {milestone_id} not found"}

            # Update simple fields
            updatable_fields = ["name", "description", "status"]

            for field in updatable_fields:
                if field in data:
                    setattr(milestone, field, data[field])

            # Handle date fields
            if "due_date" in data:
                milestone.due_date = StrategicService._parse_date(data["due_date"])
            if "completed_date" in data:
                milestone.completed_date = StrategicService._parse_date(data["completed_date"])

            # Handle deliverables
            if "deliverables" in data:
                if isinstance(data["deliverables"], list):
                    milestone.set_deliverables_list(data["deliverables"])
                else:
                    milestone.deliverables = data["deliverables"]

            # Handle dependencies
            if "dependencies" in data:
                if isinstance(data["dependencies"], list):
                    milestone.set_dependencies_list(data["dependencies"])
                else:
                    milestone.dependencies = data["dependencies"]

            milestone.updated_at = datetime.utcnow()
            db.session.commit()

            logger.info(f"Updated milestone: {milestone.name} (ID: {milestone.id})")

            return {
                "success": True,
                "milestone": milestone.to_dict(),
                "message": f'StrategicMilestone "{milestone.name}" updated successfully',
            }

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error updating milestone {milestone_id}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating milestone {milestone_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_milestone(milestone_id: int) -> Dict[str, Any]:
        """
        Delete a milestone.

        Args:
            milestone_id: ID of the milestone to delete

        Returns:
            Dictionary with success status
        """
        try:
            milestone = db.session.get(StrategicMilestone, milestone_id)

            if not milestone:
                return {"success": False, "error": f"StrategicMilestone {milestone_id} not found"}

            name = milestone.name
            db.session.delete(milestone)
            db.session.commit()

            logger.info(f"Deleted milestone: {name} (ID: {milestone_id})")

            return {"success": True, "message": f'StrategicMilestone "{name}" deleted successfully'}

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error deleting milestone {milestone_id}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting milestone {milestone_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_upcoming_milestones(days: int = 30) -> Dict[str, Any]:
        """
        Get milestones due within the next N days.

        Args:
            days: Number of days to look ahead (default 30)

        Returns:
            Dictionary with success status and list of upcoming milestones
        """
        try:
            today = datetime.now().date()
            cutoff_date = today + timedelta(days=days)

            milestones = (
                StrategicMilestone.query.filter(
                    StrategicMilestone.due_date >= today,
                    StrategicMilestone.due_date <= cutoff_date,
                    StrategicMilestone.status.notin_(["completed"]),
                )
                .order_by(StrategicMilestone.due_date.asc())
                .all()
            )

            return {
                "success": True,
                "milestones": [m.to_dict() for m in milestones],
                "total_count": len(milestones),
                "date_range": {"start": today.isoformat(), "end": cutoff_date.isoformat()},
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting upcoming milestones: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting upcoming milestones: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_overdue_milestones() -> Dict[str, Any]:
        """
        Get all milestones that are past their due date.

        Returns:
            Dictionary with success status and list of overdue milestones
        """
        try:
            today = datetime.now().date()

            milestones = (
                StrategicMilestone.query.filter(
                    StrategicMilestone.due_date < today,
                    StrategicMilestone.status.notin_(["completed"]),
                )
                .order_by(StrategicMilestone.due_date.asc())
                .all()
            )

            return {
                "success": True,
                "milestones": [m.to_dict() for m in milestones],
                "total_count": len(milestones),
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting overdue milestones: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting overdue milestones: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Roadmap Operations
    # =========================================================================

    @staticmethod
    def get_roadmap_items(
        year: Optional[int] = None,
        quarter: Optional[str] = None,
        lane: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get roadmap items with optional filters.

        Args:
            year: Filter by year
            quarter: Filter by quarter (Q1, Q2, Q3, Q4)
            lane: Filter by lane (business, application, technology, infrastructure)
            status: Filter by status

        Returns:
            Dictionary with success status and list of roadmap items
        """
        try:
            query = RoadmapItem.query

            if year:
                query = query.filter(RoadmapItem.year == year)
            if quarter:
                query = query.filter(RoadmapItem.quarter == quarter)
            if lane:
                query = query.filter(RoadmapItem.lane == lane)
            if status:
                query = query.filter(RoadmapItem.status == status)

            items = query.order_by(
                RoadmapItem.year.asc(), RoadmapItem.quarter.asc(), RoadmapItem.lane.asc()
            ).all()

            return {
                "success": True,
                "roadmap_items": [item.to_dict(include_initiative=True) for item in items],
                "total_count": len(items),
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting roadmap items: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting roadmap items: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def create_roadmap_item(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new roadmap item.

        Args:
            data: Dictionary containing roadmap item fields:
                - title (required): Item title
                - description: Item description
                - initiative_id: Parent initiative ID (optional)
                - category: Item category
                - lane: Roadmap lane
                - quarter: Target quarter
                - year: Target year
                - status: Current status
                - effort_estimate: Effort estimate
                - dependencies: List of roadmap item IDs
                - linked_capabilities: List of capability IDs
                - linked_applications: List of application IDs

        Returns:
            Dictionary with success status and created roadmap item
        """
        try:
            if not data.get("title"):
                return {"success": False, "error": "Roadmap item title is required"}

            item = RoadmapItem(
                title=data["title"],
                description=data.get("description"),
                initiative_id=data.get("initiative_id"),
                category=data.get("category", "technology"),
                lane=data.get("lane", "application"),
                quarter=data.get("quarter"),
                year=data.get("year"),
                status=data.get("status", "planned"),
                effort_estimate=data.get("effort_estimate", "medium"),
            )

            # Handle dependencies
            if data.get("dependencies"):
                if isinstance(data["dependencies"], list):
                    item.set_dependencies_list(data["dependencies"])
                else:
                    item.dependencies = data["dependencies"]

            # Handle linked capabilities
            if data.get("linked_capabilities"):
                if isinstance(data["linked_capabilities"], list):
                    item.set_linked_capabilities(data["linked_capabilities"])
                else:
                    item.linked_capabilities = data["linked_capabilities"]

            # Handle linked applications
            if data.get("linked_applications"):
                if isinstance(data["linked_applications"], list):
                    item.set_linked_applications(data["linked_applications"])
                else:
                    item.linked_applications = data["linked_applications"]

            db.session.add(item)
            db.session.commit()

            logger.info(f"Created roadmap item: {item.title} (ID: {item.id})")

            return {
                "success": True,
                "roadmap_item": item.to_dict(include_initiative=True),
                "message": f'Roadmap item "{item.title}" created successfully',
            }

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error creating roadmap item: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating roadmap item: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_roadmap_item(item_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing roadmap item.

        Args:
            item_id: ID of the roadmap item to update
            data: Dictionary containing fields to update

        Returns:
            Dictionary with success status and updated roadmap item
        """
        try:
            item = db.session.get(RoadmapItem, item_id)

            if not item:
                return {"success": False, "error": f"Roadmap item {item_id} not found"}

            # Update simple fields
            updatable_fields = [
                "title",
                "description",
                "initiative_id",
                "category",
                "lane",
                "quarter",
                "year",
                "status",
                "effort_estimate",
            ]

            for field in updatable_fields:
                if field in data:
                    setattr(item, field, data[field])

            # Handle dependencies
            if "dependencies" in data:
                if isinstance(data["dependencies"], list):
                    item.set_dependencies_list(data["dependencies"])
                else:
                    item.dependencies = data["dependencies"]

            # Handle linked capabilities
            if "linked_capabilities" in data:
                if isinstance(data["linked_capabilities"], list):
                    item.set_linked_capabilities(data["linked_capabilities"])
                else:
                    item.linked_capabilities = data["linked_capabilities"]

            # Handle linked applications
            if "linked_applications" in data:
                if isinstance(data["linked_applications"], list):
                    item.set_linked_applications(data["linked_applications"])
                else:
                    item.linked_applications = data["linked_applications"]

            item.updated_at = datetime.utcnow()
            db.session.commit()

            logger.info(f"Updated roadmap item: {item.title} (ID: {item.id})")

            return {
                "success": True,
                "roadmap_item": item.to_dict(include_initiative=True),
                "message": f'Roadmap item "{item.title}" updated successfully',
            }

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error updating roadmap item {item_id}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating roadmap item {item_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_roadmap_item(item_id: int) -> Dict[str, Any]:
        """
        Delete a roadmap item.

        Args:
            item_id: ID of the roadmap item to delete

        Returns:
            Dictionary with success status
        """
        try:
            item = db.session.get(RoadmapItem, item_id)

            if not item:
                return {"success": False, "error": f"Roadmap item {item_id} not found"}

            title = item.title
            db.session.delete(item)
            db.session.commit()

            logger.info(f"Deleted roadmap item: {title} (ID: {item_id})")

            return {"success": True, "message": f'Roadmap item "{title}" deleted successfully'}

        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Database error deleting roadmap item {item_id}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error deleting roadmap item {item_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_roadmap_by_lane() -> Dict[str, Any]:
        """
        Get roadmap items grouped by lane for swimlane visualization.

        Returns:
            Dictionary with success status and items grouped by lane
        """
        try:
            lanes = ["business", "application", "technology", "infrastructure"]
            grouped_items = {}

            for lane in lanes:
                items = (
                    RoadmapItem.query.filter_by(lane=lane)
                    .order_by(RoadmapItem.year.asc(), RoadmapItem.quarter.asc())
                    .all()
                )

                grouped_items[lane] = [item.to_dict(include_initiative=True) for item in items]

            return {
                "success": True,
                "lanes": lanes,
                "roadmap_by_lane": grouped_items,
                "total_count": sum(len(items) for items in grouped_items.values()),
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting roadmap by lane: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting roadmap by lane: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_roadmap_timeline(start_year: int, end_year: int) -> Dict[str, Any]:
        """
        Get roadmap items for timeline view spanning multiple years.

        Args:
            start_year: Starting year for the timeline
            end_year: Ending year for the timeline

        Returns:
            Dictionary with success status and items organized by year/quarter
        """
        try:
            items = (
                RoadmapItem.query.filter(
                    RoadmapItem.year >= start_year, RoadmapItem.year <= end_year
                )
                .order_by(RoadmapItem.year.asc(), RoadmapItem.quarter.asc())
                .all()
            )

            # Organize by year and quarter
            timeline = {}
            for year in range(start_year, end_year + 1):
                timeline[year] = {
                    "Q1": [],
                    "Q2": [],
                    "Q3": [],
                    "Q4": [],
                }

            for item in items:
                if item.year in timeline and item.quarter in timeline[item.year]:
                    timeline[item.year][item.quarter].append(item.to_dict(include_initiative=True))

            return {
                "success": True,
                "timeline": timeline,
                "year_range": {"start": start_year, "end": end_year},
                "total_count": len(items),
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting roadmap timeline: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting roadmap timeline: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Analytics Operations
    # =========================================================================

    @staticmethod
    def get_strategic_metrics() -> Dict[str, Any]:
        """
        Get overall strategic metrics including initiative counts, budget utilization, and completion rates.

        Returns:
            Dictionary with comprehensive strategic metrics
        """
        try:
            # Initiative counts
            total_initiatives = StrategicInitiative.query.count()
            active_initiatives = StrategicInitiative.query.filter(
                StrategicInitiative.status.in_(["planning", "in_progress"])
            ).count()
            completed_initiatives = StrategicInitiative.query.filter_by(status="completed").count()

            # Budget metrics
            budget_stats = db.session.query(
                func.sum(StrategicInitiative.budget_allocated),
                func.sum(StrategicInitiative.budget_spent),
            ).first()

            total_budget_allocated = budget_stats[0] or 0.0
            total_budget_spent = budget_stats[1] or 0.0

            # StrategicMilestone metrics
            total_milestones = StrategicMilestone.query.count()
            completed_milestones = StrategicMilestone.query.filter_by(status="completed").count()
            overdue_milestones = StrategicService.get_overdue_milestones()["total_count"]

            # Roadmap metrics
            total_roadmap_items = RoadmapItem.query.count()
            planned_items = RoadmapItem.query.filter_by(status="planned").count()
            in_progress_items = RoadmapItem.query.filter_by(status="in_progress").count()
            completed_items = RoadmapItem.query.filter_by(status="completed").count()

            # Average business value score
            avg_value_score = (
                db.session.query(func.avg(StrategicInitiative.business_value_score)).scalar() or 0
            )

            return {
                "success": True,
                "metrics": {
                    "initiatives": {
                        "total": total_initiatives,
                        "active": active_initiatives,
                        "completed": completed_initiatives,
                        "completion_rate": round(completed_initiatives / total_initiatives * 100, 2)
                        if total_initiatives > 0
                        else 0,
                    },
                    "budget": {
                        "total_allocated": total_budget_allocated,
                        "total_spent": total_budget_spent,
                        "total_remaining": total_budget_allocated - total_budget_spent,
                        "utilization_rate": round(
                            total_budget_spent / total_budget_allocated * 100, 2
                        )
                        if total_budget_allocated > 0
                        else 0,
                    },
                    "milestones": {
                        "total": total_milestones,
                        "completed": completed_milestones,
                        "overdue": overdue_milestones,
                        "completion_rate": round(completed_milestones / total_milestones * 100, 2)
                        if total_milestones > 0
                        else 0,
                    },
                    "roadmap": {
                        "total_items": total_roadmap_items,
                        "planned": planned_items,
                        "in_progress": in_progress_items,
                        "completed": completed_items,
                    },
                    "average_business_value_score": round(avg_value_score, 2),
                },
                "generated_at": datetime.utcnow().isoformat(),
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting strategic metrics: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting strategic metrics: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_initiative_health_scores() -> Dict[str, Any]:
        """
        Calculate health scores for all initiatives based on milestones, budget, and timeline.

        Health score factors:
        - StrategicMilestone completion rate (40%)
        - Budget utilization (30%)
        - Timeline adherence (30%)

        Returns:
            Dictionary with health scores per initiative
        """
        try:
            initiatives = StrategicInitiative.query.filter(
                StrategicInitiative.status.in_(["planning", "in_progress"])
            ).all()

            health_scores = []
            today = datetime.now().date()

            for initiative in initiatives:
                # Calculate milestone score (40%)
                total_milestones = initiative.milestones.count()
                completed_milestones = initiative.milestones.filter_by(status="completed").count()
                milestone_score = (
                    (completed_milestones / total_milestones * 100) if total_milestones > 0 else 100
                )

                # Calculate budget score (30%)
                # Perfect score if under budget, decreasing as over budget
                budget_utilization = initiative.budget_utilization_percentage
                if budget_utilization <= 100:
                    budget_score = 100
                else:
                    # Decrease by 5 points for each 10% over budget
                    over_budget_pct = budget_utilization - 100
                    budget_score = max(0, 100 - (over_budget_pct / 10 * 5))

                # Calculate timeline score (30%)
                timeline_score = 100
                if initiative.target_completion_date and initiative.start_date:
                    total_duration = (
                        initiative.target_completion_date - initiative.start_date
                    ).days
                    elapsed = (
                        (today - initiative.start_date).days if today > initiative.start_date else 0
                    )

                    if total_duration > 0:
                        expected_progress = min(100, elapsed / total_duration * 100)
                        actual_progress = initiative.completion_percentage

                        # If actual progress is behind expected, reduce score
                        if actual_progress < expected_progress:
                            behind_pct = expected_progress - actual_progress
                            timeline_score = max(0, 100 - behind_pct)

                # Calculate weighted overall score
                overall_score = milestone_score * 0.4 + budget_score * 0.3 + timeline_score * 0.3

                # Determine health status
                if overall_score >= 80:
                    health_status = "healthy"
                elif overall_score >= 60:
                    health_status = "at_risk"
                elif overall_score >= 40:
                    health_status = "needs_attention"
                else:
                    health_status = "critical"

                health_scores.append(
                    {
                        "initiative_id": initiative.id,
                        "initiative_name": initiative.name,
                        "overall_score": round(overall_score, 2),
                        "health_status": health_status,
                        "breakdown": {
                            "milestone_score": round(milestone_score, 2),
                            "budget_score": round(budget_score, 2),
                            "timeline_score": round(timeline_score, 2),
                        },
                        "details": {
                            "total_milestones": total_milestones,
                            "completed_milestones": completed_milestones,
                            "budget_utilization": budget_utilization,
                            "completion_percentage": initiative.completion_percentage,
                            "is_overdue": initiative.is_overdue,
                        },
                    }
                )

            # Sort by overall score ascending (worst first)
            health_scores.sort(key=lambda x: x["overall_score"])

            return {
                "success": True,
                "health_scores": health_scores,
                "summary": {
                    "healthy": sum(1 for h in health_scores if h["health_status"] == "healthy"),
                    "at_risk": sum(1 for h in health_scores if h["health_status"] == "at_risk"),
                    "needs_attention": sum(
                        1 for h in health_scores if h["health_status"] == "needs_attention"
                    ),
                    "critical": sum(1 for h in health_scores if h["health_status"] == "critical"),
                    "average_score": round(
                        sum(h["overall_score"] for h in health_scores) / len(health_scores), 2
                    )
                    if health_scores
                    else 0,
                },
                "generated_at": datetime.utcnow().isoformat(),
            }

        except SQLAlchemyError as e:
            logger.error(f"Database error getting initiative health scores: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting initiative health scores: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Helper Methods
    # =========================================================================

    @staticmethod
    def _parse_date(date_value) -> Optional[datetime.date]:
        """
        Parse a date value that could be a string, date, or datetime.

        Args:
            date_value: Date value to parse

        Returns:
            date object or None
        """
        if date_value is None:
            return None

        if isinstance(date_value, datetime):
            return date_value.date()

        if hasattr(date_value, "date"):
            return date_value

        if isinstance(date_value, str):
            try:
                # Try ISO format first
                return datetime.fromisoformat(date_value.replace("Z", "+00:00")).date()
            except ValueError:
                try:
                    # Try common formats
                    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]:
                        try:
                            return datetime.strptime(date_value, fmt).date()
                        except ValueError:
                            continue
                except Exception as e:
                    logger.debug("Failed to parse date value: %s", e)

        return None


# Convenience exports
__all__ = ["StrategicService"]
