"""The one definition of "the solutions this user may open".

UX_IA_REVIEW.md finding 2 (Critical): the health scorecard counted every Solution row while the
Solutions list counted only what the persona may open, so a new user saw "Total Solutions: 2" beside
"No solutions found". Two queries answered one question (docs/adr/0008-one-system-of-record.md).

Every surface that answers "how many solutions do I have" or lists them starts from this query and
adds only filters it can name to the user (status, search, domain), never a different notion of which
rows exist.

Scope, in order:
  * `[DELETED]` rows are never visible.
  * Admins, ARB voters, portfolio managers and the enterprise roles see the organisation's solutions;
    everyone else sees the ones they created.
  * A user with a business unit sees solutions whose business_domain names that unit, unless an admin
    asked for all (`?bu=all`).
Tenant scoping is applied below this by the TenantMixin query layer, as for every other model.
"""

from collections import namedtuple
import logging

from app import db
from app.models.solution_models import Solution

logger = logging.getLogger(__name__)

SolutionScope = namedtuple("SolutionScope", "query bu_filter_active bu_name show_all_override")

_ENTERPRISE_ROLES = ("enterprise_architect", "cto", "platform_admin")


def _is_admin(user):
    return hasattr(user, "is_admin") and user.is_admin()


def accessible_solutions(user, bu_all_requested=False):
    """Return a SolutionScope: the base query plus the business-unit flags the list page displays."""
    bu_filter_active = False
    bu_name = None
    show_all_override = False
    user_bu_id = getattr(user, "business_unit_id", None)  # model-safety-ok
    if bu_all_requested and _is_admin(user):
        show_all_override = True
    elif user_bu_id:
        try:
            from app.models.business_layer import BusinessActor

            bu_actor = db.session.get(BusinessActor, user_bu_id)
            if bu_actor:
                bu_name = bu_actor.name
                bu_filter_active = True
        except Exception as exc:
            logger.warning(
                "PLT-019: could not resolve business_unit_id=%s for solutions: %s", user_bu_id, exc
            )

    can_see_all = (
        _is_admin(user)
        or (hasattr(user, "can_vote_arb") and user.can_vote_arb())
        or (hasattr(user, "can_manage_portfolio") and user.can_manage_portfolio())
        or getattr(user, "enterprise_role", None) in _ENTERPRISE_ROLES
    )
    if can_see_all:
        query = Solution.query.filter(~Solution.name.like("[DELETED]%"))
    else:
        query = Solution.query.filter_by(created_by_id=user.id).filter(~Solution.name.like("[DELETED]%"))

    if bu_filter_active and not show_all_override and bu_name:
        safe_bu = bu_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(Solution.business_domain.ilike(f"%{safe_bu}%", escape="\\"))

    return SolutionScope(query, bu_filter_active, bu_name, show_all_override)
