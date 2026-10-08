"""One version per change, for the entity tables a generic trigger covers.

Every change to an element or
relationship leaves a row here with a non-overlapping recorded interval, so
"the model as of a date" and "what changed between two dates" (PR 2) can be
answered from this table alone -- the relational history is the system of
record; the graph projection is built
from it, not the other way around.

A generic trigger (``flask apply-entity-history-trigger``,
``app/commands/entity_history_migration.py``) writes one row per INSERT and
UPDATE on ``archimate_elements`` and ``archimate_relationships`` -- PR 1's
scope; the brief's "other entity tables listed in the PR" is left to a
follow-up rather than guessed at here. ``flask backfill-entity-history``
seeds one open (``valid_to IS NULL``) row per existing element/relationship,
with ``recorded_at`` taken from the audit log's earliest matching entry
where one exists and left NULL ("unknown") where it does not.

Invariant this table must never violate for a given (table_name, record_id):
at most one row has ``valid_to IS NULL`` (the current version), and every
other row's ``valid_to`` equals the next row's ``valid_from`` -- a closed,
non-overlapping chain. The trigger and the backfill command are the only
writers; nothing else should insert here directly.
"""
from __future__ import annotations

from app import db
from app.models.mixins import TenantMixin


class EntityHistory(TenantMixin, db.Model):
    __tablename__ = "entity_history"
    __table_args__ = (
        db.Index("ix_entity_history_table_record", "table_name", "record_id"),
        db.Index("ix_entity_history_org_table_record", "organization_id", "table_name", "record_id"),
    )

    id = db.Column(db.BigInteger, primary_key=True)

    # Overrides TenantMixin's own organization_id, which declares a hard
    # FK -> organizations.id. Some pre-existing archimate_elements/
    # archimate_relationships rows carry an organization_id that predates
    # that FK ever being enforced on those tables (reconcile-schema can add
    # a column but never a constraint -- the same class of drift CLAUDE.md
    # documents for the typed ARB FK, found here directly: a row_to_json()
    # snapshot of one such row raised ForeignKeyViolation on insert here).
    # A derived history copy enforcing a stricter constraint than the row it
    # copies is the wrong place to first surface that drift -- keep the
    # column (the tenant listener filters by class, not by the presence of
    # a FK) but without the hard constraint.
    organization_id = db.Column(db.Integer, nullable=True, index=True)

    # TenantMixin's own `organization` relationship infers its join from the
    # FK just removed above; without an override here mapper configuration
    # fails for every model at import time, not just this one. viewonly
    # since nothing here should write through it.
    organization = db.relationship(
        "Organization",
        primaryjoin="foreign(EntityHistory.organization_id) == Organization.id",
        viewonly=True,
        lazy="select",
    )

    # Which row this version is for. table_name/record_id mirror AuditLog's
    # own naming (app/models/audit_log.py) rather than inventing a second
    # convention for "which row".
    table_name = db.Column(db.String(100), nullable=False, index=True)
    record_id = db.Column(db.Integer, nullable=False, index=True)

    # The row's full column state during this version, as the trigger or
    # the backfill saw it -- not just the changed columns, so "the model as
    # of a date" (PR 2) never has to reconstruct a partial row from several
    # versions.
    snapshot = db.Column(db.JSON, nullable=False)

    # The interval this version held. valid_to IS NULL means this is the
    # current version (open interval); superseded_at is stamped on THIS row,
    # once, by the version that closed it, so "when did this stop being
    # true" survives even after a later version makes valid_to no longer
    # NULL-searchable in a running query.
    valid_from = db.Column(db.DateTime, nullable=False, index=True)
    valid_to = db.Column(db.DateTime, nullable=True, index=True)
    superseded_at = db.Column(db.DateTime, nullable=True)

    # When this version was recorded. NULL means "unknown" -- a
    # pre-existing row with no matching audit-log entry to backfill from),
    # rendered as an em dash per CLAUDE.md's null-display convention, never
    # guessed at as equal to valid_from.
    recorded_at = db.Column(db.DateTime, nullable=True, index=True)

    # Who and why, read-only from the audit log ("who changed it
    # and why" join) where the trigger or backfill could resolve one.
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    change_reason = db.Column(db.String(200), nullable=True)

    # Provenance (ADR 0008: "a copy declares itself"). "trigger" for a row
    # written live by the generic trigger, "backfill" for a row seeded by
    # backfill-entity-history for a pre-existing entity.
    source = db.Column(db.String(20), nullable=False, default="trigger")

    def __repr__(self):
        return (
            f"<EntityHistory {self.table_name}:{self.record_id} "
            f"{self.valid_from}..{self.valid_to or 'open'}>"
        )
