"""Shared tenant fence for CapabilityProcessMapping / ProcessApplicationMapping.

Neither model carries an ``organization_id`` of its own -- ownership is only
reachable via ``capability_id`` -> ``BusinessCapability.organization_id`` or
``application_id`` -> ``ApplicationComponent.organization_id``. PRs 301, 303
and 306 each independently discovered and fenced part of the surface these
two tables expose (reads in ``apqc_api_routes.py``/``api_vendors.py``/
``mapping_routes.py``, writes and deletes in the same files plus
``process_routes.py``), with the same join+filter pattern inlined at every
call site. This is the one fence every route now calls instead (pr303-v2
review, DEFECT D5/D6): consolidating PRs 301/303/306 into a single change.

Two shapes, matching the two things a route needs:

* ``*_owned_by_caller(id)`` -- a plain ownership check, for a write's target
  (create a mapping on an application/capability) or a pre-write guard.
* ``*_mapping_in_caller_org(mapping_id)`` -- fetch one mapping row by its own
  id, or ``None`` if it doesn't exist or belongs to another organisation
  (read-detail, update, delete).
* ``fenced_*_mappings_query()`` -- the base query for a list read, already
  joined and filtered; callers chain further ``.filter(...)`` clauses (by
  ``apqc_process_id``, etc.) the same way they would on the bare
  ``Model.query``.

Every one of these fails closed: no ambient organisation (CLI, scheduler, no
request context) returns ``None`` / ``False`` / an empty query rather than
falling back to an unscoped read.
"""
from __future__ import annotations

from typing import Optional

from app import db
from app.utils.tenant_sql import current_org_id


def application_owned_by_caller(application_id) -> bool:
    """Whether ``application_id`` belongs to the caller's organisation."""
    if not application_id:
        return False
    org_id = current_org_id()
    if org_id is None:
        return False
    from app.models.application_layer import ApplicationComponent

    return (
        db.session.execute(
            db.select(ApplicationComponent).where(
                ApplicationComponent.id == application_id,
                ApplicationComponent.organization_id == org_id,
            )
        ).scalar_one_or_none()
        is not None
    )


def capability_owned_by_caller(capability_id) -> bool:
    """Whether ``capability_id`` belongs to the caller's organisation."""
    if not capability_id:
        return False
    org_id = current_org_id()
    if org_id is None:
        return False
    from app.models.business_capabilities import BusinessCapability

    return (
        db.session.execute(
            db.select(BusinessCapability).where(
                BusinessCapability.id == capability_id,
                BusinessCapability.organization_id == org_id,
            )
        ).scalar_one_or_none()
        is not None
    )


def fenced_application_mappings_query():
    """Base query for ``ProcessApplicationMapping``, joined to its owning
    ``ApplicationComponent`` and filtered to the caller's organisation.
    Chain further filters (``apqc_process_id``, etc.) the same way as on the
    bare ``ProcessApplicationMapping.query``. Returns a query matching no
    rows (rather than raising or falling back unscoped) with no ambient
    organisation.
    """
    from app.models.apqc_process import ProcessApplicationMapping
    from app.models.application_layer import ApplicationComponent

    org_id = current_org_id()
    query = ProcessApplicationMapping.query.join(
        ApplicationComponent,
        ProcessApplicationMapping.application_id == ApplicationComponent.id,
    )
    if org_id is None:
        return query.filter(db.false())
    return query.filter(ApplicationComponent.organization_id == org_id)


def fenced_capability_mappings_query():
    """Base query for ``CapabilityProcessMapping``, joined to its owning
    ``BusinessCapability`` and filtered to the caller's organisation. Same
    contract as :func:`fenced_application_mappings_query`.
    """
    from app.models.apqc_process import CapabilityProcessMapping
    from app.models.business_capabilities import BusinessCapability

    org_id = current_org_id()
    query = CapabilityProcessMapping.query.join(
        BusinessCapability,
        CapabilityProcessMapping.capability_id == BusinessCapability.id,
    )
    if org_id is None:
        return query.filter(db.false())
    return query.filter(BusinessCapability.organization_id == org_id)


def application_mapping_in_caller_org(mapping_id) -> Optional[object]:
    """Fetch a ``ProcessApplicationMapping`` by its own id, only if it
    belongs to the caller's organisation. ``None`` on no match, no ambient
    org, or a foreign owner -- fail closed rather than guess.
    """
    from app.models.apqc_process import ProcessApplicationMapping

    return fenced_application_mappings_query().filter(
        ProcessApplicationMapping.id == mapping_id
    ).first()


def capability_mapping_in_caller_org(mapping_id) -> Optional[object]:
    """Fetch a ``CapabilityProcessMapping`` by its own id, only if it
    belongs to the caller's organisation. Same contract as
    :func:`application_mapping_in_caller_org`.
    """
    from app.models.apqc_process import CapabilityProcessMapping

    return fenced_capability_mappings_query().filter(
        CapabilityProcessMapping.id == mapping_id
    ).first()
