"""ProductInquiry — lead capture for a specific paid offer page.

No organisation: a visitor has none. No user link: a visitor is not signed in.

Unlike WaitlistSignup (one list, one fixed purpose: "launch news"), each row
here is a request about one named, paid offer, and ``consent_text`` is the
sentence that offer's page actually showed next to the checkbox the visitor
ticked. The route that writes this model reads that sentence from the same
page front-matter the template renders, so the stored consent can never say
something the visitor was not shown.
"""

from app import db


class ProductInquiry(db.Model):
    __tablename__ = "product_inquiries"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=True)
    # Which offer page this came from, e.g. "architecture_health_check" or
    # "team_annual_onboarding" — not a free-text field, set from the
    # submitting page's own front-matter.
    offer = db.Column(db.String(50), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    consent_text = db.Column(db.Text, nullable=False)

    __table_args__ = (
        # A visitor may inquire about both offers (two rows); resubmitting
        # the same offer's form with the same email is a duplicate, not a
        # second lead, mirroring WaitlistSignup's "duplicate shows the same
        # thanks and stores nothing new" behaviour.
        db.UniqueConstraint("email", "offer", name="uq_product_inquiry_email_offer"),
    )

    def __repr__(self):
        return f"<ProductInquiry {self.offer}:{self.email}>"
