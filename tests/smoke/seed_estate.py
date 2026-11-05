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


HUB_RECORDED = (2026, 3, 4, 9, 5)


def seed_hub_service(app, org_id, records=25):
    """An application with many relationship records in both directions to a single other element.

    It is the shape that made a ranking by relationship records and a count of distinct elements
    disagree: ``records`` relationships, one element on the other side. Returns ids and the count.
    """
    from datetime import datetime, timezone

    with app.app_context():
        hub = _element(org_id, "Hub Service")
        peer = _element(org_id, "Hub Peer")
        component = ApplicationComponent(
            name="Hub Service", organization_id=org_id, archimate_element_id=hub.id, lifecycle_status="operational",
        )
        db.session.add(component)
        kinds = ["Serving", "Association", "Flow", "Triggering", "Access", "Realization", "Aggregation"]
        recorded = datetime(*HUB_RECORDED, tzinfo=timezone.utc)
        made = []
        for i in range(records):
            outgoing = i % 5 != 4
            rel = _link(org_id, kinds[i % len(kinds)], hub if outgoing else peer, peer if outgoing else hub, label="acme/hub.yaml")
            rel.source_recorded_at = recorded
            made.append(rel)
        db.session.commit()
        out = {"element_id": hub.id, "application_id": component.id, "records": records, "ids": [r.id for r in made]}
        db.session.remove()
        return out


LONG_ORIGIN = "acme/calibration-ledger/services/ledger-reconciliation/src/main/resources/application-production.properties"


def seed_long_origin(app, org_id, count=3):
    """Relationships of the type the Ledger's cited ones have, whose recorded origin is a long unbroken path.

    A repository path has no spaces, so on a phone it is the widest thing in the relationships table;
    it shares a page with the citations because it sorts beside them.
    """
    with app.app_context():
        names = ["Ledger Reports", "Ledger Archive", "Ledger Alerts"]
        elements = {e.name: e for e in ArchiMateElement.query.filter(ArchiMateElement.organization_id == org_id, ArchiMateElement.name.in_(names)).all()}
        made = []
        for i in range(count):
            made.append(_link(org_id, "Serving", elements[names[i % 3]], elements[names[(i + 1) % 3]], label=LONG_ORIGIN))
        db.session.commit()
        ids = [r.id for r in made]
        db.session.remove()
        return ids
