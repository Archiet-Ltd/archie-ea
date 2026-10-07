"""Audit logging for sidebar menu changes."""

import logging
import json
from datetime import datetime
from flask import request
from flask_login import current_user

logger = logging.getLogger(__name__)


def _actor():
    """(id, label) of the signed-in user for the audit line. ``User`` has no ``username`` column, so the
    label is the e-mail; a signed-out caller is ``(None, "unknown")``."""
    if not getattr(current_user, "is_authenticated", False):
        return None, "unknown"
    return current_user.id, getattr(current_user, "email", None) or "unknown"


class SidebarMenuAuditLog:
    """Detailed audit logging for sidebar menu operations."""
    
    @staticmethod
    def log_toggle(item_key: str, new_state: bool, reason: str = None):
        """Log when a menu item is toggled."""
        user_id, user_name = _actor()
        ip_address = request.remote_addr if request else "unknown"
        
        audit_data = {
            'action': 'menu_item_toggled',
            'item_key': item_key,
            'new_state': new_state,
            'timestamp': datetime.utcnow().isoformat(),
            'user_id': user_id,
            'user_name': user_name,
            'ip_address': ip_address,
            'reason': reason or 'admin toggle',
            'user_agent': request.user_agent.string if request else None,
        }
        
        logger.info(f"AUDIT: Menu item toggled - {json.dumps(audit_data)}")
        return audit_data
    
    @staticmethod
    def log_update(item_key: str, changes: dict, reason: str = None):
        """Log when a menu item is updated."""
        user_id, user_name = _actor()
        ip_address = request.remote_addr if request else "unknown"
        
        audit_data = {
            'action': 'menu_item_updated',
            'item_key': item_key,
            'changes': changes,
            'timestamp': datetime.utcnow().isoformat(),
            'user_id': user_id,
            'user_name': user_name,
            'ip_address': ip_address,
            'reason': reason,
        }
        
        logger.info(f"AUDIT: Menu item updated - {json.dumps(audit_data)}")
        return audit_data
    
    @staticmethod
    def log_seed_operation(operation: str, item_count: int, errors: int = 0):
        """Log when sidebar menu is seeded."""
        audit_data = {
            'action': 'menu_seed_operation',
            'operation': operation,
            'items_processed': item_count,
            'errors': errors,
            'timestamp': datetime.utcnow().isoformat(),
        }
        
        logger.info(f"AUDIT: Menu seed operation - {json.dumps(audit_data)}")
        return audit_data
