"""
Policy Enforcement Integration
Integrate policy enforcement with existing tool system
"""

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from app.utils.policy_enforcer import policy_enforcer
from app.utils.policy_tool_wrapper import (
    get_policy_compliance_report,
    policy_enforced_delete,
    policy_enforced_edit,
    policy_enforced_write_to_file,
)

logger = logging.getLogger(__name__)


class PolicyIntegratedTools:
    """Integration layer for policy-enforced tools"""

    def __init__(self):
        self.enabled = True
        self.strict_mode = True  # Block on any violation

    def write_to_file(
        self, file_path: str, content: str, context: Optional[str] = None, **kwargs
    ) -> bool:
        """Policy-enforced write_to_file"""
        if not self.enabled:
            # Fallback to original behavior if disabled
            try:
                import os

                parent = os.path.dirname(os.path.abspath(file_path))
                # Check if parent exists and is a file (not a directory)
                if parent and os.path.exists(parent) and not os.path.isdir(parent):
                    logger.error(f"Parent path exists but is not a directory: {parent}")
                    return False
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                return True
            except Exception as e:
                logger.error(f"Error writing file {file_path}: {str(e)}")
                return False

        return policy_enforced_write_to_file(file_path, content, context, **kwargs)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        context: Optional[str] = None,
        **kwargs,
    ) -> bool:
        """Policy-enforced edit"""
        if not self.enabled:
            # Fallback to original behavior
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = content.replace(old_string, new_string)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                return True
            except Exception as e:
                logger.error(f"Error editing file {file_path}: {str(e)}")
                return False

        return policy_enforced_edit(
            file_path, old_string, new_string, context, **kwargs
        )

    def delete_file(
        self, file_path: str, context: Optional[str] = None, **kwargs
    ) -> bool:
        """Policy-enforced delete"""
        if not self.enabled:
            # Fallback to original behavior
            try:
                import os

                os.remove(file_path)
                return True
            except Exception as e:
                logger.error(f"Error deleting file {file_path}: {str(e)}")
                return False

        return policy_enforced_delete(file_path, context, **kwargs)

    def get_status(self) -> Dict[str, Any]:
        """Get policy enforcement status"""
        return get_policy_compliance_report()

    def enable(self):
        """Enable policy enforcement"""
        self.enabled = True
        logger.info("Policy enforcement enabled")

    def disable(self):
        """Disable policy enforcement (for testing only)"""
        self.enabled = False
        logger.warning("Policy enforcement disabled - use only for testing")

    def set_strict_mode(self, strict: bool):
        """Set strict mode (block on any violation)"""
        self.strict_mode = strict
        logger.info(f"Policy strict mode set to: {strict}")


# Global integrated tools instance
integrated_tools = PolicyIntegratedTools()


# Monkey patch existing tools with policy enforcement
def patch_tools_with_policy():
    """Patch existing tool functions with policy enforcement"""

    # Import the original tools
    try:
        from app.utils import tools
    except ImportError:
        # info, not warning: app/utils/tools.py does not exist in this
        # codebase at all (it's an optional monkey-patch target for a module
        # this extract doesn't carry), so this fires unconditionally on every
        # boot -- flooding /admin/errors with a permanent, unfixable
        # "warning" rather than signalling anything actionable (10 Sep 2026).
        logger.info("Original tools module not found; policy patching skipped")
        return

    # Store original functions
    original_write_to_file = getattr(tools, "write_to_file", None)
    original_edit = getattr(tools, "edit", None)
    original_delete = getattr(tools, "delete_file", None)

    # Replace with policy-enforced versions
    def policy_write_to_file(file_path: str, content: str, **kwargs):
        context = kwargs.get("explanation") or kwargs.get("context")
        return integrated_tools.write_to_file(file_path, content, context, **kwargs)

    def policy_edit(file_path: str, old_string: str, new_string: str, **kwargs):
        context = kwargs.get("explanation") or kwargs.get("context")
        return integrated_tools.edit(
            file_path, old_string, new_string, context, **kwargs
        )

    def policy_delete_file(file_path: str, **kwargs):
        context = kwargs.get("explanation") or kwargs.get("context")
        return integrated_tools.delete_file(file_path, context, **kwargs)

    # Apply patches
    if original_write_to_file:
        tools.write_to_file = policy_write_to_file
        logger.info("Patched write_to_file with policy enforcement")

    if original_edit:
        tools.edit = policy_edit
        logger.info("Patched edit with policy enforcement")

    if original_delete:
        tools.delete_file = policy_delete_file
        logger.info("Patched delete_file with policy enforcement")


