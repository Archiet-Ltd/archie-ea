"""ServiceIncident: a period when the platform was degraded or down.

The service-status page lists these as the incident history every signed-in
user can read. An incident is a fact about the platform, not about one
organisation's data, so like ``ErrorEvent`` this is deliberately not a
``TenantMixin`` model: every organisation sees the same history. It carries no
organisation id, no user id and no personal data -- a title, a short summary,
the impact and the start and end times. Incidents are opened and resolved by
the platform operator (``flask service-incident``).
"""

from datetime import datetime

from app import db

IMPACT_DEGRADED = "degraded"
IMPACT_OUTAGE = "outage"
IMPACTS = (IMPACT_DEGRADED, IMPACT_OUTAGE)


class ServiceIncident(db.Model):  # migration-exempt
    """One platform incident, open until ``resolved_at`` is set."""

    __tablename__ = "service_incidents"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, nullable=True)
    impact = db.Column(db.String(16), nullable=False, default=IMPACT_DEGRADED)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    def __repr__(self):
        return f"<ServiceIncident {self.id} {self.impact} open={self.is_open}>"
