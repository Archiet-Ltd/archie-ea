"""An organisation of realistic size for the journeys: one application with hundreds of relationships around it.

``seed_calibration_ledger`` adds, to an organisation that already holds the approved GitHub model, the
application "Calibration Ledger" with its costs, six things that depend on it, two that it depends on, a
second-step dependency, and enough other relationships (filler of types that sort first) that none of the
cited relationships sits on the first page of the relationships list.
"""

from app import db
from app.models.application_portfolio import ApplicationComponent
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

ORIGIN = "acme/calibration-ledger pom.xml"
DEPENDANTS = ["Ledger Reports", "Ledger Archive", "Ledger Alerts", "Ledger Export", "Ledger Audit", "Ledger Sync"]


def _element(org_id, name, layer="Application", kind="ApplicationComponent"):
    el = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
    db.session.add(el)
    db.session.flush()
    return el


def _link(org_id, kind, source, target, label=ORIGIN):
    rel = ArchiMateRelationship(
        type=kind, source_id=source.id, target_id=target.id, organization_id=org_id,
        source_kind="import", source_label=label,
    )
    db.session.add(rel)
    return rel


def seed_calibration_ledger(app, org_id, minimum=420):
    """Returns ``{"element_id", "application_id", "direct": n, "cited_ids": [...]}``."""
    with app.app_context():
        ledger = _element(org_id, "Calibration Ledger")
        component = ApplicationComponent(
            name="Calibration Ledger", organization_id=org_id, archimate_element_id=ledger.id,
            lifecycle_status="operational", license_cost_annual=0, infrastructure_cost_monthly=2100,
            development_cost_annual=None, maintenance_cost=None, total_cost_of_ownership=25200,
        )
        db.session.add(component)
        dependants = [_element(org_id, name) for name in DEPENDANTS]
        upstream = [_element(org_id, "Ledger Database", "Technology", "Node"), _element(org_id, "Ledger Queue", "Technology", "Node")]
        second = _element(org_id, "Ledger Dashboard")
        cited = [_link(org_id, "Serving", ledger, d) for d in dependants]
        for up in upstream:
            _link(org_id, "Association", up, ledger)
        _link(org_id, "Serving", dependants[0], second)
        db.session.flush()
        # Filler: relationship types that sort before the cited ones, so no cited row is on page one.
        count = ArchiMateRelationship.query.filter_by(organization_id=org_id).count()
        pool = [_element(org_id, "Filler %d" % i) for i in range(40)]
        i = 0
        while count < minimum:
            _link(org_id, "Access" if i % 2 else "Aggregation", pool[i % 40], pool[(i + 7) % 40], label="acme/filler.yaml")
            count += 1
            i += 1
        db.session.commit()
        out = {
            "element_id": ledger.id,
            "application_id": component.id,
            "direct": len(dependants),
            "cited_ids": [r.id for r in cited],
        }
        db.session.remove()
        return out
