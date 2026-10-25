"""Two organisations with linked records for the Overview dashboard journeys.

Each organisation has applications (owned by a record, owned by text, owned by nobody, plus a
decommissioned one and a duplicate the lists leave out), model relationships between them, an
element that names its source, and a contract with an annual cost. The records are dated across the last 90 days in the change history.
"""
import contextlib
import datetime as dt
import uuid

from sqlalchemy import text

from .conftest import PASSWORD
from .fresh_org import create_fresh_org


def seed_overview_org(label, apps, contracts, with_owner_user=True, spread=True, last_year=False):
    """Create one organisation. ``apps`` is a list of dicts; ``contracts`` of (vendor, annual cost, months ago).

    ``spread`` dates the records across the last 90 days, as the demonstration organisation is.
    """
    from app import create_app, db
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.org_role import OrgRole
    from app.models.user import Role, User
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.services.history_spread import HISTORY_DAYS_AGO, dated_history

    info = create_fresh_org("enterprise_architect", extra_roles=("portfolio_manager",))
    app = create_app("testing")
    with app.app_context():
        org_id = info["org_id"]
        ea_id = info["user_ids"]["enterprise_architect"]
        owner_email = "owner.%s@example.com" % info["suffix"]
        if with_owner_user:
            owner = User(email=owner_email, first_name="Owen", last_name="Owner", organization_id=org_id,
                         enterprise_role="platform_admin", confirmed=True)
            owner.role = Role.query.filter_by(name="Administrator").one()
            owner.is_org_admin = True
            owner.password = PASSWORD
            db.session.add(owner)
            db.session.commit()
            OrgRole.set_role(org_id, owner.id, "org_admin", granted_by_id=owner.id)
            db.session.commit()
            info["emails"]["owner"] = owner_email
        # a first record added last year gives the year card two years to set side by side
        days_ago = ((400,) + HISTORY_DAYS_AGO) if last_year else HISTORY_DAYS_AGO
        with dated_history(days_ago=days_ago) if spread else contextlib.nullcontext():
            made = []
            for spec in apps:
                row = ApplicationComponent(
                    name=spec["name"], organization_id=org_id, lifecycle_status=spec.get("status", "operational"),
                    component_type=spec.get("type", "Application"), business_domain=spec.get("domain"),
                    total_cost_of_ownership=spec.get("cost"), legacy_application_owner=spec.get("owner_text"),
                )
                db.session.add(row)
                db.session.commit()
                if spec.get("owner_record"):
                    db.session.add(ApplicationOwner(user_id=ea_id, application_id=row.id, organization_id=org_id,
                                                    ownership_type="primary"))
                made.append(row)
            db.session.commit()
            elements = [ArchiMateElement.query.get(r.archimate_element_id) for r in made if r.archimate_element_id]
            for element in elements[:2]:
                element.source_kind = "connector"
                element.source_label = "GitHub"
            db.session.commit()
            for index in range(len(elements) - 1):
                rel = ArchiMateRelationship(type="serving", source_id=elements[0].id, target_id=elements[index + 1].id,
                                            organization_id=org_id)
                db.session.add(rel)
            db.session.commit()
            old = ArchiMateRelationship.query.filter_by(organization_id=org_id).first()
            old.created_at = dt.datetime.utcnow() - dt.timedelta(days=60)
            db.session.commit()
            today = dt.date.today()
            for number, (vendor, annual, months_ago) in enumerate(contracts):
                vendor_row = VendorOrganization(name="%s %s" % (vendor, info["suffix"]))
                db.session.add(vendor_row)
                db.session.flush()
                db.session.add(VendorContract(vendor_id=vendor_row.id,
                    organization_id=org_id, contract_name="%s agreement %s" % (vendor, uuid.uuid4().hex[:4]),
                    contract_number="OV-%s-%s" % (info["suffix"], number), status="active", annual_cost=annual,
                    start_date=today - dt.timedelta(days=30 * months_ago),
                ))
            db.session.commit()
        info["app_ids"] = {r.name: r.id for r in made}
        info["relationships"] = ArchiMateRelationship.query.filter_by(organization_id=org_id).count()
        db.session.remove()
    return info


def purge_org(org_id):
    """Remove an organisation's rows created for a journey."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        for table in ("archimate_relationships", "vendor_contracts", "application_owners", "archimate_elements",
                      "application_components", "entity_history"):
            db.session.execute(text("DELETE FROM %s WHERE organization_id = :o" % table), {"o": org_id})
        db.session.commit()
        db.session.remove()
