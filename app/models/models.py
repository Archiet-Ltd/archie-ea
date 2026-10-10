# mass-deletion-ok — FRAG-042: 21 dead model classes removed (verified zero external references)
import logging
import os
from datetime import datetime

from flask import current_app  # dead-code-ok
from flask_login import current_user  # dead-code-ok
from sqlalchemy import types
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import validates

from .. import db  # main SQLAlchemy object
from .constants import ArchiMateLayer
from .mixins import TenantMixin

_key_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fernet-based encrypted column type
# ---------------------------------------------------------------------------
# Set ARCHIE_KEY_SECRET to a URL-safe base-64 Fernet key (32 raw bytes).
# Generate once with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Store it in your .env file — never commit it.
#
# Backward-compatible: if the env var is absent OR if a stored value cannot be
# decrypted (e.g. a plaintext legacy key), the raw value is returned as-is so
# existing deployments keep working until every key has been re-saved through
# the Admin UI.
# ---------------------------------------------------------------------------

class _EncryptedString(types.TypeDecorator):
    """SQLAlchemy column type that transparently Fernet-encrypts on write and decrypts on read."""

    impl = types.String
    cache_ok = True

    _FERNET_PREFIX = b"gAAAAA"  # all Fernet tokens start with this

    @staticmethod
    def _fernet():
        """Return a Fernet instance, or None if ARCHIE_KEY_SECRET is not set."""
        secret = os.environ.get("ARCHIE_KEY_SECRET", "").strip()
        if not secret:
            return None
        try:
            from cryptography.fernet import Fernet
            return Fernet(secret.encode() if isinstance(secret, str) else secret)
        except Exception as exc:
            _key_log.error("ARCHIE_KEY_SECRET is set but invalid — keys will not be encrypted: %s", exc)
            return None

    def process_bind_param(self, value, dialect):
        """Encrypt plaintext key before writing to DB."""
        if value is None:
            return value
        f = self._fernet()
        if f is None:
            return value  # no key configured → store as-is (warn on startup, not here)
        raw = value.encode() if isinstance(value, str) else value
        # Don't double-encrypt values that are already Fernet tokens
        if raw.startswith(self._FERNET_PREFIX):
            return value
        return f.encrypt(raw).decode()

    def process_result_value(self, value, dialect):
        """Decrypt Fernet token read from DB; return plaintext legacy values unchanged."""
        if value is None:
            return value
        f = self._fernet()
        if f is None:
            return value
        raw = value.encode() if isinstance(value, str) else value
        if not raw.startswith(self._FERNET_PREFIX):
            # Plaintext legacy key — return as-is; will be encrypted next time it's saved
            return value
        try:
            return f.decrypt(raw).decode()
        except Exception:
            _key_log.warning("Failed to decrypt api_key value — returning raw (may be legacy plaintext)")
            return value


# ---------------------------------------------------------------------------
# ArchiMate layer names — one canonical casing, two accepted conventions
# ---------------------------------------------------------------------------
# `archimate_elements.layer` accumulated both casings, and Postgres `=` is
# case-sensitive, so the column silently partitioned itself:
#
#     [Application] 47   [application] 52
#     [Strategy]   274   [strategy]      4
#     [Motivation]   8   [motivation]   13
#     [Technology]   1   [technology]   24
#     [business]    11   <- no capitalised "Business" row ever existed
#
# The code disagrees with itself the same way: ~70 call sites spell the layer
# in lower case and ~30 capitalise it. So the nine sites querying "Business"
# returned nothing at all, and the thirteen querying "strategy" saw 4 rows out
# of 278. Writers disagreed too — strategy_layer.py writes "Strategy".
#
# Normalising the stored data alone would have *created* a second outage: every
# capitalised comparison would go from partly-working to never-working. So the
# canonical form is enforced at three points, and each one only ever widens
# what matches:
#
#   1. `process_bind_param` canonicalises the value SQLAlchemy sends to the
#      database. That covers INSERT/UPDATE values *and* the literal in
#      `layer == "Business"`, `filter_by(layer="Strategy")` and
#      `layer.in_([...])` — so a query keeps its own spelling in the source and
#      still matches canonical rows. This is what lets ~100 existing call sites
#      stay exactly as they are, including modules this change does not touch.
#   2. `@validates` canonicalises assignment, so the in-Python value is correct
#      before a flush ever happens.
#   3. `process_result_value` hands back a str that compares case-insensitively,
#      so `element.layer == "Strategy"` stays true for a row stored as
#      "strategy". Without this, normalising storage would break the ~30
#      capitalised in-memory comparisons.
#
# Lower case is canonical because it is the majority convention in this
# codebase (~70 vs ~30 in Python, 44 vs 30 in templates/JS) and it matches the
# sibling `scope` column.
#
# Existing rows are repaired by `flask backfill-archimate-layer-casing`
# (app/commands/backfill_archimate_layer_casing.py). Until that has run, step 1
# already makes both conventions agree on every newly written row.


class _LayerName(str):
    """An ArchiMate layer name that compares equal regardless of casing.

    A plain ``str`` subclass, so templates render it, ``json.dumps`` serialises
    it and ``.lower()``/``.title()`` behave normally. Its hash is the hash of
    the canonical (lower-case) text it already holds, so dict and set behaviour
    is byte-identical to a plain lower-case ``str``. The only difference is
    that ``==`` is case-insensitive, which can turn a False into a True but
    never the reverse — no comparison that succeeds today can start failing.
    """

    __slots__ = ()

    def __eq__(self, other):
        if isinstance(other, str):
            return str.__eq__(str.lower(self), other.lower())
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        return str.__hash__(str.lower(self))


def canonical_archimate_layer(value):
    """Return the canonical spelling of an ArchiMate layer name.

    Returns a :class:`_LayerName` so a value read back from the database still
    compares equal to either convention. Non-strings (and ``None``) pass
    through untouched — this normalises casing, it does not invent data.
    """
    if not isinstance(value, str):
        return value
    canonical = ArchiMateLayer.normalize(value)
    return _LayerName(canonical) if isinstance(canonical, str) else canonical


class _ArchiMateLayerType(types.TypeDecorator):
    """VARCHAR column whose values *and bind parameters* are canonicalised.

    Canonicalising the bind parameter is the load-bearing part: it makes
    ``layer == "Business"`` and ``layer == "business"`` emit identical SQL, so
    both spellings match the same rows without editing either call site.
    """

    impl = types.String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not isinstance(value, str):
            return value
        # Deliberately a plain str — the DBAPI should never see a subclass.
        canonical = canonical_archimate_layer(value)
        return str(canonical) if canonical is not None else canonical

    def process_result_value(self, value, dialect):
        return canonical_archimate_layer(value)


_FAST_INIT = os.getenv("APP_FAST_INIT", "0") == "1"


if _FAST_INIT:
    # In fast-init/test contexts, a minimal ArchiMate core subset is defined in
    # app.models.archimate_core. If this monolithic module gets imported anyway
    # (via indirect imports), alias the canonical fast-init definitions to avoid
    # registering duplicate mapped classes in the same declarative registry.
    from .archimate_core import (  # noqa: F401
        ArchiMateElement,
        ArchiMateRelationship,
        ArchitectureModel,
    )

