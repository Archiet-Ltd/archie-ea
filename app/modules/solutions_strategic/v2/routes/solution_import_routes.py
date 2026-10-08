"""Retired ArchiMate OEF import URL (ENT-067).

The model-import screen is ``architect_ui.import_oef`` at
``/architecture/import/oef``, over the one OEF import engine
(``ArchiMateImportService``). That screen carries the preview and the import
strategy choice that used to live here, so this route only redirects the old
``/solutions/import/archimate`` URL to it, query string included.

Routes are attached to ``solution_design_bp`` (url_prefix=/solutions).
"""

from flask import redirect, request, url_for
from flask_login import login_required

from .solution_design_routes import solution_design_bp


@solution_design_bp.route("/import/archimate", methods=["GET"])
@login_required
def import_archimate_page():
    """Redirect (302) to the model-import screen."""
    target = url_for("architect_ui.import_oef")
    if request.query_string:
        target = f"{target}?{request.query_string.decode('utf-8', errors='replace')}"
    return redirect(target, code=302)
