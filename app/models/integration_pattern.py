"""
Integration Pattern catalogue model.

Stores the 18 ARB-approved/conditional/blocked integration patterns for
the Integration Architecture Governance feature (INTARCH-001).

Usage:
    from app.models.integration_pattern import IntegrationPattern
    approved = IntegrationPattern.query.filter_by(approval_status='approved').all()

This is the one pattern and reference-architecture library. A reference
architecture is a row with ``is_reference_architecture`` set: it carries the
components it adds to a solution, the controls it provides, and the solution
context it fits. The interface rules (``allowed_auth_methods``,
``requires_encryption``, ``allows_personal_data``, plus the existing
``protocol`` and ``data_format``) are what the conformance reviewer checks an
interface against. Every added column is nullable: NULL means the pattern
states no rule on that point, never "anything is allowed" by invention.
"""

# Solution-context vocabulary a reference architecture is matched on. The
# keys are what a solution architect states; the values are the options.
REFERENCE_CONTEXT_OPTIONS = {
    "data": ("public", "internal", "confidential", "personal"),
    "latency": ("real_time", "near_real_time", "batch"),
    "hosting": ("cloud", "on_premise", "hybrid"),
}
REFERENCE_CONTEXT_LABELS = {
    "data": "Data handled",
    "latency": "Latency needed",
    "hosting": "Hosting",
    "public": "Public",
    "internal": "Internal",
    "confidential": "Confidential",
    "personal": "Personal data",
    "real_time": "Real time",
    "near_real_time": "Near real time",
    "batch": "Batch",
    "cloud": "Cloud",
    "on_premise": "On premises",
    "hybrid": "Hybrid",
}


from datetime import datetime

from app import db


class IntegrationPattern(db.Model):
    __tablename__ = "integration_patterns"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    vendor_key = db.Column(db.String(50), nullable=False)    # SAP_BTP | MICROSOFT | CROSS_VENDOR | GENERIC
    pattern_type = db.Column(db.String(50), nullable=False)  # middleware | event_driven | api | file | batch | rpa
    middleware = db.Column(db.String(100))
    source_system_hint = db.Column(db.String(100))
    target_system_hint = db.Column(db.String(100))
    protocol = db.Column(db.String(30))                      # odata | rest | soap | idoc | event | file
    data_format = db.Column(db.String(30))                   # json | xml | idoc | avro | csv
    approval_status = db.Column(db.String(20), default='approved')  # approved | conditional | blocked
    approval_notes = db.Column(db.Text)
    arb_conditions = db.Column(db.JSON)
    codegen_target = db.Column(db.String(50))                # sap-btp-integration | azure-logic-app | null
    description = db.Column(db.Text)
    documentation_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Interface rules checked by the conformance reviewer (nullable: no rule).
    allowed_auth_methods = db.Column(db.JSON, nullable=True)   # e.g. ["oauth2", "mtls"]
    requires_encryption = db.Column(db.Boolean, nullable=True)
    allows_personal_data = db.Column(db.Boolean, nullable=True)

    # Reference architecture: a pattern for a whole solution rather than one
    # interface. components: [{"name", "type", "layer"}]; applies_to_controls:
    # [{"name", "description"}]; fit_context: {"data": [...], "latency": [...],
    # "hosting": [...]} using REFERENCE_CONTEXT_OPTIONS values.
    is_reference_architecture = db.Column(db.Boolean, nullable=True, default=False)
    components = db.Column(db.JSON, nullable=True)
    applies_to_controls = db.Column(db.JSON, nullable=True)
    fit_context = db.Column(db.JSON, nullable=True)

    def __repr__(self):
        return f"<IntegrationPattern id={self.id} name={self.name!r} status={self.approval_status}>"
