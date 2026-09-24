"""One canonical record of "what roles the product assigns" and "what it
means for a user to have finished onboarding".

Before this module existed, the enterprise-role write and the
onboarding_completed_at write were each copied inline into three call sites
(the retired first-login modal's endpoint -- dashboard.api_onboarding_complete
-- and the five-screen flow's own onboarding.finish route), and the copies had
already drifted: dashboard.api_onboarding_complete's valid-role set was
missing business_architect, so a business architect submitting the old modal
silently had their role write dropped (harmless only because the modal
preselects the user's current role, so the resubmitted value matched what was
already there). One set, one write, three callers.
"""
from __future__ import annotations

import datetime

# The enterprise roles the product actually assigns via onboarding -- kept in
# sync with the picker in app/templates/layouts/admin_base.html's retired
# modal and app/modules/onboarding/templates/onboarding/screen5_twin.html.
VALID_ENTERPRISE_ROLES = (
    "solution_architect",
    "enterprise_architect",
    "business_architect",
    "arb_member",
    "portfolio_manager",
    "platform_admin",
    "cto",
    "application_manager",
    "procurement",
)


def set_role(user, enterprise_role) -> bool:
    """Apply *enterprise_role* to *user* if it is one the product assigns.

    Returns True if it was applied, False if it was empty or unrecognised.
    Does not commit -- the caller decides when to.
    """
    if enterprise_role and enterprise_role in VALID_ENTERPRISE_ROLES:
        user.enterprise_role = enterprise_role
        return True
    return False


def mark_complete(user, enterprise_role=None) -> None:
    """Record that *user* has finished onboarding, optionally updating their
    role in the same write. Does not commit -- the caller decides when to."""
    set_role(user, enterprise_role)
    user.onboarding_completed_at = datetime.datetime.utcnow()