else:

    class ArchitectureModel(TenantMixin, db.Model):
        __tablename__ = "architecture_models"

        # In fast-init/test contexts we may define a lightweight ArchitectureModel
        # in app.models.archimate_core. Allow the table definition to be reused.
        __table_args__ = {"extend_existing": True}

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(100), nullable=False)
        version = db.Column(db.String(20))
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
        model_data = db.Column(db.Text)  # store raw model or filepath
        is_default = db.Column(db.Boolean, default=False, nullable=True)
        # Solution scoping (for journey-created architectures)  # migration-exempt
        solution_id = db.Column(
            db.Integer,
            db.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        )
        technology_stack_id = db.Column(db.Integer, db.ForeignKey("technology_stacks.id"))
        compliance_framework_id = db.Column(
            db.Integer, db.ForeignKey("regulatory_frameworks.id"), nullable=True
        )

        user = db.relationship("User", backref="architecture_models")
        technology_stack = db.relationship("TechnologyStack", backref="architecture_models")
        compliance_framework = db.relationship(
            "RegulatoryFramework", backref="architectures", foreign_keys=[compliance_framework_id]
        )

        def generate_outputs(self):
            """Generate requirements and code artifacts for this architecture model.

            Not implemented. `apply_transformation` was never written ("implement
            this function elsewhere"), so this method could only ever raise
            NameError — a confusing failure that reads like a missing import rather
            than unfinished work. It has no call sites anywhere in the codebase.
            Raising explicitly states the real situation, and keeps the placeholder
            visible instead of silently deleting an intended feature.
            """
            raise NotImplementedError(
                "ArchitectureModel.generate_outputs requires apply_transformation(), "
                "which has never been implemented."
            )

        def __repr__(self):
            return f"<ArchitectureModel {self.name} v{self.version}>"

    class ArchiMateElement(TenantMixin, db.Model):
        __tablename__ = "archimate_elements"

        # In fast-init/test contexts we may define a lightweight ArchiMateElement
        # in app.models.archimate_core. Allow the table definition to be reused.
        __table_args__ = {"extend_existing": True}

        id = db.Column(db.Integer, primary_key=True)
        # Width 500 per migrations/versions/20260926_widen_element_name.py
        # (ADR 0002 expand step).
        name = db.Column(db.String(500), nullable=False)
        type = db.Column(db.String(50), index=True)
        # VARCHAR(30) on the database side, exactly as before — see
        # _ArchiMateLayerType above for why the casing is mediated here.
        layer = db.Column(_ArchiMateLayerType(30), index=True)
        description = db.Column(db.Text, nullable=True)

        # Scope for Enterprise vs Application level filtering
        # Values: 'enterprise', 'application', 'cross-cutting'
        scope = db.Column(db.String(30), nullable=True, default="enterprise", index=True)

        # ArchiMate 3.2 specific attributes
        documentation = db.Column(db.Text, nullable=True)  # Detailed documentation
        properties = db.Column(db.Text, nullable=True)  # JSON string for custom properties

        # Motivation layer specific attributes
        stakeholder_interest = db.Column(db.Text, nullable=True)  # For Stakeholder elements
        priority = db.Column(db.String(20), nullable=True)  # High, Medium, Low
        status = db.Column(db.String(30), nullable=True)  # Proposed, Approved, Implemented, etc.

        # Strategic Architecture Fields (Phase 3 Enhancement)
        strategic_alignment_score = db.Column(db.Float)  # 0 - 100 score
        business_value_score = db.Column(db.Float)  # 0 - 100 score
        technical_risk_score = db.Column(db.Float)  # 0 - 100 score
        architectural_debt_score = db.Column(db.Float)  # 0 - 100 score

        # Capability Interdependency Tracking
        dependency_level = db.Column(db.String(20))  # low, medium, high, critical
        upstream_dependencies = db.Column(db.Text)  # JSON array of element IDs
        downstream_dependents = db.Column(db.Text)  # JSON array of element IDs
        critical_path_member = db.Column(db.Boolean, default=False)

        # Quality Attributes for Software Architects
        performance_metrics = db.Column(db.Text)  # JSON dict: latency, throughput, etc.
        scalability_metrics = db.Column(db.Text)  # JSON dict: horizontal, vertical, etc.
        reliability_metrics = db.Column(db.Text)  # JSON dict: MTBF, MTTR, availability
        security_posture = db.Column(db.Text)  # JSON dict: security controls, threats

        # Reference Architecture & Patterns
        architectural_pattern = db.Column(db.String(100))  # MVC, Microservices, etc.
        design_pattern_tags = db.Column(db.Text)  # JSON array of patterns
        reference_implementation_url = db.Column(db.String(500))
        architectural_decision_records = db.Column(db.Text)  # JSON array of ADRs

        # Cost & Investment Modeling
        estimated_cost = db.Column(db.Float)
        roi_score = db.Column(db.Float)
        tco_annual = db.Column(db.Float)  # Total Cost of Ownership
        investment_category = db.Column(db.String(50))  # run, grow, transform

        # Governance & Compliance
        architecture_review_status = db.Column(db.String(30))  # pending, approved, rejected
        compliance_frameworks = db.Column(db.Text)  # JSON array of frameworks
        governance_standards = db.Column(db.Text)  # JSON array of standards
        last_reviewed_date = db.Column(db.DateTime)

        reviewer_notes = db.Column(db.Text)

        # Soft delete (fix/qa-register-100): the bulk-delete path soft-deletes
        # its ApplicationComponent (see deleted_at above on that model) so the
        # delete is recoverable, but originally left the ArchiMate mirror
        # element untouched — reintroducing finding C-02 (orphaned element
        # visible in the composer palette, relationship matrix, OEF export,
        # AI context) on that one path. Mirroring deleted_at/deleted_by here,
        # nullable per ADR-0002 (reconcile-schema is add-only/nullable), lets
        # the mirror be hidden the same way ApplicationComponent already is —
        # via the unconditional do_orm_execute filter in
        # app/middleware/tenant_isolation.py — and restored by clearing both
        # columns together instead of recreating the element from scratch.
        deleted_at = db.Column(db.DateTime, nullable=True)
        deleted_by = db.Column(db.Integer, nullable=True)

        # Model history: the interval this row's
        # current state has held, and when it was recorded. Nullable per
        # ADR-0002 (reconcile-schema is add-only/nullable) -- valid_from and
        # recorded_at are backfilled for existing rows (from the audit log
        # where an entry exists, NULL/"unknown" otherwise) by
        # backfill-entity-history; new rows are stamped by the same trigger
        # that writes entity_history (app/models/entity_history.py). A row
        # with valid_to set has been superseded by a later version and is no
        # longer the current state, which superseded_at also records, once,
        # for the version that ended it; last_confirmed is the latest time
        # any read or re-import observed this row unchanged, letting a very
        # old, never-touched row be told apart from one that simply never
        # changed and was reconfirmed recently.
        valid_from = db.Column(db.DateTime, nullable=True)
        valid_to = db.Column(db.DateTime, nullable=True)
        recorded_at = db.Column(db.DateTime, nullable=True)
        superseded_at = db.Column(db.DateTime, nullable=True)
        last_confirmed = db.Column(db.DateTime, nullable=True)

        # Relationship tracking
        parent_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True)

        architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
        application_component_id = db.Column(
            db.BigInteger,
            db.ForeignKey("application_components.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )

        # Template Pattern: Track lineage from vendor product templates
        template_element_id = db.Column(
            db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True, index=True
        )
        source_product_id = db.Column(
            db.Integer,
            db.ForeignKey(
                "vendor_products.id", use_alter=True, name="fk_archimate_elements_source_product_id"
            ),
            nullable=True,
            index=True,
        )
        is_customized = db.Column(db.Boolean, default=False)
        customization_notes = db.Column(db.Text, nullable=True)

        # ACM Domain-Driven Architecture metadata
        acm_domain = db.Column(db.String(10), nullable=True, index=True)
        is_baseline = db.Column(db.Boolean, default=False)
        acm_source = db.Column(db.String(20), nullable=True)
        overlay_code = db.Column(db.String(32), nullable=True)
        acm_properties = db.Column(db.JSON, default=dict)

        # TOGAF Building Block classification (Section 37)
        # Values: 'ABB' (Architecture Building Block), 'SBB' (Solution Building Block), or None
        building_block_type = db.Column(db.String(10), nullable=True, index=True)

        # TOGAF Plateau classification for transition architectures
        # Values: 'Baseline', 'Target', 'Transition', or None
        # Column name kept as 'plateau' in DB to match raw SQL queries in archimate_crud routes.
        # Python attribute uses togaf_plateau to avoid conflict with Plateau.archimate_element backref.
        togaf_plateau = db.Column("plateau", db.String(20), nullable=True, index=True)

        # Custom tagged-value properties
        custom_properties = db.Column(db.JSON, nullable=True, default=dict)

        architecture = db.relationship("ArchitectureModel", backref="archimate_elements")
        parent = db.relationship(
            "ArchiMateElement",
            remote_side="ArchiMateElement.id",
            foreign_keys=[parent_id],
            backref="children",
        )
        template_source = db.relationship(
            "ArchiMateElement",
            remote_side="ArchiMateElement.id",
            foreign_keys=[template_element_id],
            backref="template_instances",
        )
        application_component = db.relationship(
            "ApplicationComponent",
            foreign_keys=[application_component_id],
            backref="archimate_elements",
        )
        source_product = db.relationship(
            "VendorProduct", foreign_keys=[source_product_id], backref="template_applications"
        )
        requirements = db.relationship(
            "Requirement",
            foreign_keys="Requirement.archimate_element_id",
            back_populates="archimate_element",
            lazy="dynamic",
        )
        # Use back_populates to avoid creating a backref name that conflicts with
        # the explicit `source_element` attribute defined on CodeArtifact.
        # Use fully qualified path to avoid "Multiple classes found" error
        artifacts = db.relationship("CodeArtifact", back_populates="source_element", lazy="dynamic")

        # Semantic EA Intelligence Relationships (imported from vendor_organization)
        vendor_products = db.relationship(
            "VendorProduct",
            secondary="application_vendor_products",
            back_populates="template_applications",
        )

        # System Interface relationships
        system_provided_interfaces = db.relationship(
            "SystemInterface",
            foreign_keys="SystemInterface.source_system_id",
            back_populates="source_system",
        )
        system_consumed_interfaces = db.relationship(
            "SystemInterface",
            foreign_keys="SystemInterface.target_system_id",
            back_populates="target_system",
        )

        # UML relationships
        uml_elements = db.relationship(
            "UMLElement", back_populates="archimate_element", lazy="dynamic"
        )

        @validates("layer")
        def _canonicalise_layer(self, key, value):
            """Canonicalise the layer at assignment time.

            The column type already canonicalises what reaches the database, but
            an unflushed object would otherwise report back whatever casing the
            caller passed — so ``el = ArchiMateElement(layer="Strategy")`` and a
            later ``el.layer == "strategy"`` would disagree until the next
            flush. Doing it here as well means the in-memory object and the row
            never differ.
            """
            return canonical_archimate_layer(value)

        def __repr__(self):
            return f"<ArchiMateElement {self.name} ({self.type})>"

        def to_dict(self):
            """JSON-safe dict of all columns. Several CRUD routes call
            element.to_dict() (edit/view/search element); without it they 500
            with AttributeError 'ArchiMateElement has no attribute to_dict'.

            Reads each column's value through the ORM-mapped attribute name
            (mapper.get_property_by_column), not col.name, because togaf_plateau
            is declared with an explicit DB column name ("plateau") that
            collides with the unrelated Plateau.archimate_element backref of
            the same name on this class. getattr(self, col.name) would read
            that backref (a list of Plateau rows, not JSON-serializable) instead
            of the scalar column - dropping the real classification value at
            best, raising TypeError from json.dump at worst, for any element
            linked to a Plateau row.
            """
            from datetime import date, datetime
            from decimal import Decimal

            from sqlalchemy import inspect as sa_inspect

            out = {}
            mapper = sa_inspect(self.__class__)
            for col in self.__table__.columns:
                attr_name = mapper.get_property_by_column(col).key
                val = getattr(self, attr_name)
                if isinstance(val, (datetime, date)):
                    val = val.isoformat()
                elif isinstance(val, Decimal):
                    val = float(val)
                out[col.name] = val
            return out

    class ArchiMateRelationship(TenantMixin, db.Model):
        __tablename__ = "archimate_relationships"

        # In fast-init/test contexts we may define a lightweight ArchiMateRelationship
        # in app.models.archimate_core. Allow the table definition to be reused.
        __table_args__ = {"extend_existing": True}

        id = db.Column(db.Integer, primary_key=True)
        type = db.Column(db.String(30), index=True)
        architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"), index=True)
        source_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), index=True)
        target_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), index=True)

        # BUG-CMP-002: Relationship metadata — persists properties across diagrams
        description = db.Column(db.Text, nullable=True)
        access_mode = db.Column(db.String(20), nullable=True)
        # Which of Create/Read/Update/Delete an access relationship performs,
        # as the letters in that order ("CU", "R", "CRUD"). ArchiMate's own
        # access_mode above only says read/write; this is the finer record a
        # data entity's CRUD matrix reads, with access_mode kept consistent
        # with it. NULL means no CRUD detail was recorded.
        crud_operations = db.Column(db.String(4), nullable=True)
        flow_label = db.Column(db.String(200), nullable=True)
        custom_label = db.Column(db.String(200), nullable=True)
        created_by_id = db.Column(db.Integer, nullable=True)
        created_at = db.Column(db.DateTime, nullable=True, default=db.func.now())
        updated_at = db.Column(db.DateTime, nullable=True, default=db.func.now(), onupdate=db.func.now())

        # GAP-INT-001: Structured connection specification
        connection_spec = db.Column(db.JSON, nullable=True, default=dict)

        # How this relationship came to exist, when it was not drawn explicitly:
        # "notation" (read from arrowhead and stroke), "stroke-stripped-label"
        # (inferred from a line's label because the export discarded the stroke),
        # "nesting" (one shape drawn inside another). NULL means stated outright,
        # by a person or an explicit connector, and is what every pre-existing
        # row correctly reads.
        #
        # Element provenance already survived in custom_properties; relationship
        # provenance was computed on import and then dropped on the way into the
        # database, which left the import review queue with nothing to triage.
        derived_from = db.Column(db.String(40), nullable=True, index=True)

        # Model history: same columns and rationale as
        # ArchiMateElement's above.
        valid_from = db.Column(db.DateTime, nullable=True)
        valid_to = db.Column(db.DateTime, nullable=True)
        recorded_at = db.Column(db.DateTime, nullable=True)
        superseded_at = db.Column(db.DateTime, nullable=True)
        last_confirmed = db.Column(db.DateTime, nullable=True)

        # When a person confirmed an inferred relationship. NULL with a
        # derived_from set means "still to be looked at" - that pair is the whole
        # import review queue. Kept as a timestamp rather than a boolean so the
        # queue can show what was reviewed recently and by implication what has
        # been sitting there.
        reviewed_at = db.Column(db.DateTime, nullable=True)

        # Step number for the Composer's Sequence View (a lifeline/message render
        # of the ArchiMate elements and relationships already on the canvas — no
        # separate sequence-diagram store; see ADR 0008). NULL means "not
        # explicitly ordered yet" and the Sequence View falls back to created_at,
        # so every pre-existing relationship still renders instead of being
        # dropped for lacking a value reconcile-schema cannot backfill.
        sequence_order = db.Column(db.Integer, nullable=True)

        architecture = db.relationship("ArchitectureModel", backref="archimate_relationships")
        source = db.relationship(
            "ArchiMateElement", foreign_keys=[source_id], backref="outgoing_relationships"
        )
        target = db.relationship(
            "ArchiMateElement", foreign_keys=[target_id], backref="incoming_relationships"
        )

        def __repr__(self):
            return f"<ArchiMateRelationship {self.source_id} -> {self.target_id}>"

        def to_dict(self):
            """JSON-safe dict of all columns. Mirrors ArchiMateElement.to_dict()
            immediately above (including reading each column's value through
            its ORM-mapped attribute name rather than assuming it equals the DB
            column name - no current column on this class needs that, but a
            future one might). Several CRUD routes call relationship.to_dict()
            (e.g. update_relationship); without it they 500 with AttributeError
            'ArchiMateRelationship has no attribute to_dict'."""
            from datetime import date, datetime
            from decimal import Decimal

            from sqlalchemy import inspect as sa_inspect

            out = {}
            mapper = sa_inspect(self.__class__)
            for col in self.__table__.columns:
                attr_name = mapper.get_property_by_column(col).key
                val = getattr(self, attr_name)
                if isinstance(val, (datetime, date)):
                    val = val.isoformat()
                elif isinstance(val, Decimal):
                    val = float(val)
                out[col.name] = val
            return out


