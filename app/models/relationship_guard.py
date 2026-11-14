"""The one same-organisation check for ArchiMate relationship rows.

Kept free of the heavy model graph so both ArchiMateRelationship classes (the
monolithic one in models.py and the light one in archimate_core.py used under
APP_FAST_INIT=1) register the same guard.
"""

from sqlalchemy import event


class CrossOrganisationRelationship(ValueError):
    """A relationship whose source or target belongs to an organisation other
    than the row's own organization_id. Raised by the one same-organisation
    function below; a ValueError so existing callers that catch ValueError
    keep turning it into a plain message."""


# Each value the archimate_relationships.source_kind column can hold (the
# column comment above: 'manual', 'import', 'connector', 'derived', 'sample';
# nullable, and NULL reads as 'manual') mapped to the table that holds the
# far ends of rows of that kind. A kind with no entry here cannot be
# resolved and is refused, never allowed.
_RELATIONSHIP_END_TABLES = {
    None: "archimate_elements",
    "manual": "archimate_elements",
    "import": "archimate_elements",
    "connector": "archimate_elements",
    "derived": "archimate_elements",
    "sample": "archimate_elements",
}


def assert_relationship_same_organisation(
    connection, organization_id, source_id, target_id, source_kind=None
):
    """Refuse a relationship row whose source or target belongs to another
    organisation. The one function behind the service check, accept_suggestion
    and the model guard.

    Reads each end's organization_id with plain SQL through the given
    (flush) connection, so the tenant filter on ORM queries cannot hide a
    foreign end: a missing end, or a source_kind with no table in
    _RELATIONSHIP_END_TABLES, is refused. Raises CrossOrganisationRelationship.
    """
    from sqlalchemy import text

    table = _RELATIONSHIP_END_TABLES.get(source_kind or None)
    if table is None:
        raise CrossOrganisationRelationship(
            f"cannot resolve relationship ends of kind {source_kind!r}: no table for that kind"
        )
    for end, end_id in (("source", source_id), ("target", target_id)):
        row = connection.execute(
            # nosec B608 - table name comes from the literal map above, never from input
            text(f"SELECT organization_id FROM {table} WHERE id = :end_id"),
            {"end_id": end_id},
        ).first()
        if row is None:
            raise CrossOrganisationRelationship(
                f"relationship {end} {end_id} does not exist in {table}"
            )
        if row[0] != organization_id:
            raise CrossOrganisationRelationship(
                f"relationship {end} {end_id} is not in organisation {organization_id}"
            )


def _guard_relationship_same_organisation(mapper, connection, target):
    """Refuse an ArchiMateRelationship whose source or target element belongs
    to an organisation other than the row's own organization_id.

    Applies to every writer that persists the model directly (connectors,
    imports, sync, backfills, seeds) as well as the service path, so no
    code path can save a crossing row. Reads the far ends through the flush
    connection with plain SQL, so a tenant-filtered session cannot hide a
    foreign end from this guard.
    """
    from .mixins.core import _default_org_id

    org_id = target.organization_id
    if org_id is None:
        org_id = _default_org_id()
    if org_id is None:
        return
    assert_relationship_same_organisation(
        connection,
        org_id,
        target.source_id,
        target.target_id,
        source_kind=target.source_kind or None,
    )


def register_relationship_guard(cls):
    """Attach the same-organisation guard to a relationship model class."""
    event.listen(cls, "before_insert", _guard_relationship_same_organisation)
    event.listen(cls, "before_update", _guard_relationship_same_organisation)
    return cls
