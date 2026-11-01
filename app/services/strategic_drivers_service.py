"""VA-002: Strategic Drivers service.

Links Driver ORM to UnifiedCapability and ApplicationComponent via ILIKE
name/description matching.  All access is via ORM — no raw SQL.
"""
import logging
from typing import Dict, List

from app import db
from app.models.motivation import Driver

logger = logging.getLogger(__name__)


class StrategicDriversService:
    """Service for linking strategic drivers to capabilities and applications.

    Drivers live in the ``drivers`` table (app.models.motivation).
    No raw SQL; all queries via SQLAlchemy ORM.
    """

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_drivers_for_capability(self, capability_id: int) -> List[Driver]:
        """Return Driver rows associated with a given capability.

        Uses the UnifiedCapability name to match Driver descriptions
        via ILIKE when no direct FK exists.  Returns empty list on error.
        """
        from app.models.unified_capability import UnifiedCapability

        try:
            cap = db.session.get(UnifiedCapability, capability_id)
            if cap is None:
                return []
            return (
                db.session.query(Driver)
                .filter(Driver.description.ilike(f"%{cap.name}%"))
                .order_by(Driver.business_priority.desc())
                .all()
            )
        except Exception as exc:
            logger.warning("get_drivers_for_capability(%s): %s", capability_id, exc)
            return []

    # ------------------------------------------------------------------
    # Coverage analysis
    # ------------------------------------------------------------------

    def infer_driver_coverage(self) -> List[Dict]:
        """Infer how well each Driver is covered by applications and capabilities.

        Algorithm (ORM-only, no N+1):
        1. Load all Driver, ApplicationComponent, and UnifiedCapability rows once.
        2. For each Driver, Python-level ILIKE match against pre-loaded data.
        3. coverage_score = (matching_apps + matching_caps) / total_entities,
           clamped to [0.0, 1.0].

        Returns a list of dicts with:
        {
            "driver_id": int,
            "driver_name": str,
            "matching_app_ids": list[int],
            "matching_capability_ids": list[int],
            "coverage_score": float,
        }
        Returns empty list on error; never raises.
        """
        from app.models.application import ApplicationComponent
        from app.models.unified_capability import UnifiedCapability

        result: List[Dict] = []
        try:
            drivers = db.session.query(Driver).all()
            if not drivers:
                return []

            # Preload all apps and caps once — avoids N+1 queries in the loop
            all_apps = (
                db.session.query(ApplicationComponent.id, ApplicationComponent.name,
                                 ApplicationComponent.description)
                .all()
            )
            all_caps = db.session.query(UnifiedCapability.id, UnifiedCapability.name).filter(UnifiedCapability.retired_into_id.is_(None)).all()

            total_entities = max(len(all_apps) + len(all_caps), 1)

            for driver in drivers:
                search_raw = driver.description or driver.name or ""
                search_term = search_raw[:50].lower()
                if not search_term:
                    result.append(
                        {
                            "driver_id": driver.id,
                            "driver_name": driver.name,
                            "matching_app_ids": [],
                            "matching_capability_ids": [],
                            "coverage_score": 0.0,
                        }
                    )
                    continue

                app_ids = [
                    row[0] for row in all_apps
                    if search_term in (row[1] or "").lower()
                    or search_term in (row[2] or "").lower()
                ]
                cap_ids = [
                    row[0] for row in all_caps
                    if search_term in (row[1] or "").lower()
                ]

                score = min((len(app_ids) + len(cap_ids)) / total_entities, 1.0)

                result.append(
                    {
                        "driver_id": driver.id,
                        "driver_name": driver.name,
                        "matching_app_ids": app_ids,
                        "matching_capability_ids": cap_ids,
                        "coverage_score": round(score, 4),
                    }
                )
        except Exception as exc:
            logger.warning("infer_driver_coverage: %s", exc)
            return []

        return result
