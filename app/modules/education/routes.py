"""Education eligibility.

Blueprint: education_bp
URL prefix: /education  (registered in app/_bootstrap/blueprints.py; bare /education is kept
for the public page)

Routes:
  GET  /education/eligibility — the institution the confirmed address belongs to, and the choice
  POST /education/eligibility — accept the terms and put the organisation on Education
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.decorators import admin_required
from app.services import billing_offers

logger = logging.getLogger(__name__)

education_bp = Blueprint("education", __name__)

TERMS_SENTENCE = "I will use this free plan for teaching or learning, not to run my institution's own operations."


def _org():
    from app.middleware.tenant_context import current_org

    return current_org()


@education_bp.route("/eligibility", methods=["GET", "POST"])
@login_required
@admin_required
def eligibility():
    org = _org()
    status = billing_offers.education_state(org, current_user)
    error = None
    role = request.form.get("role", "")
    if request.method == "POST" and status["state"] == "ready":
        if role not in billing_offers.EDUCATION_ROLES:
            error = billing_offers.REFUSAL_EDUCATION_ROLE
        elif request.form.get("terms") != "yes":
            error = billing_offers.REFUSAL_EDUCATION_TERMS
        else:
            result = billing_offers.grant_education(org, current_user, role, status["institution"], how="list")
            if result.granted:
                db.session.commit()
                flash("Your organisation is on Education.", "success")
                return redirect(url_for("billing.billing_index"))
            db.session.rollback()
            error = result.reason
    return render_template(
        "education/eligibility.html",
        status=status,
        roles=billing_offers.EDUCATION_ROLES,
        role=role,
        terms_sentence=TERMS_SENTENCE,
        error=error,
        period_end=billing_offers.education_period_end(),
    ), (400 if error else 200)