class WorkflowInstanceArchiMateElement(db.Model):
    """ORM mapping for the workflow_instance_archimate_elements junction table.

    Links ea_workflow_instances to archimate_elements with ADM phase tagging
    and element role classification.  Replaces raw db.text() inserts.
    """

    __tablename__ = "workflow_instance_archimate_elements"
    __table_args__ = (
        db.UniqueConstraint(
            "instance_id", "element_id", "element_role",
            name="uq_wiame_instance_element_role",
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    instance_id = db.Column(
        db.Integer,
        db.ForeignKey("ea_workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    element_id = db.Column(
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    element_role = db.Column(db.String(50), nullable=False)
    adm_phase = db.Column(db.String(10), nullable=False)
    step_id = db.Column(db.String(100), nullable=True)

    def __repr__(self):
        return (
            f"<WorkflowInstanceArchiMateElement instance={self.instance_id}"
            f" element={self.element_id} role={self.element_role}>"
        )


class Requirement(TenantMixin, db.Model):
    __tablename__ = "requirements"

    # In fast-init/test contexts we may define a lightweight Requirement in
    # app.models.requirements. Allow the table definition to be reused.
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    type = db.Column(db.String(50), nullable=True)  # functional, non-functional
    priority = db.Column(db.String(20), nullable=True)  # low, medium, high
    category = db.Column(db.String(50))
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    application_component_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )  # Optional: link to specific application
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id")
    )  # Link to ArchiMate Requirement element (Basecoat pattern)
    source_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id")
    )  # DEPRECATED: Use archimate_element_id instead  # dead-code-ok
    jira_issue_id = db.Column(db.Integer, db.ForeignKey("jira_issues.id"))  # Link to JIRA

    # ArchiMate 3.2 Motivation Layer relationships
    parent_requirement_id = db.Column(db.Integer, db.ForeignKey("requirements.id"), nullable=True)
    stakeholder_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True
    )  # Stakeholder element
    driver_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True
    )  # Driver element
    goal_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True
    )  # Goal element

    # Additional requirement metadata
    rationale = db.Column(db.Text, nullable=True)  # Why this requirement exists
    verification_method = db.Column(
        db.String(50), nullable=True
    )  # inspection, analysis, test, demonstration
    compliance_status = db.Column(
        db.String(30), default="draft"
    )  # draft, reviewed, approved, implemented, verified

    # User Story / Epic fields (TPM-003)
    epic_id = db.Column(db.Integer, db.ForeignKey("requirements.id"), nullable=True)
    story_points = db.Column(db.Integer, nullable=True)
    dod_complete = db.Column(db.Boolean, default=False, nullable=False, server_default='false')
    requirement_type = db.Column(db.String(32), default='requirement')  # epic, story, sub_task, requirement

    # Product roadmap horizon (TPM-011): now / next / later
    horizon = db.Column(db.String(16), nullable=True)

    # MoSCoW prioritisation (TPM-006)
    moscow_priority = db.Column(db.String(16), nullable=True)  # MUST/SHOULD/COULD/WONT

    # WSJF components — SAFe: score = CoD / Job Size (TPM-006)
    business_value = db.Column(db.Integer, default=1)
    time_criticality = db.Column(db.Integer, default=1)
    risk_reduction = db.Column(db.Integer, default=1)
    job_size = db.Column(db.Integer, default=1)

    # RICE components (TPM-006)
    reach = db.Column(db.Integer, default=0)
    impact = db.Column(db.Integer, default=1)   # 1=minimal … 5=massive
    confidence = db.Column(db.Integer, default=100)  # percentage

    @property
    def wsjf_score(self):
        cod = (self.business_value or 1) + (self.time_criticality or 1) + (self.risk_reduction or 1)
        return round(cod / max(self.job_size or 1, 1), 2)

    @property
    def rice_score(self):
        sp = self.story_points or 1
        return round(
            ((self.reach or 0) * (self.impact or 1) * ((self.confidence or 100) / 100))
            / max(sp, 1),
            2,
        )

    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationships
    architecture = db.relationship("ArchitectureModel", backref="requirement_models")
    application_component = db.relationship(
        "ApplicationComponent",
        foreign_keys=[application_component_id],
        backref="direct_requirements",
    )
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], back_populates="requirements"
    )

    # Application relationships (via junction table for many-to-many)
    implementing_applications = db.relationship(
        "ApplicationComponent",
        secondary="application_requirement_mapping",
        back_populates="requirements",
        overlaps="requirement_mappings,app_requirement_mappings,application_component,requirement",
    )
    source_element = db.relationship("ArchiMateElement", foreign_keys=[source_element_id])
    parent_requirement = db.relationship(
        "Requirement",
        remote_side="Requirement.id",
        backref="child_requirements",
        foreign_keys=[parent_requirement_id],
    )
    stakeholder = db.relationship("ArchiMateElement", foreign_keys=[stakeholder_id])
    driver = db.relationship("ArchiMateElement", foreign_keys=[driver_id])
    goal = db.relationship("ArchiMateElement", foreign_keys=[goal_id])
    jira_issue = db.relationship("JiraIssue", back_populates="requirements")

    # Testing relationships
    test_cases = db.relationship("TestCase", back_populates="requirement", lazy="dynamic")
    acceptance_criteria = db.relationship(
        "AcceptanceCriteria", back_populates="requirement", lazy="dynamic"
    )

    # Compliance relationships
    compliance_checks = db.relationship(
        "ComplianceCheck", back_populates="requirement", lazy="dynamic"
    )

    # Application mapping relationships
    application_mappings = db.relationship(
        "ApplicationRequirementMapping",
        back_populates="requirement",
        lazy="dynamic",
        overlaps="implementing_applications,requirements,app_requirement_mappings",
    )

    @property
    def display_title(self):
        """Return title if available, otherwise category."""
        return (
            self.title
            if self.title
            else (self.category if self.category else "Untitled Requirement")
        )

    @property
    def status(self):
        """
        Get requirement status from linked ArchiMate element (Basecoat pattern).
        Returns status from the ArchiMate Requirement element if available.
        """
        # Try archimate_element_id first (new Basecoat pattern)
        element_id = self.archimate_element_id or self.source_element_id
        if element_id:
            from app.models import ArchiMateElement

            archimate_elem = db.session.get(ArchiMateElement, element_id)
            if archimate_elem and archimate_elem.status:
                return archimate_elem.status
        return "pending"

    def __repr__(self):
        return f"<Requirement {self.title or self.category}: {self.description[:30] if self.description else 'N/A'}>"


