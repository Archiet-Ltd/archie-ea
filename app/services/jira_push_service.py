"""
Jira Push Service

Orchestrates pushing enriched application data from A.R.C.I.E to Jira.
Creates/updates Jira issues with field-level drift detection.

Key features:
- Per-app try/catch: single app failure never stops the batch
- SHA-256 hash drift detection: only pushes changed records
- Component auto-creation for business domains
- 3-system authority via jira_field_mapping.py
- Singleton pattern (mirrors AbacusSyncService)

Usage:
    service = get_jira_push_service()
    result = service.push_applications()
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config.jira_field_mapping import JIRA_FIELD_MAPPINGS
from app.services.adm_card_element_links import get_card_element_ids
from app.connectors.jira import JiraALMConnector, JiraAPIError
from app.models.jira_sync_tracking import JiraSyncTracking, PushStatus
from app.models.models import ExternalSystem

logger = logging.getLogger(__name__)

_instance = None


@dataclass
class PushResult:
    """Result of a push_applications() run."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self):
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "total": self.created + self.updated + self.skipped + self.failed,
            "errors": self.errors[:50],
        }


class JiraPushService:
    """Orchestrates pushing application data to Jira."""

    SYSTEM_NAME = "jira"

    def __init__(self):
        self.external_system = None
        self.connector = None
        self._project_key = None
        self._issue_type = "Task"
        self._custom_field_map = {}
        self._filter_countries = []
        self._filter_lifecycle = []

    def _initialize_connector(self) -> bool:
        """Load Jira config from ExternalSystem DB or .env fallback.

        Returns:
            True if connector is ready.
        """
        try:
            self.external_system = ExternalSystem.query.filter_by(
                system_name=self.SYSTEM_NAME
            ).first()

            if not self.external_system:
                import os

                env_base_url = os.getenv("JIRA_BASE_URL")
                env_username = os.getenv("JIRA_USERNAME")
                env_api_token = os.getenv("JIRA_API_TOKEN")
                env_project = os.getenv("JIRA_PROJECT_KEY")

                if all([env_base_url, env_username, env_api_token, env_project]):
                    logger.info("Using Jira credentials from .env file")
                    self._project_key = env_project

                    class TempConfig:
                        def __init__(self_inner):
                            self_inner.config = {
                                "base_url": env_base_url,
                                "username": env_username,
                                "api_token": env_api_token,
                                "project_keys": [env_project],
                            }

                    self.connector = JiraALMConnector(TempConfig())
                    return True
                else:
                    logger.error(
                        "Jira ExternalSystem not found and .env variables incomplete"
                    )
                    return False

            if not self.external_system.enabled:
                logger.info("Jira push integration disabled")
                return False

            try:
                credentials = json.loads(self.external_system.credentials)
            except (json.JSONDecodeError, TypeError):
                logger.error("Invalid Jira credentials format")
                return False

            config_data = {}
            if self.external_system.config_json:
                try:
                    config_data = json.loads(self.external_system.config_json)
                except (json.JSONDecodeError, TypeError):
                    pass

            self._project_key = config_data.get("project_key", "EA")
            self._issue_type = config_data.get("issue_type", "Task")
            self._custom_field_map = config_data.get("custom_field_map", {})
            self._filter_countries = config_data.get(
                "filter_countries", ["United Kingdom"]
            )
            self._filter_lifecycle = config_data.get(
                "filter_lifecycle", ["operational", "development", "testing"]
            )

            class TempConfig:
                def __init__(self_inner):
                    self_inner.config = {
                        "base_url": self.external_system.base_url,
                        "username": credentials.get("username"),
                        "api_token": credentials.get("api_token"),
                        "project_keys": [self._project_key],
                    }

            self.connector = JiraALMConnector(TempConfig())
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Jira connector: {e}", exc_info=True)
            return False

    def push_applications(
        self, app_ids: Optional[List[int]] = None, force: bool = False
    ) -> PushResult:
        """Push application data to Jira (main entry point).

        Args:
            app_ids: Specific application IDs to push. None = all matching filter.
            force: If True, push even if hash unchanged.

        Returns:
            PushResult with counts and errors.
        """
        if not self._initialize_connector():
            return PushResult(errors=["Failed to initialize Jira connector"])

        from app.models.application_portfolio import ApplicationComponent

        query = ApplicationComponent.query

        if app_ids:
            query = query.filter(ApplicationComponent.id.in_(app_ids))
        else:
            if self._filter_countries:
                query = query.filter(
                    ApplicationComponent.deployment_region.in_(self._filter_countries)
                )
            if self._filter_lifecycle:
                query = query.filter(
                    ApplicationComponent.lifecycle_status.in_(self._filter_lifecycle)
                )

        applications = query.all()
        result = PushResult()

        for app in applications:
            try:
                self._push_single_app(app, force, result)
            except Exception as e:
                result.failed += 1
                result.errors.append(f"App {app.id} ({app.name}): {e}")
                logger.error(f"Failed to push app {app.id}: {e}", exc_info=True)

        return result

    def push_single(self, app_id: int, force: bool = False) -> PushResult:
        """Push a single application to Jira.

        Args:
            app_id: Application ID to push.
            force: If True, push even if hash unchanged.

        Returns:
            PushResult.
        """
        return self.push_applications(app_ids=[app_id], force=force)

    def _push_single_app(
        self, app, force: bool, result: PushResult
    ) -> None:
        """Push one application, updating tracking and result counters.

        Args:
            app: ApplicationComponent instance.
            force: Push regardless of hash.
            result: PushResult to update in place.
        """
        jira_fields = self._build_jira_fields(app)
        current_hash = JiraSyncTracking.compute_hash(jira_fields)

        tracking = JiraSyncTracking.query.filter_by(
            application_id=app.id
        ).first()

        if tracking and tracking.jira_issue_key:
            if not force and not tracking.is_stale(current_hash):
                result.skipped += 1
                return

            resolved_fields = self._resolve_custom_fields(jira_fields)
            resolved_fields.pop("_component", None)

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    self.connector.update_issue(
                        tracking.jira_issue_key, resolved_fields
                    )
                )
            finally:
                loop.close()

            tracking.last_pushed_at = datetime.utcnow()
            tracking.last_pushed_hash = current_hash
            tracking.push_status = PushStatus.PUSHED.value
            tracking.field_snapshot = jira_fields
            tracking.error_message = None
            db.session.commit()
            result.updated += 1
        else:
            resolved_fields = self._resolve_custom_fields(jira_fields)
            component_name = resolved_fields.pop("_component", None)

            if component_name:
                loop = asyncio.new_event_loop()
                try:
                    comp = loop.run_until_complete(
                        self._ensure_component(self._project_key, component_name)
                    )
                    if comp:
                        resolved_fields["components"] = [{"id": comp["id"]}]
                finally:
                    loop.close()

            loop = asyncio.new_event_loop()
            try:
                created = loop.run_until_complete(
                    self.connector.create_issue(
                        self._project_key, self._issue_type, resolved_fields
                    )
                )
            finally:
                loop.close()

            if not tracking:
                tracking = JiraSyncTracking(
                    application_id=app.id,
                    jira_project_key=self._project_key,
                )
                db.session.add(tracking)

            tracking.jira_issue_key = created["key"]
            tracking.jira_component_name = component_name
            tracking.last_pushed_at = datetime.utcnow()
            tracking.last_pushed_hash = current_hash
            tracking.push_status = PushStatus.PUSHED.value
            tracking.field_snapshot = jira_fields
            tracking.error_message = None
            db.session.commit()
            result.created += 1

    def _build_jira_fields(self, app) -> Dict:
        """Apply JIRA_FIELD_MAPPINGS transforms to an ApplicationComponent.

        Args:
            app: ApplicationComponent instance.

        Returns:
            Dict of jira_field → transformed value.
        """
        fields = {}
        for rule in JIRA_FIELD_MAPPINGS:
            value = getattr(app, rule.app_field, None)
            if value is None and rule.required:
                logger.warning(
                    f"Required field '{rule.app_field}' is None for app {app.id}"
                )
            if rule.transform and value is not None:
                value = rule.transform(value)
            if value is not None:
                fields[rule.jira_field] = value
        return fields

    def _resolve_custom_fields(self, fields: Dict) -> Dict:
        """Replace logical custom field names with actual Jira field IDs.

        Args:
            fields: Dict with logical names like 'customfield_business_owner'.

        Returns:
            New dict with resolved field IDs.
        """
        resolved = {}
        for key, value in fields.items():
            if key.startswith("customfield_") and key in self._custom_field_map:
                actual_id = self._custom_field_map[key]
                if actual_id:
                    resolved[actual_id] = value
            else:
                resolved[key] = value
        return resolved

    async def _ensure_component(
        self, project_key: str, domain_name: str
    ) -> Optional[Dict]:
        """Create-or-get a Jira component for a business domain.

        Args:
            project_key: Jira project key.
            domain_name: Business domain name to use as component.

        Returns:
            Component dict or None on failure.
        """
        try:
            return await self.connector.create_component(
                project_key,
                domain_name,
                f"Business domain: {domain_name}",
            )
        except JiraAPIError as e:
            logger.warning(f"Failed to create component '{domain_name}': {e}")
            return None

    def detect_drift(self) -> PushResult:
        """Scan all tracked applications and flag those with drift.

        Returns:
            PushResult with counts (updated = drift detected).
        """
        from app.models.application_portfolio import ApplicationComponent

        result = PushResult()
        tracked = JiraSyncTracking.query.filter(
            JiraSyncTracking.push_status == PushStatus.PUSHED.value
        ).all()

        for tracking in tracked:
            try:
                app = ApplicationComponent.query.get(tracking.application_id)
                if not app:
                    tracking.push_status = PushStatus.ARCHIVED.value
                    result.skipped += 1
                    continue

                jira_fields = self._build_jira_fields(app)
                current_hash = JiraSyncTracking.compute_hash(jira_fields)

                if tracking.is_stale(current_hash):
                    tracking.push_status = PushStatus.DRIFT_DETECTED.value
                    result.updated += 1
                else:
                    result.skipped += 1
            except Exception as e:
                result.failed += 1
                result.errors.append(
                    f"Drift check app {tracking.application_id}: {e}"
                )

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Drift detection commit failed: {e}")
            result.errors.append(f"DB commit failed: {e}")

        return result

    def get_push_status(self) -> Dict:
        """Get summary statistics for admin UI.

        Returns:
            Dict with counts by push_status and total.
        """
        from sqlalchemy import func

        rows = (
            db.session.query(
                JiraSyncTracking.push_status,
                func.count(JiraSyncTracking.id),
            )
            .group_by(JiraSyncTracking.push_status)
            .all()
        )

        status_counts = {status: 0 for status in PushStatus}
        for status_val, count in rows:
            try:
                status_counts[PushStatus(status_val)] = count
            except ValueError:
                pass

        total = sum(status_counts.values())
        return {
            "total_tracked": total,
            "pending": status_counts[PushStatus.PENDING],
            "pushed": status_counts[PushStatus.PUSHED],
            "drift_detected": status_counts[PushStatus.DRIFT_DETECTED],
            "failed": status_counts[PushStatus.FAILED],
            "archived": status_counts[PushStatus.ARCHIVED],
            "project_key": self._project_key,
        }

    # -- KanbanCard Jira Push -----------------------------------------

    def _get_connector(self):
        """Initialize and return the Jira connector, or None if not configured."""
        if not self._initialize_connector():
            return None
        return self.connector

    def push_kanban_card(self, card_id: int, project_key: str = None) -> dict:
        """
        Create a Jira issue for a manually-created KanbanCard.

        Idempotent: if card.jira_issue_key is already set, returns success without
        calling create_issue() again. Never raises — all exceptions are caught,
        logged, and stored as jira_push_status='failed'.

        Args:
            card_id: KanbanCard.id
            project_key: Jira project key. Falls back to self._config["project_key"].

        Returns:
            {"success": True, "issue_key": "ARCH-42"} or {"success": False, "error": "..."}
        """
        from app.models.adm_kanban import KanbanCard
        from app.config.kanban_jira_field_mapping import build_jira_fields, get_jira_issue_type

        card = db.session.get(KanbanCard, card_id)
        if not card:
            return {"success": False, "error": f"KanbanCard {card_id} not found"}

        if card.jira_issue_key:
            return {"success": True, "issue_key": card.jira_issue_key, "skipped": True}

        try:
            connector = self._get_connector()
            if not connector:
                return {"success": False, "error": "Failed to initialize Jira connector"}
            proj = (
                project_key
                or getattr(self, "_project_key", None)
                or (getattr(self, "_config", {}) or {}).get("project_key", "")
                or ""
            )
            if not proj:
                return {"success": False, "error": "No JIRA_PROJECT_KEY configured"}

            card_data = {
                "title": card.title,
                "description": card.description or "",
                "priority": card.priority or "medium",
                "adm_phase_code": card.adm_phase.code if card.adm_phase else "A",
                "card_type": card.card_type or "requirement",
                # New ArchiMate/TOGAF fields (ADM-003)
                "arch_element_type": card.arch_element_type or "WorkPackage",
                "arch_domain": card.arch_domain or "",
                "togaf_deliverable": card.togaf_deliverable or "",
                "requires_arb_signoff": bool(card.requires_arb_signoff),
                "archimate_element_ids": get_card_element_ids(card),
            }
            fields = build_jira_fields(card_data)
            issue_type = get_jira_issue_type(card_data)

            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(connector.create_issue(
                    project_key=proj,
                    issue_type=issue_type,
                    fields_dict=fields,
                ))
            finally:
                loop.close()
            issue_key = result.get("key") if isinstance(result, dict) else None
            if not issue_key:
                raise ValueError(f"No issue key in Jira response: {result}")

            card.jira_issue_key = issue_key
            card.jira_push_status = "pushed"
            db.session.commit()

            logger.info("Pushed KanbanCard %s to Jira as %s", card_id, issue_key)
            return {"success": True, "issue_key": issue_key}

        except Exception as exc:
            db.session.rollback()
            try:
                card.jira_push_status = "failed"
                db.session.commit()
            except Exception as status_exc:
                logger.warning("Could not persist failed status for KanbanCard %s: %s", card_id, status_exc)
            logger.warning("Jira push failed for KanbanCard %s: %s", card_id, exc)
            return {"success": False, "error": str(exc)}

    def update_kanban_card_status(self, card_id: int, to_column: str) -> dict:
        """
        Update Jira issue status when a KanbanCard is moved to a new column.

        No-op if card has no jira_issue_key (not yet pushed). Never raises.

        Args:
            card_id: KanbanCard.id
            to_column: Target kanban column id (e.g. "under_development")

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        from app.models.adm_kanban import KanbanCard
        from app.config.kanban_jira_field_mapping import COLUMN_TO_JIRA_STATUS

        card = db.session.get(KanbanCard, card_id)
        if not card or not card.jira_issue_key:
            return {"success": False, "error": "Card not found or not yet pushed to Jira"}

        jira_status = COLUMN_TO_JIRA_STATUS.get(to_column)
        if not jira_status:
            return {"success": False, "error": f"No Jira status mapping for column '{to_column}'"}

        try:
            connector = self._get_connector()
            if not connector:
                return {"success": False, "error": "Failed to initialize Jira connector"}
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(connector.update_issue(
                    issue_key=card.jira_issue_key,
                    fields_dict={"status": {"name": jira_status}},
                ))
            finally:
                loop.close()
            logger.info(
                "Updated Jira issue %s status to '%s'", card.jira_issue_key, jira_status
            )
            return {"success": True}

        except Exception as exc:
            logger.warning(
                "Jira status update failed for KanbanCard %s: %s", card_id, exc
            )
            return {"success": False, "error": str(exc)}

    def get_kanban_push_status(self) -> Dict:
        """Get summary statistics for Kanban Tasks admin UI."""
        from app.models.adm_kanban import KanbanCard

        pushed = db.session.query(KanbanCard).filter(
            KanbanCard.jira_issue_key.isnot(None)
        ).count()
        failed = db.session.query(KanbanCard).filter(
            KanbanCard.jira_push_status == "failed",
            KanbanCard.jira_issue_key.is_(None),
        ).count()
        pending = db.session.query(KanbanCard).filter(
            KanbanCard.jira_issue_key.is_(None),
            db.or_(
                KanbanCard.jira_push_status.is_(None),
                KanbanCard.jira_push_status != "failed",
            ),
        ).count()
        total = db.session.query(KanbanCard).count()

        return {
            "pushed": pushed,
            "failed": failed,
            "pending": pending,
            "total": total,
        }

    def push_adm_phase_epics(self) -> Dict:
        """Create one Jira Epic per ADM phase that has pushed KanbanCards.

        Each ADM Phase maps to an ArchiMate 3.2 Plateau. Creates an Epic in Jira
        to serve as the Plateau boundary grouping all WorkPackage cards for that phase.

        Returns:
            {"pushed": N, "failed": N} or {"pushed": 0, "failed": 0, "error": "..."}
        """
        from app.models.adm_kanban import KanbanCard, ADMPhase

        connector = self._get_connector()
        if not connector:
            return {"pushed": 0, "failed": 0, "error": "No connector"}

        phases_with_cards = db.session.query(KanbanCard.adm_phase_id).filter(
            KanbanCard.jira_issue_key.isnot(None),
            KanbanCard.adm_phase_id.isnot(None),
        ).distinct().all()

        pushed = 0
        failed = 0

        for (phase_id,) in phases_with_cards:
            phase = db.session.get(ADMPhase, phase_id)
            if not phase:
                continue

            fields = {
                "summary": f"[{phase.code}] {phase.name}",
                "description": {
                    "version": 1,
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        f"ArchiMate Plateau: {phase.description or phase.name}. "
                                        f"TOGAF ADM Phase {phase.code}."
                                    ),
                                }
                            ],
                        }
                    ],
                },
                "labels": ["archimate:plateau", f"adm:phase-{phase.code}"],
            }

            try:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        connector.create_issue(
                            project_key=self._project_key,
                            issue_type="Epic",
                            fields_dict=fields,
                        )
                    )
                finally:
                    loop.close()

                epic_key = result.get("key") if isinstance(result, dict) else None

                try:
                    phase.jira_epic_key = epic_key
                    db.session.commit()
                except Exception as store_exc:
                    db.session.rollback()
                    logger.warning(
                        "Could not store jira_epic_key on ADMPhase %s: %s", phase.code, store_exc
                    )

                logger.info("Created Epic %s for ADMPhase %s", epic_key, phase.code)
                pushed += 1

            except Exception as exc:
                db.session.rollback()
                logger.warning("Failed to create Epic for ADMPhase %s: %s", phase.code, exc)
                failed += 1

        return {"pushed": pushed, "failed": failed}

    def push_all_unpushed_cards(self) -> Dict:
        """Push all KanbanCard rows that have no jira_issue_key yet."""
        from app.models.adm_kanban import KanbanCard

        if not self._initialize_connector():
            return {"pushed": 0, "failed": 0, "total": 0, "error": "Failed to initialize Jira connector"}

        cards = db.session.query(KanbanCard).filter(
            KanbanCard.jira_issue_key.is_(None)
        ).all()

        pushed = 0
        failed = 0
        for card in cards:
            result = self.push_kanban_card(card.id)
            if result.get("success") and not result.get("skipped"):
                pushed += 1
            elif not result.get("success"):
                failed += 1

        return {"pushed": pushed, "failed": failed, "total": len(cards)}

    def push_card_dependencies(self) -> Dict:
        """Create Jira Subtasks and Blocks links from KanbanCard dependency fields.

        ArchiMate 3.2 mapping:
        - depends_on  → TriggeringRelationship → Jira Subtask (child of parent card)
        - blocks      → AssociationRelationship → Jira Blocks issue link

        Only processes cards that already have jira_issue_key set (pushed cards).
        Idempotent: cards with jira_subtask_key already set are skipped.

        Returns:
            {"linked": N, "failed": N}
        """
        from app.models.adm_kanban import KanbanCard
        from app.config.kanban_jira_field_mapping import build_jira_fields

        connector = self._get_connector()
        if not connector:
            return {"linked": 0, "failed": 0, "error": "No connector"}

        # Cards that depend on something and are already pushed
        dep_cards = db.session.query(KanbanCard).filter(
            KanbanCard.depends_on.isnot(None),
            KanbanCard.jira_issue_key.isnot(None),
        ).all()

        linked = 0
        failed = 0

        for card in dep_cards:
            dep_ids = card.depends_on if isinstance(card.depends_on, list) else []
            for parent_id in dep_ids:
                parent = db.session.get(KanbanCard, parent_id)
                if not parent or not parent.jira_issue_key:
                    # Parent not pushed yet — skip
                    continue

                if card.jira_subtask_key:
                    # Already linked — idempotent skip
                    linked += 1
                    continue

                try:
                    card_data = {
                        "title": f"[DEP] {card.title}",
                        "description": card.description or "",
                        "priority": card.priority or "medium",
                        "adm_phase_code": card.adm_phase.code if card.adm_phase else "A",
                        "card_type": card.card_type or "requirement",
                        "archimate_element_ids": get_card_element_ids(card),
                    }
                    fields = build_jira_fields(card_data)
                    fields["parent"] = {"key": parent.jira_issue_key}

                    loop = asyncio.new_event_loop()
                    try:
                        result = loop.run_until_complete(
                            connector.create_issue(
                                project_key=self._project_key,
                                issue_type="Subtask",
                                fields_dict=fields,
                            )
                        )
                    finally:
                        loop.close()

                    subtask_key = result.get("key") if isinstance(result, dict) else None
                    if subtask_key:
                        card.jira_subtask_key = subtask_key
                        db.session.commit()
                        logger.info(
                            "Created Subtask %s under %s for KanbanCard %s",
                            subtask_key, parent.jira_issue_key, card.id,
                        )
                        linked += 1
                    else:
                        raise ValueError(f"No key in Jira response: {result}")

                except Exception as exc:
                    db.session.rollback()
                    logger.warning(
                        "Subtask creation failed for KanbanCard %s → parent %s: %s",
                        card.id, parent_id, exc,
                    )
                    failed += 1

        # Create Blocks issue links for cards with blocks[] populated
        blocks_cards = db.session.query(KanbanCard).filter(
            KanbanCard.blocks.isnot(None),
            KanbanCard.jira_issue_key.isnot(None),
        ).all()

        for card in blocks_cards:
            blocked_ids = card.blocks if isinstance(card.blocks, list) else []
            for blocked_id in blocked_ids:
                blocked = db.session.get(KanbanCard, blocked_id)
                if not blocked or not blocked.jira_issue_key:
                    continue

                try:
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(
                            connector.create_issue_link(
                                inward_key=blocked.jira_issue_key,
                                outward_key=card.jira_issue_key,
                                link_type="Blocks",
                            )
                        )
                    finally:
                        loop.close()
                    logger.info(
                        "Created Blocks link %s → %s",
                        card.jira_issue_key, blocked.jira_issue_key,
                    )
                    linked += 1

                except Exception as exc:
                    logger.warning(
                        "Blocks link failed %s → %s: %s",
                        card.id, blocked_id, exc,
                    )
                    failed += 1

        return {"linked": linked, "failed": failed}


def get_jira_push_service() -> JiraPushService:
    """Singleton factory for JiraPushService."""
    global _instance
    if _instance is None:
        _instance = JiraPushService()
    return _instance
