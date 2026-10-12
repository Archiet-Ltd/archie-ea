"""Capabilities with no supporting application, and capabilities with many."""
from flask import Blueprint, render_template
from flask_login import login_required

from app.modules.architecture.services.capability_support import capability_support

capability_support_bp = Blueprint("capability_support", __name__, url_prefix="/architecture/capability-support")


@capability_support_bp.route("/")
@login_required
def index():
    return render_template("architecture/capability_support.html", result=capability_support())