class CodeArtifact(db.Model):
    __tablename__ = "code_artifacts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    type = db.Column(db.String(50))
    language = db.Column(db.String(30))
    content = db.Column(db.Text, nullable=True)
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    source_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    requirement_id = db.Column(db.Integer, db.ForeignKey("requirements.id"))
    pipeline_id = db.Column(db.Integer, db.ForeignKey("generation_pipelines.id"))
    artifact_metadata = db.Column(db.JSON, nullable=True)

    architecture = db.relationship("ArchitectureModel", backref="code_artifact_models")
    requirement = db.relationship("Requirement", backref="implemented_by")
    # Match the relationship with back_populates on ArchiMateElement.artifacts
    source_element = db.relationship("ArchiMateElement", back_populates="artifacts")
    pipeline = db.relationship("GenerationPipeline", backref="code_artifacts")

    # Testing relationships
    test_cases = db.relationship("TestCase", back_populates="code_artifact", lazy="dynamic")

    # UML relationships
    uml_elements = db.relationship("UMLElement", back_populates="code_artifact", lazy="dynamic")

    # Compliance and security relationships
    compliance_checks = db.relationship(
        "ComplianceCheck", back_populates="code_artifact", lazy="dynamic"
    )
    security_scans = db.relationship("SecurityScan", back_populates="code_artifact", lazy="dynamic")

    def __repr__(self):
        return f"<CodeArtifact {self.name} ({self.language})>"


class JiraProject(db.Model):
    """Maps to the jira_projects table — referenced by JiraIssue and GenerationPipeline."""

    __tablename__ = "jira_projects"

    id = db.Column(db.Integer, primary_key=True)
    jira_key = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200))
    jira_id = db.Column(db.String(50), unique=True)
    project_type = db.Column(db.String(50))
    webhook_url = db.Column(db.String(500))
    webhook_secret = db.Column(db.String(200))
    last_sync_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=db.func.now())
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))

    # Relationships
    issues = db.relationship("JiraIssue", backref="project", lazy="dynamic")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<JiraProject {self.jira_key}: {self.name}>"


class JiraIssue(db.Model):
    __tablename__ = "jira_issues"

    id = db.Column(db.Integer, primary_key=True)
    jira_key = db.Column(db.String(20), unique=True, nullable=False, index=True)
    jira_id = db.Column(db.String(50), unique=True)
    project_id = db.Column(db.Integer, db.ForeignKey("jira_projects.id"), nullable=False)
    issue_type = db.Column(db.String(50))  # Epic, Story, Task, Bug
    summary = db.Column(db.String(500))
    description = db.Column(db.Text)
    status = db.Column(db.String(50))  # To Do, In Progress, Done
    priority = db.Column(db.String(20))  # Highest, High, Medium, Low, Lowest
    assignee = db.Column(db.String(100))
    reporter = db.Column(db.String(100))
    parent_issue_id = db.Column(db.Integer, db.ForeignKey("jira_issues.id"))
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationships
    architecture = db.relationship("ArchitectureModel", backref="jira_issue_models")
    parent_issue = db.relationship("JiraIssue", remote_side="JiraIssue.id", backref="sub_issues")
    requirements = db.relationship("Requirement", back_populates="jira_issue")
    uml_elements = db.relationship("UMLElement", back_populates="jira_issue", lazy="dynamic")

    def __repr__(self):
        return f"<JiraIssue {self.jira_key}: {self.summary[:30]}>"


# ============================================================================
# Enhanced Requirements Layer
# ============================================================================


class AcceptanceCriteria(db.Model):
    __tablename__ = "acceptance_criteria"

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    requirement_id = db.Column(db.Integer, db.ForeignKey("requirements.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending, passed, failed
    order = db.Column(db.Integer)  # Display order
    test_case_id = db.Column(db.Integer, db.ForeignKey("test_cases.id"))
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationships
    requirement = db.relationship("Requirement", back_populates="acceptance_criteria")

    def __repr__(self):
        return f"<AcceptanceCriteria {self.id}: {self.description[:30]}>"


# ============================================================================
# Testing Layer
# ============================================================================


class TestCase(db.Model):
    __tablename__ = "test_cases"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    test_type = db.Column(db.String(50))  # unit, integration, e2e, acceptance
    test_framework = db.Column(db.String(50))  # pytest, unittest, jest, etc.
    test_code = db.Column(db.Text)
    requirement_id = db.Column(db.Integer, db.ForeignKey("requirements.id"))
    code_artifact_id = db.Column(db.Integer, db.ForeignKey("code_artifacts.id"))
    status = db.Column(db.String(20), default="draft")  # draft, active, deprecated
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationships
    requirement = db.relationship("Requirement", back_populates="test_cases")
    # Use fully qualified path to avoid "Multiple classes found" error
    code_artifact = db.relationship("CodeArtifact", back_populates="test_cases")
    acceptance_criteria = db.relationship("AcceptanceCriteria", backref="test_case")

    def __repr__(self):
        return f"<TestCase {self.name} ({self.test_type})>"


class UMLModel(db.Model):
    """UML diagram model — stub required for FK resolution in autogenerate."""

    __tablename__ = "uml_models"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200))
    diagram_type = db.Column(db.String(50))
    description = db.Column(db.Text)
    architecture_id = db.Column(db.Integer)
    plantuml_source = db.Column(db.Text)
    mermaid_source = db.Column(db.Text)
    svg_output = db.Column(db.Text)
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)

    elements = db.relationship("UMLElement", backref="uml_model", lazy="dynamic")


class UMLElement(db.Model):
    __tablename__ = "uml_elements"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    element_type = db.Column(db.String(50))  # class, interface, actor, component, node
    stereotype = db.Column(db.String(50))  # <<controller>>, <<entity>>, <<boundary>>
    visibility = db.Column(db.String(20))  # public, private, protected, package
    is_abstract = db.Column(db.Boolean, default=False)
    uml_model_id = db.Column(db.Integer, db.ForeignKey("uml_models.id"), nullable=False)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    code_artifact_id = db.Column(db.Integer, db.ForeignKey("code_artifacts.id"))
    jira_issue_id = db.Column(db.Integer, db.ForeignKey("jira_issues.id"))
    created_at = db.Column(db.DateTime, default=db.func.now())

    # Relationships
    archimate_element = db.relationship("ArchiMateElement", back_populates="uml_elements")
    code_artifact = db.relationship("CodeArtifact", back_populates="uml_elements")
    jira_issue = db.relationship(
        "JiraIssue", back_populates="uml_elements", foreign_keys=[jira_issue_id]
    )
    attributes = db.relationship("UMLAttribute", backref="uml_element", lazy="dynamic")
    methods = db.relationship("UMLMethod", backref="uml_element", lazy="dynamic")

    def __repr__(self):
        return f"<UMLElement {self.name} ({self.element_type})>"


class UMLAttribute(db.Model):
    __tablename__ = "uml_attributes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    data_type = db.Column(db.String(50))
    visibility = db.Column(db.String(20))  # public, private, protected
    default_value = db.Column(db.String(100))
    is_static = db.Column(db.Boolean, default=False)
    uml_element_id = db.Column(db.Integer, db.ForeignKey("uml_elements.id"), nullable=False)

    def __repr__(self):
        return f"<UMLAttribute {self.name}: {self.data_type}>"


class UMLMethod(db.Model):
    __tablename__ = "uml_methods"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    return_type = db.Column(db.String(50))
    parameters = db.Column(db.Text)  # JSON string of parameters
    visibility = db.Column(db.String(20))  # public, private, protected
    is_static = db.Column(db.Boolean, default=False)
    is_abstract = db.Column(db.Boolean, default=False)
    uml_element_id = db.Column(db.Integer, db.ForeignKey("uml_elements.id"), nullable=False)

    def __repr__(self):
        return f"<UMLMethod {self.name}(): {self.return_type}>"


# ============================================================================
# Workflow & Orchestration Layer
# ============================================================================


