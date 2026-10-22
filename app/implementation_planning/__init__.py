"""
DEPRECATED: This file is migrated to app/modules/applications/.
Registration is now centralized via app.modules.applications.register().
Do NOT modify -- kept as fallback until Phase 6 cleanup.

Implementation Planning Blueprint

ArchiMate 3.2 Implementation & Migration Planning module for enterprise architecture.

This blueprint provides comprehensive implementation planning capabilities including:
- WorkPackage management and tracking
- Deliverable management
- Gap analysis and discovery
- Plateau state management
- ImplementationEvent tracking
- Gantt chart visualization
- Export capabilities (CSV, PNG, JPG)

Complies with:
- ArchiMate 3.2 Specification
- Flask blueprint patterns
- RESTful API design
- shadcn/ui design system
"""

from flask import Blueprint, abort

# Create the implementation planning blueprint
implementation_planning = Blueprint(
    "implementation_planning", __name__, template_folder="templates", url_prefix="/implementation"
)

# Mark as guardrailed BEFORE routes are registered
from app.core.compat import mark_blueprint_guardrailed
mark_blueprint_guardrailed(implementation_planning)


@implementation_planning.before_request
def _check_feature_flag():
    """Answer 404 only when an administrator has switched the module off.

    A database with no ``architecture_implementation_planning`` row has the
    module on, as every other feature flag does (``is_feature_enabled``): the
    gap analysis page and the work packages list link straight to these pages,
    so a missing row must never turn those links into dead ends. A row that
    exists and is not active still switches the module off.
    """
    from app.models.feature_flags import FeatureFlag

    if not FeatureFlag.is_feature_enabled("architecture_implementation_planning"):
        abort(404)


# Import routes to register them
from . import routes
