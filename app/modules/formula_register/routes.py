"""R1-B34: Formula register routes (TB-0135).

A reviewer edits a composite score's weights here; editing creates a new
version rather than mutating the one past scores were computed with, so
"which formula produced this number" is always answerable.
"""
from __future__ import annotations

import math

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.decorators.requires_role import requires_role
from app.models.formula_register import FormulaRegister

formula_register_bp = Blueprint(
    "formula_register", __name__, template_folder="../../templates"
)

# Only this one key is wired to a real score today (R1-B34 PR 1); other
# composite scores joining the register is Release 3 (MIG-C-0015) per the
# brief, so the picker is a closed list, not free text, until then.
FORMULA_KEYS = {"rationalization_overall": "Rationalization overall score"}

# The four dimensions RationalizationScoringService actually weights
# (get_weights_dict() in ScoringConfiguration). Names are checked against
# this set, not accepted as free text, so a typo is refused rather than
# silently stored as an unused extra key.
FORMULA_DIMENSIONS = {
    "rationalization_overall": [
        "technical_health", "business_value", "cost_efficiency", "vendor_risk",
    ],
}


@formula_register_bp.route("/")
@login_required
def index():
    org_id = g.current_org_id
    versions = {
        key: FormulaRegister.query.filter_by(organization_id=org_id, formula_key=key)
        .order_by(FormulaRegister.version.desc())
        .all()
        for key in FORMULA_KEYS
    }
    return render_template(
        "formula_register/index.html",
        formula_keys=FORMULA_KEYS,
        formula_dimensions=FORMULA_DIMENSIONS,
        versions=versions,
    )


@formula_register_bp.route("/<formula_key>/new-version", methods=["POST"])
@login_required
@requires_role(["portfolio_manager"])
def new_version(formula_key):
    if formula_key not in FORMULA_KEYS:
        flash("Unknown formula.", "error")
        return redirect(url_for("formula_register.index"))

    org_id = g.current_org_id
    known_dimensions = set(FORMULA_DIMENSIONS.get(formula_key, []))
    inputs = {}
    errors = []
    names = request.form.getlist("input_name")
    weights = request.form.getlist("input_weight")
    for name, raw_weight in zip(names, weights):
        name = (name or "").strip()
        raw_weight = (raw_weight or "").strip()
        if not name or not raw_weight:
            # A blank weight means "unset for this version", not an error --
            # the form always renders one row per known dimension, and a
            # reviewer need not provide every one in a single submission.
            continue
        if known_dimensions and name not in known_dimensions:
            errors.append(f"'{name}' is not a known input for this formula (expected one of: {', '.join(sorted(known_dimensions))})")
            continue
        try:
            weight = float(raw_weight)
        except ValueError:
            errors.append(f"{name}: '{raw_weight}' is not a number")
            continue
        if not math.isfinite(weight) or weight < 0:
            errors.append(f"{name}: weight must be a finite number >= 0")
            continue
        inputs[name] = weight

    if not inputs:
        errors.append("At least one input/weight pair is required.")

    if errors:
        flash("Could not create a new version: " + "; ".join(errors), "error")
        return redirect(url_for("formula_register.index"))

    # Owner is whoever submitted this version; reviewer/reviewed_at stay
    # unset here -- self-stamping the submitter as their own reviewer would
    # fabricate a review that never happened. A separate review action
    # (not built in this PR) sets those fields.
    FormulaRegister.activate_new_version(
        org_id,
        formula_key,
        inputs=inputs,
        owner_user_id=current_user.id,
    )
    db.session.commit()
    flash(f"New version of '{FORMULA_KEYS[formula_key]}' activated.", "success")
    return redirect(url_for("formula_register.index"))