class GenerationPipeline(db.Model):
    __tablename__ = "generation_pipelines"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))
    jira_project_id = db.Column(db.Integer, db.ForeignKey("jira_projects.id"))
    selected_issue_keys = db.Column(
        db.Text
    )  # Comma-separated JIRA issue keys (e.g., "PROJ - 123,PROJ - 456")

    # Platform targeting (optional)
    platform_type_id = db.Column(db.Integer, db.ForeignKey("platform_types.id"))
    platform_config_id = db.Column(db.Integer, db.ForeignKey("platform_configurations.id"))

    # Capability-based pipeline support
    use_capabilities = db.Column(
        db.Boolean, default=False
    )  # True = capability-based, False = JIRA-based
    selected_capability_ids = db.Column(db.Text)  # Comma-separated capability IDs
    context_data = db.Column(
        db.JSON
    )  # JSON field for storing pipeline context and intermediate data

    # Vendor template integration (hybrid discovery)
    vendor_template_id = db.Column(db.Integer, db.ForeignKey("vendor_stack_templates.id"))

    status = db.Column(
        db.String(20), default="pending"
    )  # pending, running, completed, failed, cancelled
    trigger_type = db.Column(db.String(50))  # manual, jira_webhook, scheduled
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=db.func.now())
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    metadata_store = db.Column("metadata", MutableDict.as_mutable(db.JSON), default=dict)

    # Relationships
    architecture = db.relationship("ArchitectureModel", backref="generation_pipeline_models")
    platform_type = db.relationship("PlatformType", overlaps="target_platform")
    platform_config = db.relationship("PlatformConfiguration")
    created_by = db.relationship("User", backref="created_pipelines")
    stages = db.relationship(
        "PipelineStage",
        backref="pipeline",
        lazy="dynamic",
        order_by="PipelineStage.order",
        cascade="all, delete-orphan",
    )
    vendor_template = db.relationship("VendorStackTemplate", backref="pipelines")

    # Compliance relationships
    compliance_checks = db.relationship(
        "ComplianceCheck", back_populates="pipeline", lazy="dynamic"
    )

    # Security relationships
    security_scans = db.relationship("SecurityScan", back_populates="pipeline", lazy="dynamic")

    # NOTE: ValidationGates accessed via stages: [gate for stage in self.stages for gate in stage.validation_gates]
    # ValidationGate belongs to PipelineStage, not directly to GenerationPipeline

    def __repr__(self):
        return f"<GenerationPipeline {self.name}: {self.status}>"

    def __getattribute__(self, name):
        if name == "metadata":
            value = super().__getattribute__("metadata_store")
            if value is None:
                super().__setattr__("metadata_store", {})
                value = super().__getattribute__("metadata_store")
            return value
        return super().__getattribute__(name)

    def __setattr__(self, name, value):
        if name == "metadata":
            super().__setattr__("metadata_store", value or {})
        else:
            super().__setattr__(name, value)


class PipelineStage(db.Model):
    __tablename__ = "pipeline_stages"

    id = db.Column(db.Integer, primary_key=True)
    pipeline_id = db.Column(db.Integer, db.ForeignKey("generation_pipelines.id"), nullable=False)
    stage_type = db.Column(
        db.String(50)
    )  # jira_sync, architecture_gen, requirements_gen, uml_gen, code_gen, test_gen
    order = db.Column(db.Integer, nullable=False)
    status = db.Column(
        db.String(20), default="pending"
    )  # pending, running, completed, failed, skipped
    error_message = db.Column(db.Text)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Integer)

    # Relationships
    llm_interactions = db.relationship(
        "LLMInteraction", backref="pipeline_stage", lazy="dynamic", cascade="all, delete-orphan"
    )
    def __repr__(self):
        return f"<PipelineStage {self.stage_type}: {self.status}>"


