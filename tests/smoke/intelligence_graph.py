"""A small connected tenant for the Ask and Twin map browser tests.

    Service --serves--> Gateway --serves--> Ops Team
    Service --serves--> Portal
    Service ==worked out==> Ops Team          (a connection nobody drew)

The Gateway has an owner. The Portal and the Ops Team have none. The names share
one noun with a fresh suffix, so a search for the noun finds exactly these four
and two runs against the same database never collide.
"""

import datetime
import uuid


def seed_impact_graph(org_id, noun_prefix="Ledgerpay"):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    noun = "%s %s" % (noun_prefix, suffix)
    out = {"noun": noun, "org": org_id}
    with app.app_context():
        def element(name, kind, layer):
            row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        service = element("%s Service" % noun, "ApplicationComponent", "application")
        gateway = element("%s Gateway" % noun, "ApplicationComponent", "application")
        ops = element("%s Ops Team" % noun, "BusinessActor", "business")
        portal = element("%s Portal" % noun, "Node", "technology")

        def serves(source, target):
            rel = ArchiMateRelationship(
                type="Serving", source_id=source.id, target_id=target.id, organization_id=org_id
            )
            db.session.add(rel)
            db.session.commit()
            return rel

        first = serves(service, gateway)
        second = serves(gateway, ops)
        serves(service, portal)

        component = ApplicationComponent(
            name="Payments gateway app %s" % suffix, organization_id=org_id,
            archimate_element_id=gateway.id,
        )
        db.session.add(component)
        db.session.commit()
        unit = OrganizationUnit(name="Payments Platform %s" % suffix)
        db.session.add(unit)
        db.session.commit()
        db.session.add(ApplicationOwnership(
            application_id=component.id, organization_unit_id=unit.id,
            ownership_type="Business Owner",
        ))
        db.session.commit()

        # The worked-out connection goes in last: writing to an element or a
        # relationship marks any connection worked out over it as out of date.
        db.session.add(DerivedRelationship(
            organization_id=org_id,
            source_element_id=service.id,
            target_element_id=ops.id,
            derived_type="Serving",
            rule_id="serving-through-serving",
            chain=[first.id, second.id],
            chain_element_ids=[service.id, gateway.id, ops.id],
            depth=2,
            confidence=0.82,
            provenance="derivation",
            engine_version="1.0",
            computed_at=datetime.datetime.utcnow(),
            stale=False,
        ))
        db.session.commit()

        out.update(
            service=service.id, gateway=gateway.id, ops=ops.id, portal=portal.id,
            names={
                "service": service.name, "gateway": gateway.name,
                "ops": ops.name, "portal": portal.name,
            },
            owner=unit.name,
        )
    return out


def mark_derived_stale(graph):
    """Make the worked-out connection out of date the way the product does it.

    A person edits one of the relationships the connection was worked out from; the
    invalidation listener on the session then marks it stale in the same flush
    (stale, the time, and the reason). Nothing here writes those columns: the edit
    does, so the state is the one a real model would be in.

    Returns the stored row's staleness so a test can confirm it is genuine.
    """
    from app import create_app, db
    from app.models import ArchiMateRelationship
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    app = create_app("testing")
    with app.app_context():
        relationship = ArchiMateRelationship.query.filter_by(
            source_id=graph["service"], target_id=graph["gateway"]
        ).one()
        relationship.description = "Reviewed by the platform team"
        db.session.commit()
        row = DerivedRelationship.query.filter_by(
            source_element_id=graph["service"], target_element_id=graph["ops"]
        ).one()
        return {
            "stale": row.stale,
            "stale_since": row.stale_since,
            "stale_reason": row.stale_reason,
        }
