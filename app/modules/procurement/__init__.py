"""
Procurement Module (NS-010, NS-011)

Provides contract management and license tracking for the Procurement persona.
Part of North Star Persona MVP implementation.

ADR Reference: docs/adr/0010-procurement-persona.md
"""

from flask import Blueprint

procurement_bp = Blueprint(
    "procurement",
    __name__,
    url_prefix="/procurement",
    template_folder="templates",
)



def acting_org_id():
    """The organisation the signed-in person is acting in: the active one.

    The role guards on these pages admit the administrator of the ACTIVE
    organisation, so every read and write here is fenced to that same
    organisation -- never to the person's home organisation, which may be a
    different one.
    """
    from flask_login import current_user

    from app.middleware.tenant_context import current_org_id

    return current_org_id() or current_user.organization_id


from . import routes  # noqa: F401, E402
from . import crud_routes  # noqa: F401, E402
from . import ai_routes  # noqa: F401, E402
