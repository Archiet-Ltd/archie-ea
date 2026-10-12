"""Completeness check for the applications an application manager owns."""

from flask import render_template
from flask_login import current_user, login_required

from app.decorators import requires_application_owner
from app.services.inventory_completeness_service import (
    REQUIRED_FACTS,
    STALE_AFTER_DAYS,
    check_applications,
)

from . import my_applications_bp
from .services import get_owned_applications


@my_applications_bp.route("/completeness")
@login_required
@requires_application_owner
def completeness():
    """Owned applications with missing facts or a record that has not changed for 90 days."""
    owned = get_owned_applications(current_user.id)
    return render_template(
        "my_applications/completeness.html",
        findings=check_applications(owned),
        owned_count=len(owned),
        required=[label for _, label in REQUIRED_FACTS],
        stale_after_days=STALE_AFTER_DAYS,
    )
