"""Business Model Canvas + Operating Model.

A NEW, self-contained module implementing the classic Osterwalder/Pigneur
Business Model Canvas (9 blocks) plus a Ross/Weill operating-model archetype
selector. This is the signature Business-Architect artifact.

Convention notes (mirrors app/models/business_capabilities.py and
app/models/risk.py):
    - TenantMixin supplies organization_id (multi-tenant FK); the app's
      SQLAlchemy event listener in app.middleware.tenant_isolation
      auto-installs tenant filters on any model that has this column.
    - Every new column is nullable so a brand-new table (built by
      db.create_all() on first boot) never conflicts with the
      reconcile-schema drift-repair step.
"""

from app import db
from app.datetime_helpers import utcnow
from app.models.mixins import TenantMixin

# Ross/Weill (MIT CISR) operating model archetypes.
OPERATING_MODEL_TYPES = (
    "coordination",
    "unification",
    "replication",
    "diversification",
)

# The 9 Business Model Canvas blocks (Osterwalder/Pigneur), stored as
# free-form bullet-line Text for this first version.
CANVAS_BLOCKS = (
    "key_partners",
    "key_activities",
    "key_resources",
    "value_propositions",
    "customer_relationships",
    "channels",
    "customer_segments",
    "cost_structure",
    "revenue_streams",
)


class BusinessModelCanvas(TenantMixin, db.Model):
    """A single Business Model Canvas + Operating Model artifact."""

    __tablename__ = "business_model_canvases"

    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(256), nullable=True)
    description = db.Column(db.Text, nullable=True)

    # Ross/Weill operating model archetype: coordination / unification /
    # replication / diversification. Stored as a plain string (not a DB enum)
    # so new archetypes can be added without a migration.
    operating_model_type = db.Column(db.String(32), nullable=True)

    # ── The 9 Business Model Canvas blocks ──────────────────────────────
    key_partners = db.Column(db.Text, nullable=True)
    key_activities = db.Column(db.Text, nullable=True)
    key_resources = db.Column(db.Text, nullable=True)
    value_propositions = db.Column(db.Text, nullable=True)
    customer_relationships = db.Column(db.Text, nullable=True)
    channels = db.Column(db.Text, nullable=True)
    customer_segments = db.Column(db.Text, nullable=True)
    cost_structure = db.Column(db.Text, nullable=True)
    revenue_streams = db.Column(db.Text, nullable=True)

    # ADR-CV-1: the saved diagram this canvas projects onto, once one has
    # been chosen (export and share both resolve through this before falling
    # back to the tenant's most recent saved diagram of this template kind —
    # see resolve_canvas_saved_diagram_id). Nullable: most canvases have no
    # saved diagram yet.
    saved_diagram_id = db.Column(
        db.Integer, db.ForeignKey("saved_diagrams.id"), nullable=True, index=True
    )

    # Timestamps
    created_at = db.Column(db.DateTime, default=utcnow, nullable=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=True)

    def __repr__(self):
        return f"<BusinessModelCanvas {self.id} {self.name!r}>"

    def get_block(self, block_key):
        """Return the raw text content of one of the 9 canvas blocks."""
        if block_key not in CANVAS_BLOCKS:
            raise ValueError(f"Unknown canvas block: {block_key}")
        return getattr(self, block_key)

    def set_block(self, block_key, content):
        """Set the raw text content of one of the 9 canvas blocks."""
        if block_key not in CANVAS_BLOCKS:
            raise ValueError(f"Unknown canvas block: {block_key}")
        setattr(self, block_key, content)

    def to_dict(self):
        data = {
            "id": self.id,
            "organization_id": self.organization_id,
            "name": self.name,
            "description": self.description,
            "operating_model_type": self.operating_model_type,
            "saved_diagram_id": self.saved_diagram_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        for block_key in CANVAS_BLOCKS:
            data[block_key] = getattr(self, block_key)
        return data
