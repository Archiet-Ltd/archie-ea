"""The policy monitoring service, kept importable from its original path.

The one implementation lives in
``app.modules.solutions_strategic.v2.services.policy_monitoring_service``; every
caller of this path gets that same class, so there is a single copy to change.
"""

from app.modules.solutions_strategic.v2.services.policy_monitoring_service import (  # noqa: F401
    PolicyMonitoringService,
)

__all__ = ["PolicyMonitoringService"]