class LLMInteraction(db.Model):
    """Not TenantMixin-scoped by design: platform-admin cost views read this
    table across every organisation. Tenant-facing reads (budget checks, an
    organisation's own cost figures) must filter on organization_id explicitly
    -- see LLMCostTracker. Consequence: pre-existing rows and any interaction
    recorded outside a request context have organization_id NULL and count
    towards no organisation's budget.
    """

    __tablename__ = "llm_interactions"

    id = db.Column(db.Integer, primary_key=True)
    pipeline_stage_id = db.Column(db.Integer, db.ForeignKey("pipeline_stages.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)  # For budget tracking
    model_name = db.Column(db.String(50))  # gpt - 4, claude - 3 - opus, etc.
    provider = db.Column(db.String(50))  # openai, anthropic, azure
    prompt = db.Column(db.Text)
    response = db.Column(db.Text)
    token_count_input = db.Column(db.Integer)
    token_count_output = db.Column(db.Integer)
    cost = db.Column(db.Numeric(10, 4))
    latency_ms = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=db.func.now())

    # Gateway fields on llm_interactions for provider register
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    prompt_version = db.Column(
        db.String(32), nullable=True,
        comment="Semver-style version string, e.g. v1.3; references LLMPromptVersion.version",
    )
    retention_setting = db.Column(
        db.String(50), nullable=True,
        comment="Data retention policy: forever, 30d, 90d, 1y",
    )

    def __repr__(self):
        input_tokens = self.token_count_input or 0
        output_tokens = self.token_count_output or 0
        return f"<LLMInteraction {self.model_name}: {input_tokens + output_tokens} tokens>"


class APISettings(TenantMixin, db.Model):
    """Store API keys and configuration for LLM providers and JIRA.

    Multiple keys per provider are supported via key_label — e.g. two
    OpenAI keys labelled "Production" and "Dev" can coexist.

    Per-tenant: each organization has its own API keys. The ORM event
    listener in app.middleware.tenant_isolation auto-filters SELECTs
    and auto-sets organization_id on INSERTs.
    """

    __tablename__ = "api_settings"
    __table_args__ = (
        db.UniqueConstraint(
            "provider", "key_label", "organization_id",
            name="uq_api_settings_provider_label_org",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(50), nullable=False)  # openai, anthropic, azure, jira
    key_label = db.Column(db.String(100), nullable=False, default="")  # user-defined label; "" = default key
    api_key = db.Column(_EncryptedString(1000))  # stored Fernet-encrypted; see _EncryptedString above
    enabled = db.Column(db.Boolean, default=True)

    # LLM-specific fields
    default_model = db.Column(db.String(500))  # Supports comma-separated backup models (up to 5)
    max_tokens = db.Column(db.Integer, default=2000)
    temperature = db.Column(db.Float, default=0.7)

    # JIRA-specific fields
    jira_url = db.Column(db.String(255))  # e.g., https://your-domain.atlassian.net
    jira_email = db.Column(db.String(255))  # Email for JIRA authentication

    # Hugging Face specific fields
    hf_model_id = db.Column(db.String(255))  # Model ID from Hugging Face
    hf_endpoint_url = db.Column(db.String(500))  # Custom endpoint URL

    # Custom API specific fields
    custom_endpoint_url = db.Column(db.String(500))  # API endpoint URL
    custom_auth_method = db.Column(db.String(50), default="bearer")  # Auth method
    custom_headers = db.Column(db.Text)  # Additional headers in JSON

    # Common fields
    last_tested_at = db.Column(db.DateTime)
    test_status = db.Column(db.String(20))  # success, failed, pending
    test_message = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    updated_by = db.relationship("User", backref="api_settings_updates")

    def __repr__(self):
        label = f" ({self.key_label})" if self.key_label else ""
        return f"<APISettings {self.provider}{label}: {'Enabled' if self.enabled else 'Disabled'}>"

    # ------------------------------------------------------------------
    # Tenant-aware lookups with platform-level fallback
    # ------------------------------------------------------------------

    @classmethod
    def get_for_provider(cls, provider, enabled_only=True):
        """Return API settings for a provider, falling back to platform-level keys.

        1. Try the normal ORM query (auto-scoped to current org by tenant filter).
        2. If nothing found and we're in a tenant context, try platform-level
           keys (organization_id = 1, the default org) via raw SQL to bypass
           the ORM tenant filter.

        Returns a list of APISettings (may be empty).
        """
        from flask import g
        from sqlalchemy import text

        filters = {"provider": provider}
        if enabled_only:
            filters["enabled"] = True
        results = cls.query.filter_by(**filters).all()
        if results:
            return results

        # Fallback: if tenant-scoped query returned nothing, check default org
        # Use db.session.get() which hits the identity map / issues a GET by PK
        # and is not subject to the do_orm_execute SELECT filter.
        org_id = getattr(g, "current_org_id", None)
        if org_id and org_id != 1:
            try:
                sql = text(
                    "SELECT id FROM api_settings "  # raw-sql-ok: tenant fallback
                    "WHERE provider = :provider AND organization_id = 1"
                    + (" AND enabled = true" if enabled_only else "")
                )
                rows = db.session.execute(sql, {"provider": provider}).fetchall()  # tenant-exempt: intentional fallback to org_id=1
                if rows:
                    ids = [r[0] for r in rows]
                    return [obj for pk in ids if (obj := db.session.get(cls, pk)) is not None]
            except Exception:
                _key_log.debug("Platform-level API key fallback failed", exc_info=True)

        return []

    @classmethod
    def get_one_for_provider(cls, provider, enabled_only=True):
        """Convenience: return a single APISettings for a provider, or None."""
        results = cls.get_for_provider(provider, enabled_only=enabled_only)
        return results[0] if results else None

    def has_key(self):
        """Check if a non-empty API key is configured."""
        key = self.api_key  # TypeDecorator decrypts transparently
        return bool(key and key.strip())

    def get_masked_key(self):
        """Return masked API key for display (never expose more than first/last 4 chars)."""
        key = self.api_key
        if not key or not key.strip():
            return "Not configured"
        key = key.strip()
        if len(key) < 8:
            return "****"
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


# ============================================================================
# Enterprise Technology Stack & Platform Management
# ============================================================================


class TechnologyStack(db.Model):
    """Enterprise-approved technology stacks and platform configurations."""

    __tablename__ = "technology_stacks"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(100), nullable=False
    )  # "Enterprise Java Stack", "Python Microservices"
    description = db.Column(db.Text)
    platform = db.Column(db.String(50))  # aws, azure, gcp, on-prem, hybrid

    # Vendor organization link (for strategic vendor management)
    vendor_organization_id = db.Column(db.Integer, db.ForeignKey("vendor_organizations.id"))

    # Vendor template link (for ArchiMate reference architecture)
    vendor_template_id = db.Column(db.Integer, db.ForeignKey("vendor_stack_templates.id"))

    # Language & Framework
    primary_language = db.Column(db.String(50))  # java, python, csharp, typescript
    framework = db.Column(db.String(100))  # Spring Boot, FastAPI, .NET Core, NestJS
    framework_version = db.Column(db.String(20))

    # Data Tier
    primary_database = db.Column(db.String(50))  # postgresql, oracle, sqlserver, mongodb
    database_version = db.Column(db.String(20))
    orm_framework = db.Column(db.String(50))  # Hibernate, SQLAlchemy, Entity Framework

    # Infrastructure
    container_runtime = db.Column(db.String(50))  # docker, containerd, cri-o
    orchestration = db.Column(db.String(50))  # kubernetes, openshift, ecs, aks
    service_mesh = db.Column(db.String(50))  # istio, linkerd, consul

    # API & Integration
    api_standard = db.Column(db.String(50))  # REST, GraphQL, gRPC
    api_gateway = db.Column(db.String(50))  # kong, apigee, aws-api-gateway
    message_broker = db.Column(db.String(50))  # kafka, rabbitmq, sqs, servicebus

    # Security
    auth_provider = db.Column(db.String(50))  # oauth2, saml, ldap, active-directory
    secrets_manager = db.Column(db.String(50))  # vault, aws-secrets-manager, azure-keyvault

    # Monitoring & Logging
    logging_framework = db.Column(db.String(50))  # log4j, winston, serilog
    metrics_platform = db.Column(db.String(50))  # prometheus, cloudwatch, azure-monitor
    apm_tool = db.Column(db.String(50))  # datadog, newrelic, dynatrace, application-insights
    tracing_tool = db.Column(db.String(50))  # jaeger, zipkin, opentelemetry

    # CI/CD
    build_tool = db.Column(db.String(50))  # maven, gradle, npm, dotnet
    ci_cd_platform = db.Column(db.String(50))  # jenkins, gitlab-ci, azure-devops, github-actions

    # Code Quality & Security
    sast_tool = db.Column(db.String(50))  # sonarqube, checkmarx, fortify
    dast_tool = db.Column(db.String(50))  # zap, burp, acunetix
    dependency_scanner = db.Column(db.String(50))  # snyk, whitesource, blackduck

    # Deployment Configuration (JSON)
    deployment_templates = db.Column(db.Text)  # JSON: Kubernetes manifests, Terraform templates
    environment_variables = db.Column(db.Text)  # JSON: Required env vars
    approved_libraries = db.Column(db.Text)  # JSON: Whitelist of approved dependencies

    # Cost & Licensing
    estimated_cost_per_month = db.Column(db.Numeric(10, 2))
    license_requirements = db.Column(db.Text)
    license_cost_per_year = db.Column(db.Numeric(10, 2))

    # Approval & Governance
    approval_status = db.Column(
        db.String(20), default="draft"
    )  # draft, approved, deprecated, pilot
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    deprecation_date = db.Column(db.DateTime)
    replacement_stack_id = db.Column(db.Integer, db.ForeignKey("technology_stacks.id"))

    # Metadata
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationships
    approved_by = db.relationship("User", foreign_keys=[approved_by_id], backref="approved_stacks")
    replacement_stack = db.relationship(
        "TechnologyStack", remote_side="TechnologyStack.id", backref="replaces"
    )
    architectures = db.relationship(
        "ArchitectureModel", back_populates="technology_stack", overlaps="architecture_models"
    )
    vendor_template = db.relationship("VendorStackTemplate", backref="technology_stack_instances")
    vendor_organization = db.relationship(
        "VendorOrganization",
        foreign_keys=[vendor_organization_id],
        back_populates="technology_stacks",
        overlaps="vendor_org",
    )

    # Capability relationships
    supported_capabilities = db.relationship(
        "EnterpriseCapability",
        secondary="capability_technology_mapping",
        back_populates="technology_stacks",
    )

    # Semantic EA Intelligence Relationships (imported from vendor_organization)
    vendor_products = db.relationship(
        "VendorProduct", secondary="vendor_product_tech_stacks", back_populates="technology_stacks"
    )
    initiatives = db.relationship(
        "EnterpriseInitiative",
        secondary="initiative_tech_stacks",
        back_populates="technology_stacks",
    )

    # Application relationships
    applications = db.relationship(
        "ApplicationComponent",
        secondary="application_technology_mapping",
        back_populates="technology_stacks",
    )

    def __repr__(self):
        return f"<TechnologyStack {self.name} ({self.platform}): {self.approval_status}>"


class PlatformCapability(db.Model):
    """Capabilities and constraints of specific deployment platforms."""

    __tablename__ = "platform_capabilities"

    id = db.Column(db.Integer, primary_key=True)
    technology_stack_id = db.Column(
        db.Integer, db.ForeignKey("technology_stacks.id"), nullable=False
    )
    capability_name = db.Column(db.String(100))  # "Auto-scaling", "Load Balancing", "Managed DB"
    capability_type = db.Column(db.String(50))  # compute, storage, network, security, monitoring
    is_available = db.Column(db.Boolean, default=True)
    configuration = db.Column(db.Text)  # JSON: Specific configuration details
    constraints = db.Column(db.Text)  # JSON: Limitations or restrictions
    cost_impact = db.Column(db.Numeric(10, 2))  # Additional cost for this capability

    technology_stack = db.relationship("TechnologyStack", backref="capabilities")

    def __repr__(self):
        return f"<PlatformCapability {self.capability_name}: {'Available' if self.is_available else 'Unavailable'}>"


# ============================================================================
# Version Control & GitOps Integration
# ============================================================================


# ============================================================================
# Validation & Quality Gates
# ============================================================================


class ValidationGate(db.Model):
    """Pipeline validation gate — stub required for FK resolution in autogenerate."""

    __tablename__ = "validation_gates"

    id = db.Column(db.Integer, primary_key=True)
    pipeline_stage_id = db.Column(db.Integer)
    gate_type = db.Column(db.String(50))
    gate_name = db.Column(db.String(100))
    rule_name = db.Column(db.String(100))
    severity = db.Column(db.String(20))
    status = db.Column(db.String(20))
    is_blocking = db.Column(db.Boolean)
    violations_found = db.Column(db.Integer)
    validated_at = db.Column(db.DateTime)

    violations = db.relationship("ValidationViolation", backref="gate", lazy="dynamic")


class ValidationViolation(db.Model):
    """Specific violations found during validation."""

    __tablename__ = "validation_violations"

    id = db.Column(db.Integer, primary_key=True)
    validation_gate_id = db.Column(db.Integer, db.ForeignKey("validation_gates.id"), nullable=False)

    # Violation Details
    violation_type = db.Column(db.String(50))  # missing_element, invalid_pattern, security_issue
    severity = db.Column(db.String(20))  # blocker, critical, major, minor
    title = db.Column(db.String(200))
    description = db.Column(db.Text)
    recommendation = db.Column(db.Text)  # How to fix

    # Location
    file_path = db.Column(db.String(500))
    line_number = db.Column(db.Integer)
    element_id = db.Column(db.String(100))  # Reference to affected element

    # Status
    status = db.Column(db.String(20), default="open")  # open, fixed, ignored, false_positive
    resolved_at = db.Column(db.DateTime)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    resolved_by = db.relationship("User", backref="resolved_violations")

    def __repr__(self):
        return f"<ValidationViolation {self.violation_type}: {self.severity}>"


# ============================================================================
# Enterprise Integration & External Systems
# ============================================================================


class ExternalSystem(db.Model):
    """Configuration for external system integrations."""

    __tablename__ = "external_systems"

    id = db.Column(db.Integer, primary_key=True)
    system_name = db.Column(db.String(100), nullable=False, unique=True)
    system_type = db.Column(db.String(50))  # servicenow, confluence, ldap, vault, sonarqube

    # Connection
    base_url = db.Column(db.String(500))
    api_endpoint = db.Column(db.String(500))
    auth_type = db.Column(db.String(50))  # basic, oauth2, token, certificate
    credentials = db.Column(db.Text)  # Encrypted credentials JSON

    # Configuration
    enabled = db.Column(db.Boolean, default=True)
    config_json = db.Column(db.Text)  # JSON: System-specific configuration
    sync_enabled = db.Column(db.Boolean, default=False)
    sync_interval_minutes = db.Column(db.Integer)

    # Status
    last_connection_test = db.Column(db.DateTime)
    connection_status = db.Column(db.String(20))  # connected, disconnected, error
    last_sync_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)

    # Metadata
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Relationships
    updated_by = db.relationship("User", backref="external_system_updates")
    def __repr__(self):
        return f"<ExternalSystem {self.system_name}: {self.connection_status}>"


class ComplianceCheck(db.Model):
    """Track compliance verification for generated code."""

    __tablename__ = "compliance_checks"

    id = db.Column(db.Integer, primary_key=True)
    compliance_requirement_id = db.Column(
        db.Integer, db.ForeignKey("compliance_requirements_model.id"), nullable=True
    )
    pipeline_id = db.Column(db.Integer, db.ForeignKey("generation_pipelines.id"))
    code_artifact_id = db.Column(db.Integer, db.ForeignKey("code_artifacts.id"))
    requirement_id = db.Column(db.Integer, db.ForeignKey("requirements.id"), nullable=True)

    # Check Results
    status = db.Column(db.String(20))  # compliant, non-compliant, not-applicable, needs-review
    check_method = db.Column(db.String(50))  # automated, manual, llm-review
    findings = db.Column(db.Text)  # JSON: Detailed findings
    evidence = db.Column(db.Text)  # JSON: Evidence of compliance

    # Remediation
    issues_found = db.Column(db.Integer, default=0)
    remediation_required = db.Column(db.Boolean, default=False)
    remediation_notes = db.Column(db.Text)

    # Timestamps
    checked_at = db.Column(db.DateTime, default=db.func.now())
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)

    # Relationships
    pipeline = db.relationship("GenerationPipeline", back_populates="compliance_checks")
    code_artifact = db.relationship("CodeArtifact", back_populates="compliance_checks")
    requirement = db.relationship("Requirement", back_populates="compliance_checks")
    reviewed_by = db.relationship("User", backref="compliance_reviews")

    def __repr__(self):
        return f"<ComplianceCheck {self.status}: {self.check_method}>"


