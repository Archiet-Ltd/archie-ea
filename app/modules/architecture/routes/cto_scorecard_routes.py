"""R1-B85: the CTO scorecard -- supported-estate share, open exceptions
(escalatable), and where surfaces disagree. Gated on the "governance"
section, which both ROLE_CTO and ROLE_BUSINESS_ARCHITECT already carry --
the brief names a business architect as a second reader of the disagreement
panel, not a second page.
"""

from flask import Blueprint, flash, g, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.modules.architecture.services import cto_scorecard_service as svc
from app.utils.role_access import can_access_section

cto_scorecard_bp = Blueprint("cto_scorecard", __name__, url_prefix="/architecture/cto-scorecard")


def _guard():
    if not can_access_section(current_user, "governance"):
        return render_template("errors/403.html"), 403
    return None


@cto_scorecard_bp.route("/")
@login_required
def index():
    guard = _guard()
    if guard:
        return guard
    org_id = g.current_org_id
    return render_template(
        "architecture/cto_scorecard.html",
        versions=svc.supported_version_share(org_id),
        exceptions=svc.open_exceptions(org_id),
        agreement=svc.store_agreement_findings(org_id),
    )


@cto_scorecard_bp.route("/exceptions/<int:change_request_id>/escalate", methods=["POST"])
@login_required
def escalate_exception(change_request_id):
    guard = _guard()
    if guard:
        return guard
    ok = svc.escalate(g.current_org_id, change_request_id)
    if ok:
        flash("Exception escalated.", "success")
    else:
        flash("That exception could not be escalated.", "error")
    return redirect(url_for("cto_scorecard.index"))
