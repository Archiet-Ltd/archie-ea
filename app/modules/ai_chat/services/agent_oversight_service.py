"""
Agent Oversight Service

Provides oversight controls for AI agent write operations:
- Pause one agent / stop all writes switch per organisation
- Refused-call log view (reads from existing audit log)
- Classification-vs-actual-touch check integration
"""

from datetime import datetime, timezone
from typing import Optional

from app import db
from app.models.agent_oversight_state import AgentOversightState
from app.models.audit_log import AuditLog
from app.models.user import User
from app.modules.ai_chat.services.tool_risk_classifier import get_tool_risk_classifier


class AgentOversightService:
    """
    Service for managing agent oversight controls.

    All operations are scoped to the current user's organisation.
    """

    def __init__(self, user_id: int):
        self.user_id = user_id
        self._user = None
        self._org_id = None

    def _get_user_and_org(self):
        """Load the acting user and their organisation."""
        if self._user is not None:
            return self._user, self._org_id

        from flask import g
        org_id = getattr(g, "current_org_id", None)
        if org_id is None:
            # Without a tenant context we cannot safely scope the query;
            # fail closed rather than querying without an org filter.
            return None, None
        query = User.query.filter_by(id=self.user_id, organization_id=org_id)
        self._user = query.first()
        if self._user:
            self._org_id = self._user.organization_id
        return self._user, self._org_id

    def _require_org_admin(self):
        """Check if the current user is an organisation admin."""
        user, org_id = self._get_user_and_org()
        if not user:
            return False, "User not found"
        if not user.is_org_admin:
            return False, "Organisation administrator privileges required"
        return True, None

    def _require_platform_admin(self):
        """Check if the current user is a platform admin."""
        user, org_id = self._get_user_and_org()
        if not user:
            return False, "User not found"
        if not user.is_platform_admin:
            return False, "Platform administrator privileges required"
        return True, None

    # ------------------------------------------------------------------ #
    # Pause / Resume Controls                                              #
    # ------------------------------------------------------------------ #

    def pause_all_writes(self, reason: str) -> dict:
        """
        Activate the stop-all-writes switch for the organisation.

        Takes effect before the next tool call is dispatched.
        """
        ok, err = self._require_org_admin()
        if not ok:
            return {"success": False, "error": err}

        user, org_id = self._get_user_and_org()
        state = AgentOversightState.get_for_org(org_id)
        state.pause(user.id, reason)
        db.session.commit()

        return {
            "success": True,
            "message": "All agent writes paused for this organisation",
            "state": self._state_to_dict(state),
        }

    def resume_all_writes(self) -> dict:
        """
        Deactivate the stop-all-writes switch for the organisation.
        """
        ok, err = self._require_org_admin()
        if not ok:
            return {"success": False, "error": err}

        user, org_id = self._get_user_and_org()
        state = AgentOversightState.get_for_org(org_id)
        state.resume()
        db.session.commit()

        return {
            "success": True,
            "message": "Agent writes resumed for this organisation",
            "state": self._state_to_dict(state),
        }

    def get_oversight_state(self) -> dict:
        """Get the current oversight state for the organisation."""
        user, org_id = self._get_user_and_org()
        if not user:
            return {"success": False, "error": "User not found"}

        state = AgentOversightState.get_for_org(org_id)
        return {
            "success": True,
            "state": self._state_to_dict(state),
        }

    def _state_to_dict(self, state: AgentOversightState) -> dict:
        """Convert oversight state to dictionary."""
        paused_by = None
        if state.paused_by_id:
            paused_by_user = db.session.get(User, state.paused_by_id)
            if paused_by_user:
                paused_by = {
                    "id": paused_by_user.id,
                    "name": paused_by_user.full_name(),
                    "email": paused_by_user.email,
                }
        return {
            "organization_id": state.organization_id,
            "writes_paused": state.writes_paused,
            "paused_by": paused_by,
            "paused_at": state.paused_at.isoformat() if state.paused_at else None,
            "reason": state.reason,
            "updated_at": state.updated_at.isoformat() if state.updated_at else None,
        }

    def is_write_paused(self) -> bool:
        """Check if writes are currently paused for the organisation."""
        user, org_id = self._get_user_and_org()
        if not user:
            return False
        state = AgentOversightState.get_for_org(org_id)
        return state.is_paused()

    # ------------------------------------------------------------------ #
    # Refused Call Log                                                     #
    # ------------------------------------------------------------------ #

    def get_refused_calls(
        self,
        user_id: Optional[int] = None,
        tool_name: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """
        Get refused tool calls from the audit log.

        Reads from the existing ai_tool_call audit log entries.
        Filterable by user, tool name.
        """
        user, org_id = self._get_user_and_org()
        if not user:
            return {"success": False, "error": "User not found"}

        # Only org admins can see the refused call log
        ok, err = self._require_org_admin()
        if not ok:
            return {"success": False, "error": err}

        query = AuditLog.query.filter(
            AuditLog.organization_id == org_id,
            AuditLog.action == "tool_refused",
        )

        if user_id:
            query = query.filter(AuditLog.user_id == user_id)
        if tool_name:
            # Filter by tool name in new_value JSON
            query = query.filter(AuditLog.new_value["tool"].astext == tool_name)

        total = query.count()
        refused = (
            query.order_by(AuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

        results = []
        for entry in refused:
            new_value = entry.new_value or {}
            results.append({
                "id": entry.id,
                "timestamp": entry.created_at.isoformat() if entry.created_at else None,
                "user_id": entry.user_id,
                "user_name": entry.user_email,
                "tool": new_value.get("tool"),
                "rule": new_value.get("rule"),
                "rule_description": new_value.get("rule_description"),
                "role": new_value.get("role"),
                "via": new_value.get("via"),
                "arguments": new_value.get("arguments"),
            })

        return {
            "success": True,
            "refused_calls": results,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ------------------------------------------------------------------ #
    # Tool Catalogue Export                                                #
    # ------------------------------------------------------------------ #

    def export_tool_catalogue(self, write_tools_only: bool = False) -> dict:
        """
        Export the complete tool catalogue with risk class and record types.

        Used by Security Architect to export write tools with risk class,
        record types written, and engine called.
        """
        user, org_id = self._get_user_and_org()
        if not user:
            return {"success": False, "error": "User not found"}

        # Security Architect or Platform Admin can export
        if not (user.has_role("security_architect") or user.is_platform_admin):
            return {"success": False, "error": "Security Architect or Platform Admin role required"}

        classifier = get_tool_risk_classifier()
        catalogue = classifier.export_catalogue(filter_write_only=write_tools_only)

        return {
            "success": True,
            "catalogue": catalogue,
            "exported_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "exported_by": user.full_name(),
        }

    # ------------------------------------------------------------------ #
    # Classification Check                                                 #
    # ------------------------------------------------------------------ #

    def check_tool_classification(self, tool_name: str) -> dict:
        """
        Check a tool's classification against its actual test touches.

        Returns whether the tool can be activated.
        """
        user, org_id = self._get_user_and_org()
        if not user:
            return {"success": False, "error": "User not found"}

        # Only org admins / platform admins can check classification
        ok, err = self._require_org_admin()
        if not ok:
            return {"success": False, "error": err}

        classifier = get_tool_risk_classifier()
        can_activate, reason = classifier.can_activate_tool(tool_name)
        result = classifier.classify_tool(tool_name)

        return {
            "success": True,
            "tool_name": tool_name,
            "can_activate": can_activate,
            "blocked_reason": reason,
            "classification": {
                "declared_risk_class": result.declared_risk_class,
                "declared_record_types": result.declared_record_types,
                "actual_record_types_touched": result.actual_record_types_touched,
                "is_compliant": result.is_compliant,
                "mismatch_details": result.mismatch_details,
            },
        }

    def get_all_classification_status(self) -> dict:
        """Get classification status for all tools."""
        user, org_id = self._get_user_and_org()
        if not user:
            return {"success": False, "error": "User not found"}

        ok, err = self._require_org_admin()
        if not ok:
            return {"success": False, "error": err}

        classifier = get_tool_risk_classifier()
        results = classifier.get_all_classifications()

        return {
            "success": True,
            "classifications": [
                {
                    "tool_name": r.tool_name,
                    "declared_risk_class": r.declared_risk_class,
                    "declared_record_types": r.declared_record_types,
                    "actual_record_types_touched": r.actual_record_types_touched,
                    "is_compliant": r.is_compliant,
                    "mismatch_details": r.mismatch_details,
                }
                for r in results
            ],
            "non_compliant_count": len([r for r in results if not r.is_compliant]),
        }

    # ------------------------------------------------------------------ #
    # Pre-dispatch Check (called by ToolExecutor)                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def check_write_allowed(organization_id: int) -> tuple[bool, Optional[str]]:
        """
        Check if agent writes are allowed for an organisation.

        Called by ToolExecutor before dispatching any mutating tool.
        Returns (allowed, reason_if_blocked).
        """
        state = AgentOversightState.get_for_org(organization_id)
        if state.is_paused():
            paused_by_name = "Unknown"
            if state.paused_by_id:
                paused_by_user = db.session.get(User, state.paused_by_id)
                if paused_by_user:
                    paused_by_name = paused_by_user.full_name()
            return False, (
                f"Agent writes are paused for this organisation "
                f"(paused by {paused_by_name} at {state.paused_at}): {state.reason}"
            )
        return True, None