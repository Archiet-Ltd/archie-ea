"""A contract and a licence under it, made directly in an organisation for a journey to read.

Each record is removed by its own key when the journey ends. Nobody signed in is deleted:
a person's audit rows are written while their session closes, so removing the person races them.
"""

from datetime import date, timedelta


def add_contract_and_licence(org_id, label):
    """Create one contract and one licence in ``org_id``; return ``{"contract", "licence", "names"}``."""
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.license_entitlement import LicenseEntitlement

    app = create_app("testing")
    contract_name = "%s support agreement" % label
    licence_name = "%s seat pack" % label
    with app.app_context():
        contract = VendorContract(organization_id=org_id, contract_name=contract_name,
                                  contract_number="C-%s" % label.upper()[:12], status="active",
                                  annual_cost=48000, start_date=date.today() - timedelta(days=30),
                                  end_date=date.today() + timedelta(days=300))
        db.session.add(contract)
        db.session.flush()
        licence = LicenseEntitlement(organization_id=org_id, contract_id=contract.id, product_name=licence_name, license_type="named_user",
                                     quantity_entitled=40, quantity_deployed=31)
        db.session.add(licence)
        db.session.commit()
        ids = {"contract": contract.id, "licence": licence.id}
        db.session.remove()
    return {**ids, "names": {"contract": contract_name, "licence": licence_name}}


def remove_records(*records):
    """Delete the licences, then the contracts, that ``add_contract_and_licence`` made, by key."""
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.license_entitlement import LicenseEntitlement

    app = create_app("testing")
    with app.app_context():
        for record in records:
            LicenseEntitlement.query.filter_by(id=record["licence"]).delete()
            VendorContract.query.filter_by(id=record["contract"]).delete()
        db.session.commit()
        db.session.remove()