# Auto-patch on import
patch_tools_with_policy()


# Policy violation alert system
def check_for_critical_violations() -> List[Dict[str, Any]]:
    """Check for critical policy violations that need attention"""
    critical_violations = []

    for violation in policy_enforcer.violation_log:
        if violation.rule.severity in ["critical", "termination"]:
            critical_violations.append(
                {
                    "rule_name": violation.rule.name,
                    "description": violation.rule.description,
                    "context": violation.context,
                    "file_path": violation.file_path,
                    "detected_at": violation.detected_at.isoformat(),
                    "suggested_action": violation.suggested_action,
                    "severity": violation.rule.severity.value,
                }
            )

    return critical_violations


def generate_policy_alert() -> Optional[str]:
    """Generate policy alert if violations detected"""
    critical_violations = check_for_critical_violations()

    if not critical_violations:
        return None

    alert = "🚨 POLICY VIOLATION ALERT 🚨\n\n"
    alert += f"Critical violations detected: {len(critical_violations)}\n\n"

    for i, violation in enumerate(critical_violations, 1):
        alert += f"{i}. {violation['rule_name']}\n"
        alert += f"   Description: {violation['description']}\n"
        alert += f"   Context: {violation['context']}\n"
        if violation["file_path"]:
            alert += f"   File: {violation['file_path']}\n"
        alert += f"   Suggested: {violation['suggested_action']}\n\n"

    if policy_enforcer.termination_triggered:
        alert += "⚠️ TERMINATION CONDITION TRIGGERED ⚠️\n"
        alert += "LLM session termination required due to policy violations.\n"

    return alert


# Policy enforcement status check
def is_policy_enforcement_active() -> bool:
    """Check if policy enforcement is active"""
    return integrated_tools.enabled and not policy_enforcer.termination_triggered


# Emergency policy disable (for recovery)
def emergency_policy_disable():
    """Emergency disable of policy enforcement"""
    logger.critical("🚨 EMERGENCY POLICY DISABLE 🚨")
    logger.critical("This should only be used for recovery from policy violations")
    integrated_tools.disable()

    # Log emergency action
    policy_enforcer.violation_log.append(
        {
            "rule": type(
                "Rule", (), {"name": "EMERGENCY_DISABLE", "severity": "critical"}
            )(),
            "context": "Emergency policy disable",
            "detected_at": datetime.utcnow(),
        }
    )


# Policy enforcement initialization
def initialize_policy_enforcement():
    """Initialize policy enforcement system"""
    logger.info("Initializing policy enforcement system")

    # Check if enforcement is already active
    if is_policy_enforcement_active():
        logger.info("Policy enforcement already active")
        return

    # Enable enforcement
    integrated_tools.enable()
    integrated_tools.set_strict_mode(True)

    # Log initialization
    logger.info("Policy enforcement system initialized")
    logger.info(f"Loaded {len(policy_enforcer.rules)} policy rules")

    # Check for any existing violations
    if policy_enforcer.violation_log:
        logger.warning(
            f"Found {len(policy_enforcer.violation_log)} existing violations"
        )

        # Generate alert if critical violations exist
        alert = generate_policy_alert()
        if alert:
            logger.critical(alert)


# Auto-initialize on import
initialize_policy_enforcement()
