"""A cited public signal about an organisation: one news item, filing, figure, patent, paper or mention.

Written by ``app/modules/organization/public_signals``; read on the company profile and by the PESTLE,
SWOT and competitor views. Each row keeps the source it came from, the link, and the date it was
published, so a person can trace any signal to where it was read. A row is stored once per
organisation (``checksum`` is the source, the link and the title), and a refresh updates it in place.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class OrganizationSignal(TenantMixin, db.Model):
    __tablename__ = "organization_signals"
    __table_args__ = (db.UniqueConstraint("organization_id", "checksum", name="uq_organization_signal_checksum"),)

    id = db.Column(db.Integer, primary_key=True)
    # news | filing | financial | patent | research | mention | sentiment
    kind = db.Column(db.String(24), nullable=True, index=True)
    source = db.Column(db.String(32), nullable=True, index=True)  # registry key, e.g. "sec_edgar"
    source_label = db.Column(db.String(80), nullable=True)
    subject = db.Column(db.String(200), nullable=True)  # who it is about: the company, or a named competitor
    title = db.Column(db.String(500), nullable=True)
    summary = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(1000), nullable=True)
    published_at = db.Column(db.DateTime, nullable=True)  # None when the source states no date
    fetched_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
    pestle_factor = db.Column(db.String(16), nullable=True)  # political, economic, social, technological, legal, environmental
    swot_quadrant = db.Column(db.String(16), nullable=True)  # strength, weakness, opportunity, threat
    sentiment_score = db.Column(db.Float, nullable=True)  # -1 to 1, set only where a model scored it
    details = db.Column(db.JSON, nullable=True)  # the source's own figures (form type, price, patent number ...)
    checksum = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
