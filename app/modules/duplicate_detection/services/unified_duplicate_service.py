"""

Unified Duplicate Detection Service

Single entry point for all duplicate detection operations.
Uses Strategy Pattern for pluggable detection algorithms.
"""

import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from flask import g

from psycopg2.errors import SerializationFailure
from sqlalchemy.exc import OperationalError

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.unified_duplicate_detection import (
    GroupStatus,
    UnifiedDetectionRun,
    UnifiedDuplicateGroup,
    unified_group_members,
)
from app.services.detection_strategies import (
    DetectionStrategy,
    EnhancedDetectionStrategy,
    FastDetectionStrategy,
    HybridDetectionStrategy,
)

logger = logging.getLogger(__name__)


def with_retry(max_retries: int = 3, base_delay: float = 0.1):
    """
    Decorator for retrying database transactions on serialization failures.
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, SerializationFailure) as e:
                    if attempt == max_retries:
                        raise e

                    # fabricated-ok: retry backoff jitter, not a displayed value
                    delay = base_delay * (2**attempt) + random.uniform(0, 0.1)
                    logger.warning(
                        f"Database error, retrying in {delay:.2f}s (attempt {attempt + 1}/{max_retries + 1})"
                    )
                    time.sleep(delay)
                    db.session.rollback()

                except Exception as e:
                    db.session.rollback()
                    raise e

            return None

        return wrapper

    return decorator


class UnifiedDuplicateService:
    """
    Unified service for duplicate detection operations.

    Provides a single API for:
    - Running detection with any strategy
    - Managing detection runs
    - Managing duplicate groups
    - Resolving duplicates
    - Generating reports
    """

    # Strategy registry
    STRATEGIES: Dict[str, Type[DetectionStrategy]] = {
        "fast": FastDetectionStrategy,
        "hybrid": HybridDetectionStrategy,
        "enhanced": EnhancedDetectionStrategy,
    }

    @classmethod
    def get_strategy(cls, strategy: str, threshold: float = 0.55, config: Optional[Dict] = None) -> DetectionStrategy:
        """Get a strategy instance by name."""
        strategy_class = cls.STRATEGIES.get(strategy)
        if not strategy_class:
            raise ValueError(f"Unknown strategy: {strategy}. Available: {list(cls.STRATEGIES.keys())}")
        return strategy_class(threshold=threshold, config=config)

    @classmethod
    @with_retry(max_retries=3)
    def run_detection(
        cls,
        strategy: str = "fast",
        threshold: float = 0.55,
        config: Optional[Dict] = None,
        run_name: Optional[str] = None,
        user_id: Optional[int] = None,
        cleanup_first: bool = True,
    ) -> Dict[str, Any]:
        """
        Run duplicate detection with the specified strategy.

        Args:
            strategy: Detection strategy to use
            threshold: Similarity threshold
            config: Strategy-specific configuration
            run_name: Name for this detection run
            user_id: ID of user triggering the detection
            cleanup_first: Whether to clean up previous results first

        Returns:
            Dict with run results
        """
        start_time = datetime.utcnow()

        try:
            # Optional cleanup
            if cleanup_first:
                cls._cleanup_stale_data()

            # Get applications first (read-only, no transaction conflict)
            applications = ApplicationComponent.query.all()
            app_count = len(applications)

            if not applications:
                # Create completed run with no results
                run = UnifiedDetectionRun(
                    run_name=run_name
                    or f"{strategy.title()} Detection - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    strategy=strategy,
                    similarity_threshold=threshold,
                    config=config or {},
                    status="completed",
                    started_at=start_time,
                    completed_at=datetime.utcnow(),
                    duration_seconds=0,
                    applications_analyzed=0,
                    groups_found=0,
                    triggered_by="user" if user_id else "api",
                    user_id=user_id,
                    organization_id=getattr(g, "current_org_id", None),
                )
                db.session.add(run)
                db.session.commit()

                return {
                    "success": True,
                    "run_id": run.id,
                    "message": "No applications to analyze",
                    "groups_found": 0,
                }

            # Run detection algorithm (pure computation, no DB)
            detector = cls.get_strategy(strategy, threshold, config)
            result = detector.detect(applications)

            # Calculate end time
            end_time = datetime.utcnow()
            duration = int((end_time - start_time).total_seconds())

            # Single transaction: create run + all groups + all members
            with db.session.no_autoflush:
                # Create detection run with final values
                run = UnifiedDetectionRun(
                    run_name=run_name
                    or f"{strategy.title()} Detection - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    strategy=strategy,
                    similarity_threshold=threshold,
                    config=config or {},
                    status="completed",
                    started_at=start_time,
                    completed_at=end_time,
                    duration_seconds=duration,
                    applications_analyzed=app_count,
                    groups_found=len(result.groups),
                    exact_matches=result.exact_matches,
                    fuzzy_matches=result.fuzzy_matches,
                    estimated_savings=result.estimated_savings,
                    triggered_by="user" if user_id else "api",
                    user_id=user_id,
                    organization_id=getattr(g, "current_org_id", None),
                )
                db.session.add(run)
                db.session.flush()  # Get run.id

                # Store all groups and members
                groups_created = 0
                for group_data in result.groups:
                    group = UnifiedDuplicateGroup(
                        name=f"Duplicate Group {groups_created + 1}",
                        description=f"Applications with {group_data['duplicate_type']} similarity",
                        detection_run_id=run.id,
                        duplicate_type=group_data["duplicate_type"],
                        similarity_score=group_data["similarity_score"],
                        match_details=group_data.get("match_details", {}),
                        status=GroupStatus.PENDING.value,
                        estimated_savings=cls._estimate_group_savings(
                            [
                                app
                                for app in applications
                                if app.id in [a["id"] for a in group_data["applications"]]
                            ]
                        ),
                    )
                    db.session.add(group)
                    db.session.flush()  # Get group.id

                    # Add applications to group
                    primary_id = group_data.get("primary_app_id")
                    for app_data in group_data["applications"]:
                        stmt = unified_group_members.insert().values(
                            group_id=group.id,
                            application_id=app_data["id"],
                            similarity_to_primary=app_data.get("similarity_to_primary", 1.0),
                            is_primary=(app_data["id"] == primary_id),
                            added_at=datetime.utcnow(),
                        )
                        db.session.execute(stmt)  # tenant-filtered: scoped via parent FK (group_id)

                    groups_created += 1

            # Commit entire transaction
            db.session.commit()

            logger.info(
                f"Detection completed: {groups_created} groups found using {strategy} strategy"
            )

            return {
                "success": True,
                "run_id": run.id,
                "strategy": strategy,
                "groups_found": groups_created,
                "exact_matches": result.exact_matches,
                "fuzzy_matches": result.fuzzy_matches,
                "applications_analyzed": result.applications_analyzed,
                "estimated_savings": result.estimated_savings,
                "duration_seconds": run.duration_seconds,
                "message": f"Detection completed. Found {groups_created} duplicate groups.",
            }

        except Exception as e:
            logger.error(f"Detection failed: {e}")

            # Update run status if it exists
            if "run" in locals():
                run.status = "failed"
                run.error_message = str(e)
                run.completed_at = datetime.utcnow()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            raise

    @classmethod
    def _cleanup_stale_data(cls, max_retries: int = 3) -> Dict[str, Any]:
        """
        Clean up old detection results before new run.
        """
        for attempt in range(max_retries):
            try:
                # Delete old groups (cascades to members)
                UnifiedDuplicateGroup.query.delete()
                db.session.commit()
                return {"success": True, "message": "Cleanup completed"}

            except Exception as e:
                db.session.rollback()
                if attempt == max_retries - 1:
                    logger.warning(f"Cleanup failed after {max_retries} attempts: {e}")
                    return {"success": False, "error": str(e)}
                time.sleep(0.1 * (2**attempt))

        return {"success": False, "error": "Max retries exceeded"}

    @classmethod
    def _estimate_group_savings(cls, applications: List[ApplicationComponent]) -> float:
        """
        Estimate potential savings from consolidating a group.

        Uses actual cost data from ApplicationComponent:
        - total_cost_of_ownership (preferred, if available)
        - Or sum of: license_cost + maintenance_cost + infrastructure_cost + support_cost

        Returns 0 if no cost data is available (no hardcoded fallbacks).
        """
        if len(applications) <= 1:
            return 0.0

        total_cost = 0.0
        apps_with_cost = 0

        for app in applications:
            cost = 0.0

            # Prefer total_cost_of_ownership if available
            if hasattr(app, "total_cost_of_ownership") and app.total_cost_of_ownership:
                cost = float(app.total_cost_of_ownership)
            else:
                # Calculate from individual cost components
                if hasattr(app, "license_cost") and app.license_cost:
                    cost += float(app.license_cost)
                if hasattr(app, "maintenance_cost") and app.maintenance_cost:
                    cost += float(app.maintenance_cost)
                if hasattr(app, "infrastructure_cost") and app.infrastructure_cost:
                    cost += float(app.infrastructure_cost)
                if hasattr(app, "support_cost") and app.support_cost:
                    cost += float(app.support_cost)

            if cost > 0:
                total_cost += cost
                apps_with_cost += 1

        # Calculate savings: average cost * (number of duplicates that would be removed)
        if apps_with_cost > 0:
            avg_cost = total_cost / apps_with_cost
            return avg_cost * (len(applications) - 1)

        # No cost data available - return 0 (no hardcoded fallbacks)
        return 0.0

    # ===== Group Management =====

    @classmethod
    def get_groups(
        cls,
        run_id: Optional[int] = None,
        status: Optional[str] = None,
        duplicate_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> Dict[str, Any]:
        """
        Get duplicate groups with filtering and pagination.
        """
        query = UnifiedDuplicateGroup.query

        if run_id:
            query = query.filter_by(detection_run_id=run_id)
        if status:
            query = query.filter_by(status=status)
        if duplicate_type:
            query = query.filter_by(duplicate_type=duplicate_type)

        query = query.order_by(UnifiedDuplicateGroup.similarity_score.desc())

        # Paginate
        total = query.count()
        groups = query.offset((page - 1) * per_page).limit(per_page).all()

        return {
            "groups": [g.to_dict(include_apps=True) for g in groups],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        }

    @classmethod
    def get_group(cls, group_id: int) -> Optional[Dict[str, Any]]:
        """Get a single duplicate group by ID"""
        group = UnifiedDuplicateGroup.query.get(group_id)
        if group:
            return group.to_dict(include_apps=True)
        return None

    # ===== Run Management =====

    @classmethod
    def get_runs(
        cls, status: Optional[str] = None, strategy: Optional[str] = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent detection runs"""
        query = UnifiedDetectionRun.query

        if status:
            query = query.filter_by(status=status)
        if strategy:
            query = query.filter_by(strategy=strategy)

        runs = query.order_by(UnifiedDetectionRun.created_at.desc()).limit(limit).all()
        return [r.to_dict() for r in runs]

    @classmethod
    def get_run(cls, run_id: int) -> Optional[Dict[str, Any]]:
        """Get a single detection run by ID"""
        run = UnifiedDetectionRun.query.get(run_id)
        if run:
            result = run.to_dict()
            result["groups"] = [g.to_dict() for g in run.groups]
            return result
        return None

    # ===== Statistics =====

    @classmethod
    def get_statistics(cls) -> Dict[str, Any]:
        """Get overall duplicate detection statistics"""
        total_groups = UnifiedDuplicateGroup.query.count()
        pending_groups = UnifiedDuplicateGroup.query.filter_by(
            status=GroupStatus.PENDING.value
        ).count()
        resolved_groups = UnifiedDuplicateGroup.query.filter_by(
            status=GroupStatus.RESOLVED.value
        ).count()

        # Get latest run
        latest_run = UnifiedDetectionRun.query.order_by(
            UnifiedDetectionRun.created_at.desc()
        ).first()

        # Calculate total potential savings
        total_savings = (
            db.session.query(db.func.sum(UnifiedDuplicateGroup.estimated_savings))
            .filter(UnifiedDuplicateGroup.status == GroupStatus.PENDING.value)
            .scalar()
            or 0
        )

        return {
            "total_groups": total_groups,
            "pending_groups": pending_groups,
            "resolved_groups": resolved_groups,
            "ignored_groups": UnifiedDuplicateGroup.query.filter_by(
                status=GroupStatus.IGNORED.value
            ).count(),
            "total_potential_savings": float(total_savings),
            "latest_run": latest_run.to_dict() if latest_run else None,
            "total_runs": UnifiedDetectionRun.query.count(),
        }

    # ===== Bulk Operations =====