class SecurityScan(db.Model):
    """Security scanning results for generated code."""

    __tablename__ = "security_scans"

    id = db.Column(db.Integer, primary_key=True)
    pipeline_id = db.Column(db.Integer, db.ForeignKey("generation_pipelines.id"))
    code_artifact_id = db.Column(db.Integer, db.ForeignKey("code_artifacts.id"))

    # Scan Configuration
    scan_type = db.Column(db.String(50))  # sast, dast, dependency, license, secrets
    scanner_name = db.Column(db.String(50))  # sonarqube, snyk, checkmarx, zap
    scanner_version = db.Column(db.String(20))

    # Results Summary
    status = db.Column(db.String(20))  # passed, failed, warning, error
    overall_score = db.Column(db.Numeric(5, 2))  # 0 - 100
    risk_rating = db.Column(db.String(20))  # critical, high, medium, low

    # Vulnerabilities Found
    critical_count = db.Column(db.Integer, default=0)
    high_count = db.Column(db.Integer, default=0)
    medium_count = db.Column(db.Integer, default=0)
    low_count = db.Column(db.Integer, default=0)

    # Detailed Results
    vulnerabilities = db.Column(db.Text)  # JSON: List of vulnerabilities
    dependencies_scanned = db.Column(db.Integer)
    license_issues = db.Column(db.Text)  # JSON: License compliance issues

    # Actions
    blocking_issues = db.Column(db.Integer, default=0)  # Issues that block deployment
    remediation_available = db.Column(db.Boolean, default=False)

    # Timestamps
    scan_started_at = db.Column(db.DateTime)
    scan_completed_at = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Integer)

    # Report
    report_url = db.Column(db.String(500))
    report_file_path = db.Column(db.String(500))

    # Relationships
    pipeline = db.relationship("GenerationPipeline", back_populates="security_scans")
    code_artifact = db.relationship("CodeArtifact", back_populates="security_scans")

    def __repr__(self):
        return f"<SecurityScan {self.scan_type}: {self.risk_rating}>"


# ============================================================================
# ArchiMate 3.2 Motivation Layer - Extended Elements
# ============================================================================


class Outcome(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Outcome element.

    An Outcome is an end result that has been achieved.
    It realizes Goals and is measured through KPIs.

    Follows Basecoat pattern: archimate_element_id links to ArchiMateElement.

    TenantMixin declares organization_id NOT NULL, but `outcomes` is an
    existing table and reconcile-schema can only ADD nullable columns (ADR
    0002). Override to nullable so the column can land on deployed databases
    without a maintenance window -- same pattern as Principle below.
    Consequence: pre-existing rows have organization_id NULL until backfilled.
    Run: flask --app manage backfill-outcome-org
    """

    __tablename__ = "outcomes"
    __table_args__ = {"extend_existing": True}

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    # ArchiMate linkage (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Outcome measurement
    kpi_metric = db.Column(db.String(100))  # "Revenue", "NPS", "System Uptime"
    target_value = db.Column(db.String(50))  # "€20M", "NPS >50", "99.9%"
    current_value = db.Column(db.String(50))  # Current measured value
    measurement_unit = db.Column(db.String(50))  # "EUR", "percentage", "count"
    measurement_frequency = db.Column(db.String(30))  # daily, weekly, monthly, quarterly

    # Goal realization (Outcome realizes Goal)
    goal_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    realization_status = db.Column(db.String(30), default="not_started")
    # not_started, in_progress, at_risk, achieved, failed

    # Metadata
    baseline_value = db.Column(db.String(50))  # Starting point
    target_date = db.Column(db.Date)  # When should target be achieved
    achieved_date = db.Column(db.Date)  # When was target actually achieved

    # Relationships
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))

    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationship mappings
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], backref="outcome"
    )
    goal = db.relationship("ArchiMateElement", foreign_keys=[goal_id], backref="realized_outcomes")
    architecture = db.relationship("ArchitectureModel", backref="outcomes")

    def __repr__(self):
        return f"<Outcome {self.name}: {self.kpi_metric}>"

    @property
    def achievement_percentage(self):
        """Calculate achievement percentage if values are numeric."""
        try:
            if self.current_value and self.target_value:
                current = float(self.current_value.replace("%", "").replace(",", ""))
                target = float(self.target_value.replace("%", "").replace(",", ""))
                return min(100, (current / target) * 100)
        except (ValueError, ZeroDivisionError):
            pass
        return None


class Principle(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Principle element.

    A Principle is a normative property of the implementation of systems,
    constraining and guiding the design and evolution of an architecture.

    Examples:
    - "All data must be encrypted at rest and in transit"
    - "Cloud-first strategy for all new applications"
    - "Open source preferred over proprietary solutions"

    Follows Basecoat pattern: archimate_element_id links to ArchiMateElement.
    """

    __tablename__ = "principles"

    # TenantMixin declares organization_id NOT NULL, but `principles` is an
    # existing table and `reconcile-schema` can only ADD nullable columns
    # (see CLAUDE.md / ADR 0002). Override to nullable so the column can land on
    # deployed databases without a maintenance window. Inheriting TenantMixin is
    # what actually switches on isolation — tenant_isolation filters on
    # isinstance(TenantMixin), not on the column's nullability.
    #
    # Consequence: pre-existing rows have organization_id NULL and are filtered
    # out of every tenant-scoped query until backfilled. Run:
    #     flask --app manage backfill-principle-org
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    statement = db.Column(db.Text, nullable=False)  # The principle statement
    rationale = db.Column(db.Text)  # Why this principle exists
    implications = db.Column(db.Text)  # What this means for implementation

    # ArchiMate linkage (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Principle classification
    category = db.Column(db.String(50))
    # Security, Data, Integration, Technology, Business, Architecture

    enforcement_level = db.Column(db.String(20))  # MUST, SHOULD, MAY (RFC 2119)

    # Governance
    approved_by = db.Column(db.String(100))  # Architecture Review Board, CTO, etc.
    approval_date = db.Column(db.Date)
    review_frequency = db.Column(db.String(30))  # annual, biannual, quarterly
    next_review_date = db.Column(db.Date)

    # Status
    status = db.Column(db.String(30), default="draft")
    # draft, under_review, approved, deprecated, superseded

    citation_count = db.Column(db.Integer, nullable=True)
    superseded_by_id = db.Column(db.Integer, db.ForeignKey("principles.id"))
    retired_into_id = db.Column(db.Integer, db.ForeignKey("principles.id"), nullable=True)

    # Relationships
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))

    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationship mappings
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], backref="principle"
    )
    architecture = db.relationship("ArchitectureModel", backref="principles")
    superseded_by = db.relationship(
        "Principle",
        foreign_keys=[superseded_by_id],
        remote_side="Principle.id",
        backref="supersedes",
    )
    retired_into = db.relationship(
        "Principle",
        foreign_keys=[retired_into_id],
        remote_side="Principle.id",
        backref="retired_principles",
    )

    # Initiative relationships (Principles constrain Initiatives)
    initiatives = db.relationship(
        "EnterpriseInitiative", secondary="initiative_principles", back_populates="principles"
    )

    def __repr__(self):
        return f"<Principle {self.name} [{self.enforcement_level}]>"


class ConstraintElement(db.Model):
    """
    ArchiMate 3.2 Constraint element.

    A Constraint is a factor that prevents or obstructs the realization of goals.

    Examples:
    - Budget: "Maximum €5M CapEx budget for this initiative"
    - Timeline: "Must launch by Q2 2026 (regulatory deadline)"
    - Regulatory: "GDPR compliance required - EU data residency"
    - Technical: "Must run on OpenShift, not native Kubernetes"
    - Resource: "Only 2 Java developers available on team"

    Follows Basecoat pattern: archimate_element_id links to ArchiMateElement.
    """

    __tablename__ = "constraints"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)

    # ArchiMate linkage (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Constraint classification
    constraint_type = db.Column(db.String(50))
    # budget, timeline, regulatory, technical, resource, platform, vendor, organizational

    # Constraint severity
    is_hard_constraint = db.Column(db.Boolean, default=True)
    # True = cannot violate, False = negotiable/soft constraint

    # Constraint details
    constraint_value = db.Column(db.String(200))  # "€5M max", "12 months", "EU only"
    constraint_unit = db.Column(db.String(50))  # "EUR", "months", "region"

    # Impact
    impacts_capabilities = db.Column(db.Text)  # JSON: List of capability IDs affected
    violation_consequence = db.Column(db.Text)  # What happens if violated

    # Constraint relationships
    goal_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    # Goal being constrained

    # Temporal aspects
    effective_from = db.Column(db.Date)  # When constraint starts
    effective_until = db.Column(db.Date)  # When constraint expires (if applicable)

    # Status
    status = db.Column(db.String(30), default="active")
    # active, expired, waived, superseded

    waiver_reason = db.Column(db.Text)  # If status=waived, why was it waived
    waived_by = db.Column(db.String(100))  # Who approved the waiver

    # Relationships
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))

    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationship mappings
    archimate_element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    goal = db.relationship("ArchiMateElement", foreign_keys=[goal_id])
    architecture = db.relationship("ArchitectureModel", backref="constraints")

    def __repr__(self):
        return f"<Constraint {self.name} [{self.constraint_type}]>"

    @property
    def is_active(self):
        """Check if constraint is currently active."""
        from datetime import date

        today = date.today()

        if self.status != "active":
            return False

        if self.effective_from and today < self.effective_from:
            return False

        if self.effective_until and today > self.effective_until:
            return False

        return True


