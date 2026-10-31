"""Frameworks: one gallery of every framework the platform supports.

The organisation's own frameworks come first (the compliance frameworks it has adopted), then
the industry process frameworks, then the other compliance frameworks it can adopt. Each card
opens the page that holds that framework's content.
"""

from flask import Blueprint, current_app, g, render_template
from flask_login import current_user, login_required

from app.seed_data.standard_control_totals import controls_loaded_text
from app.services.industry_apqc_service import IndustryAPQCService

frameworks_bp = Blueprint("frameworks", __name__, url_prefix="/frameworks")


def _compliance_cards(org_id):
    """(adopted, available): one dict per active regulatory framework, split by this organisation's adoption."""
    from app.models.compliance_models import RegulatoryFramework
    from app.models.regulatory_framework import FrameworkAdoption

    sources = {}
    if org_id is not None:
        sources = {
            row.framework_id: row.source
            for row in FrameworkAdoption.query.filter_by(organization_id=org_id, status="active").all()
        }
    adopted_ids = set(sources)
    adopted, available = [], []
    for framework in RegulatoryFramework.query.filter_by(status="active").order_by(RegulatoryFramework.name).all():
        loaded = framework.controls.count()
        card = {
            "code": framework.code,
            "name": framework.name,
            "description": framework.description,
            "jurisdiction": framework.jurisdiction,
            "controls": loaded,
            "partly_loaded": controls_loaded_text(framework.code, loaded),
            "adopted": framework.id in adopted_ids,
            "source": sources.get(framework.id),
        }
        (adopted if card["adopted"] else available).append(card)
    return adopted, available


@frameworks_bp.route("/")
@login_required
def gallery():
    """The frameworks gallery."""
    org_id = getattr(g, "current_org_id", None) or getattr(current_user, "organization_id", None)
    load_error = None
    adopted, available, industries = [], [], []
    try:
        adopted, available = _compliance_cards(org_id)
        industries = IndustryAPQCService().framework_summaries()
    except Exception:
        from app import db

        db.session.rollback()
        current_app.logger.exception("Error loading the frameworks gallery")
        load_error = "The frameworks could not be read."
    return render_template(
        "frameworks/gallery.html",
        adopted=adopted,
        available=available,
        industries=industries,
        load_error=load_error,
    )
