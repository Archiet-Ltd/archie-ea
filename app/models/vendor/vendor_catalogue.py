"""The public vendor catalogue (ADR-0021).

The catalogue product is the shared ``VendorProductDetail`` record (no
organisation column, the same for every customer). This module adds the two
things that record lacked:

``VendorCatalogueFact``
    One sourced fact about a catalogue product: an architecture element
    (ArchiMate component, service or interface), a relationship between two
    elements, a typical capability, an integration with another catalogue
    product, a certification, a data region, a sub-processor, a list-price
    tier, or the plain summary of what the product does. Every fact carries
    the address it came from and the date it was checked. A fact is draft,
    published or needs re-check; only published facts reach organisations, and
    a fact without a source cannot be published (enforced here and by a
    database check constraint).

``VendorCatalogueLink``
    One organisation's statement that a vendor product it uses is a given
    catalogue product. Tenant-scoped (``TenantMixin``): each organisation sees
    and writes only its own links. The catalogue itself never holds
    organisation data.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import validates

from app import db
from app.models.mixins.core import TenantMixin

FACT_KINDS = (
    "summary",
    "component",
    "service",
    "interface",
    "relationship",
    "capability",
    "integration",
    "certification",
    "data_region",
    "sub_processor",
    "price_tier",
)

# The ArchiMate element and relationship types a fragment may name.
ARCHIMATE_TYPES = {
    "component": ("ApplicationComponent", "TechnologyService", "Node", "SystemSoftware"),
    "service": ("ApplicationService", "TechnologyService"),
    "interface": ("ApplicationInterface", "TechnologyInterface"),
    "relationship": ("Serving", "Realization", "Composition", "Assignment", "Flow", "Access"),
}

REVIEW_STATES = ("draft", "published", "needs_recheck")


class UnsourcedFactError(ValueError):
    """Raised when a fact without a source address or check date is published."""


class VendorCatalogueFact(db.Model):
    """One sourced fact about a public catalogue product."""

    __tablename__ = "vendor_catalogue_facts"
    __table_args__ = (
        db.CheckConstraint(
            "review_state <> 'published' OR (source_url IS NOT NULL AND source_url <> '' "
            "AND checked_on IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_vendor_catalogue_facts_published_sourced",
        ),
        db.CheckConstraint(
            "review_state IN ('draft', 'published', 'needs_recheck')",
            name="ck_vendor_catalogue_facts_review_state",
        ),
        db.UniqueConstraint("product_detail_id", "kind", "name", name="uq_vendor_catalogue_fact"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    product_detail_id = db.Column(
        db.BigInteger,
        db.ForeignKey("vendor_product_details.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = db.Column(db.String(30), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    # ArchiMate element or relationship type, for architecture facts.
    archimate_type = db.Column(db.String(50))
    # Plain detail in our own words (never copied documentation).
    detail = db.Column(db.Text)
    # Structured attributes: protocol, relationship source and target, APQC
    # reference, related catalogue slug, price and unit.
    attributes = db.Column(db.JSON, default=dict)

    source_url = db.Column(db.String(1000))
    checked_on = db.Column(db.Date)
    # Words the research pipeline looks for on the source page to confirm it.
    evidence = db.Column(db.String(300))

    review_state = db.Column(db.String(20), nullable=False, default="draft", index=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_by_name = db.Column(db.String(200))
    reviewed_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = db.relationship(
        "VendorProductDetail",
        backref=db.backref("catalogue_facts", cascade="all, delete-orphan", lazy="select"),
    )

    @validates("kind")
    def _check_kind(self, _key, value):
        if value not in FACT_KINDS:
            raise ValueError(f"unknown catalogue fact kind: {value!r}")
        return value

    @validates("review_state")
    def _check_state(self, _key, value):
        if value not in REVIEW_STATES:
            raise ValueError(f"unknown review state: {value!r}")
        if value == "published" and not self.is_sourced:
            raise UnsourcedFactError(
                f"'{self.name}' has no source address or check date and cannot be published"
            )
        return value

    @property
    def is_sourced(self) -> bool:
        return bool((self.source_url or "").strip()) and self.checked_on is not None

    def publish(self, reviewer_name: str, reviewer_id: int | None = None, when: datetime | None = None):
        """Publish after a person's review. Refuses an unsourced fact."""
        if not self.is_sourced:
            raise UnsourcedFactError(
                f"'{self.name}' has no source address or check date and cannot be published"
            )
        self.reviewed_at = when or datetime.utcnow()
        self.reviewed_by_name = reviewer_name
        self.reviewed_by_id = reviewer_id
        self.review_state = "published"

    def mark_needs_recheck(self):
        self.review_state = "needs_recheck"

    @property
    def is_stale(self) -> bool:
        return self.checked_on is None or (date.today() - self.checked_on).days > 180

    def __repr__(self):
        return f"<VendorCatalogueFact {self.kind}:{self.name} [{self.review_state}]>"


class VendorCatalogueLink(TenantMixin, db.Model):
    """An organisation's link from a vendor product it uses to a catalogue product."""

    __tablename__ = "vendor_catalogue_links"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "vendor_product_id", name="uq_vendor_catalogue_link"),
    )

    id = db.Column(db.Integer, primary_key=True)
    vendor_product_id = db.Column(
        db.Integer, db.ForeignKey("vendor_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_detail_id = db.Column(
        db.BigInteger,
        db.ForeignKey("vendor_product_details.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    linked_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    linked_at = db.Column(db.DateTime, default=datetime.utcnow)

    vendor_product = db.relationship("VendorProduct")
    catalogue_product = db.relationship("VendorProductDetail")

    def __repr__(self):
        return f"<VendorCatalogueLink org={self.organization_id} vp={self.vendor_product_id} -> {self.product_detail_id}>"