class RiskAssessment(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Assessment element (Risk-specific).

    A Risk Assessment is the result of analyzing risks that could prevent
    goal achievement or impact capability delivery.

    Risk Types:
    - Technical: Unproven technology, integration complexity, performance
    - Organizational: Skills gap, change resistance, resource constraints
    - Schedule: Dependencies, procurement delays, vendor availability
    - Budget: Cost overruns, unexpected expenses
    - Regulatory: Compliance failures, legal challenges

    Follows Basecoat pattern: archimate_element_id links to ArchiMateElement.
    """

    __tablename__ = "risk_assessments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)

    # ArchiMate linkage (Basecoat pattern)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))

    # Risk categorization
    risk_type = db.Column(db.String(50))
    # technical, organizational, schedule, budget, regulatory, operational, strategic

    risk_category = db.Column(db.String(50))
    # threat (negative) or opportunity (positive)

    # Risk scoring
    probability = db.Column(db.String(20))  # very_low, low, medium, high, very_high
    probability_score = db.Column(db.Integer)  # 1 - 5

    impact = db.Column(db.String(20))  # negligible, low, medium, high, critical
    impact_score = db.Column(db.Integer)  # 1 - 5

    risk_score = db.Column(db.Integer)  # probability_score × impact_score (1 - 25)
    risk_level = db.Column(
        db.String(20)
    )  # low (1 - 5), medium (6 - 12), high (13 - 19), critical (20 - 25)

    # Risk details
    triggers = db.Column(db.Text)  # What events would cause this risk to materialize
    indicators = db.Column(db.Text)  # Early warning signs

    # Risk response
    response_strategy = db.Column(db.String(50))
    # avoid, mitigate, transfer, accept, exploit (for opportunities)

    mitigation_strategy = db.Column(db.Text)  # How to reduce probability or impact
    contingency_plan = db.Column(db.Text)  # Plan B if risk materializes

    mitigation_cost = db.Column(db.Numeric(12, 2))  # Cost to mitigate
    mitigation_effort = db.Column(db.String(50))  # low, medium, high

    # Risk ownership
    risk_owner = db.Column(db.String(100))  # Who owns this risk
    action_owner = db.Column(db.String(100))  # Who's responsible for mitigation

    # Risk linkage (what is at risk?)
    driver_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    goal_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"))
    capability_id = db.Column(db.Integer, db.ForeignKey("business_capability.id"))
    requirement_id = db.Column(db.Integer, db.ForeignKey("requirements.id"))

    # Risk status
    status = db.Column(db.String(30), default="identified")
    # identified, analyzed, mitigated, monitoring, materialized, closed

    residual_probability = db.Column(db.Integer)  # After mitigation (1 - 5)
    residual_impact = db.Column(db.Integer)  # After mitigation (1 - 5)
    residual_risk_score = db.Column(db.Integer)  # residual_prob × residual_impact

    # Temporal tracking
    identified_date = db.Column(db.Date)
    review_date = db.Column(db.Date)  # Last reviewed
    next_review_date = db.Column(db.Date)
    closure_date = db.Column(db.Date)  # When risk was closed/resolved

    # Relationships
    architecture_id = db.Column(db.Integer, db.ForeignKey("architecture_models.id"))

    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # Relationship mappings
    archimate_element = db.relationship(
        "ArchiMateElement", foreign_keys=[archimate_element_id], backref="risk_assessment"
    )
    driver = db.relationship("ArchiMateElement", foreign_keys=[driver_id], backref="driven_risks")
    goal = db.relationship("ArchiMateElement", foreign_keys=[goal_id], backref="goal_risks")
    capability = db.relationship(
        "BusinessCapability", foreign_keys=[capability_id], backref="capability_risks"
    )
    requirement = db.relationship(
        "Requirement", foreign_keys=[requirement_id], backref="requirement_risks"
    )
    architecture = db.relationship("ArchitectureModel", backref="risk_assessments")

    def __repr__(self):
        return f"<RiskAssessment {self.name} [{self.risk_level}]>"

    @property
    def risk_reduction_percentage(self):
        """Calculate risk reduction after mitigation."""
        if self.risk_score and self.residual_risk_score:
            reduction = ((self.risk_score - self.residual_risk_score) / self.risk_score) * 100
            return round(reduction, 1)
        return None

    def calculate_risk_score(self):
        """Calculate risk score from probability and impact."""
        if self.probability_score and self.impact_score:
            self.risk_score = self.probability_score * self.impact_score

            # Determine risk level
            if self.risk_score <= 5:
                self.risk_level = "low"
            elif self.risk_score <= 12:
                self.risk_level = "medium"
            elif self.risk_score <= 19:
                self.risk_level = "high"
            else:
                self.risk_level = "critical"

    def calculate_residual_risk_score(self):
        """Calculate residual risk score after mitigation."""
        if self.residual_probability and self.residual_impact:
            self.residual_risk_score = self.residual_probability * self.residual_impact


class Notification(db.Model):
    """In-app notifications surfaced via the header bell icon."""
    __tablename__ = "notifications"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    url = db.Column(db.String(500), nullable=True)
    read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# BA-003: Business Layer ArchiMate ORM entities — re-exported from canonical modules.
# These classes already exist in their canonical files; we expose them from models.py
# so that `from app.models.models import BusinessProcess` etc. works for downstream use.
from .process_data import BusinessProcess  # noqa: E402,F401  # dead-code-ok
from .unified_capability import BusinessFunction  # noqa: E402,F401  # dead-code-ok
from .business_layer import (  # noqa: E402,F401  # dead-code-ok
    BusinessActor,
    BusinessObject,
    BusinessRole,
    BusinessService,
)
from .archimate_business import BusinessCollaboration, BusinessInterface  # noqa: E402,F401  # dead-code-ok


class ApqcProcessHierarchy(db.Model):
    """APQC Process Classification Framework L1→L2→L3 hierarchy.

    Enables structured process taxonomy browsing and Phase B gap analysis.
    Table created via flask init-db.
    """
    __tablename__ = "apqc_process_hierarchy"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    level = db.Column(db.Integer, nullable=False)  # 1, 2, or 3
    parent_id = db.Column(db.Integer, db.ForeignKey("apqc_process_hierarchy.id"), nullable=True)
    apqc_reference_number = db.Column(db.String(50), nullable=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    children = db.relationship("ApqcProcessHierarchy", backref=db.backref("parent", remote_side=[id]))


# BA-004: Motivation Layer ArchiMate ORM entities.
# MotivationDriver, MotivationGoal, MotivationPrinciple are new.
# The others (Outcome, Stakeholder, Constraint, Assessment) are re-exported from archimate_motivation.py.

class MotivationDriver(db.Model):
    """ArchiMate Motivation Layer — Driver element."""
    __tablename__ = "motivation_drivers"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    driver_type = db.Column(db.String(50), nullable=True)  # external/internal
    source_document = db.Column(db.Text, nullable=True)
    archimate_element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Re-export existing Motivation Layer models from their canonical module
from .archimate_motivation import (  # noqa: E402,F401  # dead-code-ok
    MotivationAssessment,
    MotivationConstraint,
    MotivationOutcome,
    MotivationStakeholder,
)


# SA-001: Application Layer ArchiMate ORM entities — re-exported from application_layer.py.
from .application_layer import (  # noqa: E402,F401  # dead-code-ok
    ApplicationEvent,
    ApplicationFunction,
    ApplicationInteraction,
    ApplicationInterface,
    ApplicationProcess,
    ApplicationService,
)


# SA-002: Data Layer ArchiMate ORM entities.
# DataObject is re-exported from application_layer.py (tablename: application_data_objects).
# DataFlow and DataStore are new models.

# ADR-0003: tenant-scoped — organization_id backfilled/hardened by flask backfill-layer-tenancy
class DataStore(TenantMixin, db.Model):
    """ArchiMate Data Layer — Data Store element representing persistent data storage."""
    __tablename__ = "data_stores"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    store_type = db.Column(db.String(50), nullable=True)  # relational/nosql/file/object/cache
    is_master_record = db.Column(db.Boolean, default=False)
    app_component_id = db.Column(db.Integer, db.ForeignKey("application_components.id"), nullable=True)
    data_classification = db.Column(db.String(20), default="internal")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Re-export DataObject from its canonical module
from .application_layer import DataObject  # noqa: E402,F401  # dead-code-ok


# SA-003: Add ApplicationLifecycleState columns to ApplicationComponent.
# ApplicationComponent is defined in application_portfolio.py.
# We extend its table and mapper here without editing that file.
from sqlalchemy import Column, Integer, String as _SA_String  # noqa: E402
from .application_portfolio import ApplicationComponent as _ApplicationComponent  # noqa: E402

_ac_table = _ApplicationComponent.__table__
_lifecycle_cols = [
    ("arch_pattern", _SA_String(50)),
    ("current_lifecycle_state", _SA_String(30)),
    ("target_disposition", _SA_String(30)),
    ("transition_wave", Integer()),
]
for _col_name, _col_type in _lifecycle_cols:
    if _col_name not in _ac_table.c:
        _col = Column(_col_name, _col_type, nullable=True)
        _ac_table.append_column(_col)
        setattr(_ApplicationComponent, _col_name, _col)

# Re-export so `from app.models.models import ApplicationComponent` works
ApplicationComponent = _ApplicationComponent  # noqa: F401
