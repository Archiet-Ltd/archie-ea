"""
Data Sharing Agreement model (R1-B80, PB-0355).

No data-sharing-agreement store existed before this brief; it is added
beside DataLineage/DataTransformation, on TenantMixin, not as a new
schema -- one more governed record alongside the lineage graph it covers,
not a separate subsystem.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin


class DataSharingAgreement(TenantMixin, db.Model):  # migration-exempt
    # ADR-0003: tenant-scoped -- organization_id is set at creation, not backfilled
    """
    An organisation's agreement governing a data flow to or from a vendor.

    Links to the DataLineage rows it governs (the flow(s) it covers) and to
    VendorOrganization (R1-B10's canonical, deliberately NOT tenant-scoped
    vendor master -- see its own module docstring) for the partner the
    agreement is with. A flow reaching that vendor with no linked,
    unexpired agreement is "unagreed"; see
    DataSharingAgreement.unagreed_flow_ids.
    """

    __tablename__ = "data_sharing_agreements"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    # VendorOrganization is global reference data (not TenantMixin, see its
    # own docstring) -- the FK names which vendor, the organization_id
    # column (from TenantMixin) names which tenant's agreement this is.
    vendor_organization_id = db.Column(
        db.Integer, db.ForeignKey("vendor_organizations.id"), nullable=False, index=True
    )

    # The flows this agreement covers. A flow can be covered by more than
    # one agreement over its life (renewal); the association table keeps
    # that history rather than overwriting a single FK on DataLineage.
    status = db.Column(db.String(20), default="active", nullable=False)  # active, expired, terminated
    effective_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date, nullable=True)  # NULL: no expiry recorded

    # PB-0237 (R1-B81): a cross-border flow needs either this agreement or
    # a documented transfer basis -- recorded here since the basis belongs
    # to the agreement, not to the flow or the vendor.
    transfer_basis = db.Column(db.String(100), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    vendor_organization = db.relationship("VendorOrganization")
    created_by = db.relationship("User", backref="created_data_sharing_agreements")
    flows = db.relationship(
        "DataLineage", secondary="data_sharing_agreement_flows",
        backref="sharing_agreements",
    )

    @staticmethod
    def partner_bound_flow_ids(organization_id: int):
        """DataLineage rows whose target reaches a vendor-linked
        application (via ApplicationComponent.primary_vendor_product's
        VendorOrganization, R1-B10's canonical vendor master) -- the flows
        a data-sharing agreement could ever govern."""
        from app import db
        from app.models.all_missing_models import DataLineage
        from app.models.application_portfolio import ApplicationComponent
        from app.models.vendor.vendor_organization import VendorProduct

        rows = db.session.execute(
            db.select(DataLineage.id)
            .join(
                ApplicationComponent,
                ApplicationComponent.archimate_element_id == DataLineage.target_archimate_element_id,
            )
            .join(VendorProduct, VendorProduct.id == ApplicationComponent.vendor_product_id)
            .where(DataLineage.organization_id == organization_id)
            .where(VendorProduct.vendor_organization_id.isnot(None))
        ).scalars().all()
        return set(rows)

    @staticmethod
    def unagreed_flow_ids(organization_id: int):
        """Partner-bound flows with no linked, current (active,
        unexpired) agreement -- PB-0355's "flag partner-bound flows with
        no agreement". A flow stops appearing here the moment a current
        agreement links to it, with no separate "resolved" state to keep
        in sync."""
        from app import db

        partner_bound = DataSharingAgreement.partner_bound_flow_ids(organization_id)
        if not partner_bound:
            return set()

        agreed = db.session.execute(
            db.select(data_sharing_agreement_flows.c.data_lineage_id)
            .join(
                DataSharingAgreement,
                DataSharingAgreement.id == data_sharing_agreement_flows.c.agreement_id,
            )
            .where(DataSharingAgreement.organization_id == organization_id)
            .where(DataSharingAgreement.status == "active")
            .where(
                db.or_(
                    DataSharingAgreement.expiry_date.is_(None),
                    DataSharingAgreement.expiry_date >= datetime.utcnow().date(),
                )
            )
        ).scalars().all()
        return partner_bound - set(agreed)

    def is_current(self) -> bool:
        """True while the agreement is active and not past its expiry
        date. A NULL expiry_date is read as "no expiry recorded", not as
        "never expires" -- the brief's own "never invent data" rule: an
        unset date is an absence, never a license to assume forever."""
        if self.status != "active":
            return False
        if self.expiry_date is None:
            return True
        return self.expiry_date >= datetime.utcnow().date()

    def __repr__(self):
        return f"<DataSharingAgreement {self.name} (vendor={self.vendor_organization_id})>"


# Association table: which DataLineage flows a DataSharingAgreement covers.
# A plain many-to-many junction, not a model of its own -- the agreement
# and the flow are the two things with their own identity and lifecycle;
# the link between them carries no attributes of its own.
data_sharing_agreement_flows = db.Table(
    "data_sharing_agreement_flows",
    db.Column(
        "agreement_id", db.Integer,
        db.ForeignKey("data_sharing_agreements.id", ondelete="CASCADE"), primary_key=True,
    ),
    db.Column(
        "data_lineage_id", db.Integer,
        db.ForeignKey("data_lineage.id", ondelete="CASCADE"), primary_key=True,
    ),
)
