"""
ArchiMate relationship auto-sync for domain junction tables.

When a junction row is inserted (e.g. ServiceRealization linking
BusinessProcess -> BusinessService), this module auto-creates the
corresponding ArchiMateRelationship record.

Extends the pattern from process_data.py (_ensure_archimate_relationship).

Wave 1 covers 3 junction tables:
  - ServiceRealization  -> realization  (BusinessProcess -> BusinessService)
  - ProcessRoleRaci     -> assignment   (BusinessRole -> BusinessProcess)
  - ApplicationProcessSupport -> serving (ApplicationComponent -> BusinessProcess)

Wave 2 covers 3 more junction tables:
  - ProcessActorRaci    -> assignment   (BusinessActor -> BusinessProcess)
  - ProcessDataCrud     -> access       (BusinessProcess -> BusinessObject)
  - ServiceDependency   -> serving      (BusinessService -> BusinessService)

Wave 3 covers 3 more junction tables:
  - CapabilityActorRaci -> assignment   (BusinessActor -> BusinessCapability)
  - InterfaceConsumer   -> serving      (ApplicationInterface -> ApplicationComponent)
  - DataObjectStorage   -> access       (ApplicationComponent -> BusinessObject)

Note: 3 db.Table associations (actor_role_assignment, application_component_vendor_products,
capability_compliance_requirements) cannot use mapper events — covered by backfill only.
"""

from sqlalchemy import event, select
from flask import g, has_request_context

from app import db


def _get_element_id(instance_or_id, model_class=None):
    """Get the ArchiMateElement.id for a domain model instance.

    Each domain model has an ``archimate_element_id`` FK populated
    by its own before_insert listener. This just reads it.
    """
    if isinstance(instance_or_id, int):
        if model_class is None:
            return None
        obj = db.session.get(model_class, instance_or_id)
        return obj.archimate_element_id if obj else None
    return getattr(instance_or_id, "archimate_element_id", None)


def _ensure_relationship(
    session,
    rel_type,
    source_id,
    target_id,
    architecture_id=None,
    expected_organization_id=None,
):
    """Idempotent ArchiMateRelationship creator.

    Reuses the same logic as process_data.py:_ensure_archimate_relationship.
    """
    if not source_id or not target_id:
        return None

    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    endpoint_rows = session.execute(
        select(
            ArchiMateElement.__table__.c.id,
            ArchiMateElement.__table__.c.organization_id,
        ).where(ArchiMateElement.__table__.c.id.in_((source_id, target_id)))
    ).all()
    endpoint_tenants = {row.id: row.organization_id for row in endpoint_rows}
    source_tenant = endpoint_tenants.get(source_id)
    target_tenant = endpoint_tenants.get(target_id)
    if source_tenant is None or target_tenant is None:
        return None
    if source_tenant != target_tenant:
        raise ValueError("ArchiMate relationship endpoints must share a tenant")
    request_tenant = (
        getattr(g, "current_org_id", None) if has_request_context() else None
    )
    expected_tenant = expected_organization_id or request_tenant
    if expected_tenant is not None and source_tenant != int(expected_tenant):
        raise ValueError("ArchiMate relationship endpoints are outside the active tenant")

    query = session.query(ArchiMateRelationship).filter_by(
        type=rel_type,
        source_id=source_id,
        target_id=target_id,
        organization_id=source_tenant,
    )
    if architecture_id is None:
        query = query.filter(ArchiMateRelationship.architecture_id.is_(None))
    else:
        query = query.filter_by(architecture_id=architecture_id)

    existing = query.one_or_none()
    if existing:
        return existing

    rel = ArchiMateRelationship(
        type=rel_type,
        source_id=source_id,
        target_id=target_id,
        architecture_id=architecture_id,
        organization_id=source_tenant,
    )
    session.add(rel)
    return rel


def _remove_relationship(session, rel_type, source_id, target_id):
    """Remove ArchiMateRelationship when junction row is deleted.

    Per-instance query + ``session.delete()`` rather than a bulk
    ``Query.delete()`` (round-2 refuter findings NEW-1/NEW-2): a bulk
    ``.delete()`` here never populates ``session.deleted``, so it is
    structurally invisible to T-003's precisely-scoped ``after_flush``
    invalidation listener and instead falls through to the conservative,
    tenant-blunt ``do_orm_execute`` bulk-listener fallback -- turning an
    ordinary single-row junction unlink into either an entire tenant's
    derived-fact store going stale (NEW-1) or, when no tenant context is
    resolvable, every tenant's store going stale (NEW-2). This call site
    only ever matches a small number of rows (one relationship instance per
    (type, source, target) triple), so per-instance deletion costs nothing
    and lets these deletes flow through the existing, already-tested,
    correctly-scoped ``after_flush`` per-row path instead.
    """
    if not source_id or not target_id:
        return

    from app.models.archimate_core import ArchiMateRelationship

    from app.models.archimate_core import ArchiMateElement

    # Only the organisation that owns the source element: two organisations never share an element, but
    # the filter keeps this delete from ever reaching a relationship another organisation owns.
    owner = session.execute(
        select(ArchiMateElement.__table__.c.organization_id).where(
            ArchiMateElement.__table__.c.id == source_id
        )
    ).scalar()
    existing = (
        session.query(ArchiMateRelationship)
        .filter_by(type=rel_type, source_id=source_id, target_id=target_id)
        .filter(ArchiMateRelationship.organization_id == owner)
        .all()
    )
    for rel in existing:
        session.delete(rel)


# ---------------------------------------------------------------------------
# Listener 1: ServiceRealization -> realization
# BusinessProcess realizes BusinessService
# ---------------------------------------------------------------------------

from app.models.relationship_tables import ServiceRealization  # noqa: E402


@event.listens_for(ServiceRealization, "after_insert")
def _on_service_realization_insert(mapper, connection, target):
    """BusinessProcess realizes BusinessService."""
    from app.models.business_layer import BusinessService
    from app.models.process_data import BusinessProcess

    process = db.session.get(BusinessProcess, target.process_id)
    service = db.session.get(BusinessService, target.service_id)
    if process and service and process.archimate_element_id and service.archimate_element_id:
        _ensure_relationship(
            db.session,
            "realization",
            source_id=process.archimate_element_id,
            target_id=service.archimate_element_id,
        )


@event.listens_for(ServiceRealization, "after_delete")
def _on_service_realization_delete(mapper, connection, target):
    """Clean up realization when ServiceRealization is deleted."""
    from app.models.business_layer import BusinessService
    from app.models.process_data import BusinessProcess

    process = db.session.get(BusinessProcess, target.process_id)
    service = db.session.get(BusinessService, target.service_id)
    if process and service and process.archimate_element_id and service.archimate_element_id:
        _remove_relationship(
            db.session,
            "realization",
            process.archimate_element_id,
            service.archimate_element_id,
        )


# ---------------------------------------------------------------------------
# Listener 2: ProcessRoleRaci -> assignment
# BusinessRole assigned to BusinessProcess
# ---------------------------------------------------------------------------

from app.models.relationship_tables import ProcessRoleRaci  # noqa: E402


@event.listens_for(ProcessRoleRaci, "after_insert")
def _on_raci_insert(mapper, connection, target):
    """BusinessRole assigned to BusinessProcess."""
    from app.models.business_layer import BusinessRole
    from app.models.process_data import BusinessProcess

    role = db.session.get(BusinessRole, target.role_id)
    process = db.session.get(BusinessProcess, target.process_id)
    if role and process and role.archimate_element_id and process.archimate_element_id:
        _ensure_relationship(
            db.session,
            "assignment",
            source_id=role.archimate_element_id,
            target_id=process.archimate_element_id,
        )


@event.listens_for(ProcessRoleRaci, "after_delete")
def _on_raci_delete(mapper, connection, target):
    """Clean up assignment when ProcessRoleRaci is deleted."""
    from app.models.business_layer import BusinessRole
    from app.models.process_data import BusinessProcess

    role = db.session.get(BusinessRole, target.role_id)
    process = db.session.get(BusinessProcess, target.process_id)
    if role and process and role.archimate_element_id and process.archimate_element_id:
        _remove_relationship(
            db.session,
            "assignment",
            role.archimate_element_id,
            process.archimate_element_id,
        )


# ---------------------------------------------------------------------------
# Listener 3: ApplicationProcessSupport -> serving
# ApplicationComponent serves BusinessProcess
# ---------------------------------------------------------------------------

from app.models.relationship_tables import ApplicationProcessSupport  # noqa: E402


@event.listens_for(ApplicationProcessSupport, "after_insert")
def _on_app_process_insert(mapper, connection, target):
    """ApplicationComponent serves BusinessProcess."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.process_data import BusinessProcess

    app_comp = db.session.get(ApplicationComponent, target.application_component_id)
    process = db.session.get(BusinessProcess, target.business_process_id)
    if app_comp and process and app_comp.archimate_element_id and process.archimate_element_id:
        _ensure_relationship(
            db.session,
            "serving",
            source_id=app_comp.archimate_element_id,
            target_id=process.archimate_element_id,
        )


@event.listens_for(ApplicationProcessSupport, "after_delete")
def _on_app_process_delete(mapper, connection, target):
    """Clean up serving when ApplicationProcessSupport is deleted."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.process_data import BusinessProcess

    app_comp = db.session.get(ApplicationComponent, target.application_component_id)
    process = db.session.get(BusinessProcess, target.business_process_id)
    if app_comp and process and app_comp.archimate_element_id and process.archimate_element_id:
        _remove_relationship(
            db.session,
            "serving",
            app_comp.archimate_element_id,
            process.archimate_element_id,
        )


# ===========================================================================
# WAVE 2 LISTENERS
# ===========================================================================

# ---------------------------------------------------------------------------
# Listener 4: ProcessActorRaci -> assignment
# BusinessActor assigned to BusinessProcess
# ---------------------------------------------------------------------------

from app.models.relationship_tables import ProcessActorRaci  # noqa: E402


@event.listens_for(ProcessActorRaci, "after_insert")
def _on_actor_raci_insert(mapper, connection, target):
    """BusinessActor assigned to BusinessProcess."""
    from app.models.business_layer import BusinessActor
    from app.models.process_data import BusinessProcess

    actor = db.session.get(BusinessActor, target.actor_id)
    process = db.session.get(BusinessProcess, target.process_id)
    if actor and process and actor.archimate_element_id and process.archimate_element_id:
        _ensure_relationship(
            db.session,
            "assignment",
            source_id=actor.archimate_element_id,
            target_id=process.archimate_element_id,
        )


@event.listens_for(ProcessActorRaci, "after_delete")
def _on_actor_raci_delete(mapper, connection, target):
    """Clean up assignment when ProcessActorRaci is deleted."""
    from app.models.business_layer import BusinessActor
    from app.models.process_data import BusinessProcess

    actor = db.session.get(BusinessActor, target.actor_id)
    process = db.session.get(BusinessProcess, target.process_id)
    if actor and process and actor.archimate_element_id and process.archimate_element_id:
        _remove_relationship(
            db.session,
            "assignment",
            actor.archimate_element_id,
            process.archimate_element_id,
        )


# ---------------------------------------------------------------------------
# Listener 5: ProcessDataCrud -> access
# BusinessProcess accesses BusinessObject
# ---------------------------------------------------------------------------

from app.models.relationship_tables import ProcessDataCrud  # noqa: E402


@event.listens_for(ProcessDataCrud, "after_insert")
def _on_data_crud_insert(mapper, connection, target):
    """BusinessProcess accesses BusinessObject."""
    from app.models.business_layer import BusinessObject
    from app.models.process_data import BusinessProcess

    process = db.session.get(BusinessProcess, target.process_id)
    obj = db.session.get(BusinessObject, target.business_object_id)
    if process and obj and process.archimate_element_id and obj.archimate_element_id:
        _ensure_relationship(
            db.session,
            "access",
            source_id=process.archimate_element_id,
            target_id=obj.archimate_element_id,
            expected_organization_id=target.organization_id,
        )


@event.listens_for(ProcessDataCrud, "after_delete")
def _on_data_crud_delete(mapper, connection, target):
    """Clean up access when ProcessDataCrud is deleted."""
    from app.models.business_layer import BusinessObject
    from app.models.process_data import BusinessProcess

    process = db.session.get(BusinessProcess, target.process_id)
    obj = db.session.get(BusinessObject, target.business_object_id)
    if process and obj and process.archimate_element_id and obj.archimate_element_id:
        _remove_relationship(
            db.session,
            "access",
            process.archimate_element_id,
            obj.archimate_element_id,
        )


# ---------------------------------------------------------------------------
# Listener 6: ServiceDependency -> serving
# Provider BusinessService serves dependent BusinessService
# ---------------------------------------------------------------------------

from app.models.relationship_tables import ServiceDependency  # noqa: E402


@event.listens_for(ServiceDependency, "after_insert")
def _on_service_dependency_insert(mapper, connection, target):
    """Provider BusinessService serves dependent BusinessService."""
    from app.models.business_layer import BusinessService

    provider = db.session.get(BusinessService, target.provider_service_id)
    dependent = db.session.get(BusinessService, target.dependent_service_id)
    if (
        provider
        and dependent
        and provider.archimate_element_id
        and dependent.archimate_element_id
    ):
        _ensure_relationship(
            db.session,
            "serving",
            source_id=provider.archimate_element_id,
            target_id=dependent.archimate_element_id,
        )


@event.listens_for(ServiceDependency, "after_delete")
def _on_service_dependency_delete(mapper, connection, target):
    """Clean up serving when ServiceDependency is deleted."""
    from app.models.business_layer import BusinessService

    provider = db.session.get(BusinessService, target.provider_service_id)
    dependent = db.session.get(BusinessService, target.dependent_service_id)
    if (
        provider
        and dependent
        and provider.archimate_element_id
        and dependent.archimate_element_id
    ):
        _remove_relationship(
            db.session,
            "serving",
            provider.archimate_element_id,
            dependent.archimate_element_id,
        )


# ===========================================================================
# WAVE 3 LISTENERS
# ===========================================================================

# ---------------------------------------------------------------------------
# Listener 7: CapabilityActorRaci -> assignment
# BusinessActor assigned to BusinessCapability
# ---------------------------------------------------------------------------

from app.models.relationship_tables import CapabilityActorRaci  # noqa: E402


@event.listens_for(CapabilityActorRaci, "after_insert")
def _on_capability_actor_raci_insert(mapper, connection, target):
    """BusinessActor assigned to BusinessCapability."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.business_layer import BusinessActor

    actor = db.session.get(BusinessActor, target.actor_id)
    capability = db.session.get(BusinessCapability, target.capability_id)
    if (
        actor
        and capability
        and actor.archimate_element_id
        and capability.archimate_element_id
    ):
        _ensure_relationship(
            db.session,
            "assignment",
            source_id=actor.archimate_element_id,
            target_id=capability.archimate_element_id,
        )


@event.listens_for(CapabilityActorRaci, "after_delete")
def _on_capability_actor_raci_delete(mapper, connection, target):
    """Clean up assignment when CapabilityActorRaci is deleted."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.business_layer import BusinessActor

    actor = db.session.get(BusinessActor, target.actor_id)
    capability = db.session.get(BusinessCapability, target.capability_id)
    if (
        actor
        and capability
        and actor.archimate_element_id
        and capability.archimate_element_id
    ):
        _remove_relationship(
            db.session,
            "assignment",
            actor.archimate_element_id,
            capability.archimate_element_id,
        )


# ---------------------------------------------------------------------------
# Listener 8: InterfaceConsumer -> serving
# ApplicationInterface serves consuming ApplicationComponent
# Note: consumer_application_id is a direct FK to archimate_elements.id
# ---------------------------------------------------------------------------

from app.models.relationship_tables import InterfaceConsumer  # noqa: E402


@event.listens_for(InterfaceConsumer, "after_insert")
def _on_interface_consumer_insert(mapper, connection, target):
    """ApplicationInterface serves consuming ApplicationComponent."""
    from app.models.application_layer import ApplicationInterface

    interface = db.session.get(ApplicationInterface, target.interface_id)
    # consumer_application_id is already an archimate_elements FK
    consumer_element_id = target.consumer_application_id
    if interface and interface.archimate_element_id and consumer_element_id:
        _ensure_relationship(
            db.session,
            "serving",
            source_id=interface.archimate_element_id,
            target_id=consumer_element_id,
        )


@event.listens_for(InterfaceConsumer, "after_delete")
def _on_interface_consumer_delete(mapper, connection, target):
    """Clean up serving when InterfaceConsumer is deleted."""
    from app.models.application_layer import ApplicationInterface

    interface = db.session.get(ApplicationInterface, target.interface_id)
    consumer_element_id = target.consumer_application_id
    if interface and interface.archimate_element_id and consumer_element_id:
        _remove_relationship(
            db.session,
            "serving",
            interface.archimate_element_id,
            consumer_element_id,
        )


# ---------------------------------------------------------------------------
# Listener 9: DataObjectStorage -> access
# ApplicationComponent accesses BusinessObject
# Note: application_component_id is a direct FK to archimate_elements.id
# ---------------------------------------------------------------------------

from app.models.relationship_tables import DataObjectStorage  # noqa: E402


@event.listens_for(DataObjectStorage, "after_insert")
def _on_data_object_storage_insert(mapper, connection, target):
    """ApplicationComponent accesses BusinessObject."""
    from app.models.business_layer import BusinessObject

    # application_component_id is already an archimate_elements FK
    app_element_id = target.application_component_id
    obj = db.session.get(BusinessObject, target.business_object_id)
    if app_element_id and obj and obj.archimate_element_id:
        _ensure_relationship(
            db.session,
            "access",
            source_id=app_element_id,
            target_id=obj.archimate_element_id,
            expected_organization_id=target.organization_id,
        )


@event.listens_for(DataObjectStorage, "after_delete")
def _on_data_object_storage_delete(mapper, connection, target):
    """Clean up access when DataObjectStorage is deleted."""
    from app.models.business_layer import BusinessObject

    app_element_id = target.application_component_id
    obj = db.session.get(BusinessObject, target.business_object_id)
    if app_element_id and obj and obj.archimate_element_id:
        _remove_relationship(
            db.session,
            "access",
            app_element_id,
            obj.archimate_element_id,
        )


# ---------------------------------------------------------------------------
# Listener 10: ApplicationCapabilityMapping -> serving
# ApplicationComponent serves BusinessCapability
# ---------------------------------------------------------------------------

from app.models.application_capability import ApplicationCapabilityMapping  # noqa: E402


@event.listens_for(ApplicationCapabilityMapping, "after_insert")
def _on_app_capability_mapping_insert(mapper, connection, target):
    """ApplicationComponent serves BusinessCapability."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app_comp = db.session.get(ApplicationComponent, target.application_component_id)
    capability = db.session.get(BusinessCapability, target.business_capability_id)
    if (
        app_comp
        and capability
        and app_comp.archimate_element_id
        and capability.archimate_element_id
    ):
        _ensure_relationship(
            db.session,
            "serving",
            source_id=app_comp.archimate_element_id,
            target_id=capability.archimate_element_id,
            expected_organization_id=target.organization_id,
        )


@event.listens_for(ApplicationCapabilityMapping, "after_delete")
def _on_app_capability_mapping_delete(mapper, connection, target):
    """Clean up serving when ApplicationCapabilityMapping is deleted."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app_comp = db.session.get(ApplicationComponent, target.application_component_id)
    capability = db.session.get(BusinessCapability, target.business_capability_id)
    if (
        app_comp
        and capability
        and app_comp.archimate_element_id
        and capability.archimate_element_id
    ):
        _remove_relationship(
            db.session,
            "serving",
            app_comp.archimate_element_id,
            capability.archimate_element_id,
        )


# ---------------------------------------------------------------------------
# Backfill: one-time migration for existing junction rows
# ---------------------------------------------------------------------------


def backfill_relationships():
    """Create ArchiMateRelationship records for existing junction rows.

    Covers Wave 1, Wave 2, and Wave 3 junction tables, plus db.Table associations.
    Safe to run multiple times (idempotent via _ensure_relationship).
    Returns dict with per-table counts of new relationships created.
    """
    from app.models.application_layer import ApplicationInterface
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.business_layer import BusinessActor, BusinessObject, BusinessRole, BusinessService
    from app.models.process_data import BusinessProcess

    counts = {}

    # --- Wave 1 ---

    # 1. ServiceRealization -> realization
    n = 0
    for sr in ServiceRealization.query.all():
        p = db.session.get(BusinessProcess, sr.process_id)
        s = db.session.get(BusinessService, sr.service_id)
        if p and s and p.archimate_element_id and s.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "realization", p.archimate_element_id, s.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["ServiceRealization"] = n

    # 2. ProcessRoleRaci -> assignment
    n = 0
    for raci in ProcessRoleRaci.query.all():
        r = db.session.get(BusinessRole, raci.role_id)
        p = db.session.get(BusinessProcess, raci.process_id)
        if r and p and r.archimate_element_id and p.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "assignment", r.archimate_element_id, p.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["ProcessRoleRaci"] = n

    # 3. ApplicationProcessSupport -> serving
    n = 0
    for aps in ApplicationProcessSupport.query.all():
        a = db.session.get(ApplicationComponent, aps.application_component_id)
        p = db.session.get(BusinessProcess, aps.business_process_id)
        if a and p and a.archimate_element_id and p.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "serving", a.archimate_element_id, p.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["ApplicationProcessSupport"] = n

    # --- Wave 2 ---

    # 4. ProcessActorRaci -> assignment
    n = 0
    for raci in ProcessActorRaci.query.all():
        a = db.session.get(BusinessActor, raci.actor_id)
        p = db.session.get(BusinessProcess, raci.process_id)
        if a and p and a.archimate_element_id and p.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "assignment", a.archimate_element_id, p.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["ProcessActorRaci"] = n

    # 5. ProcessDataCrud -> access
    n = 0
    for crud in ProcessDataCrud.query.all():
        p = db.session.get(BusinessProcess, crud.process_id)
        o = db.session.get(BusinessObject, crud.business_object_id)
        if p and o and p.archimate_element_id and o.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "access", p.archimate_element_id, o.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["ProcessDataCrud"] = n

    # 6. ServiceDependency -> serving
    n = 0
    for dep in ServiceDependency.query.all():
        prov = db.session.get(BusinessService, dep.provider_service_id)
        depn = db.session.get(BusinessService, dep.dependent_service_id)
        if prov and depn and prov.archimate_element_id and depn.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "serving", prov.archimate_element_id, depn.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["ServiceDependency"] = n

    # --- Wave 3 ---

    # 7. CapabilityActorRaci -> assignment
    n = 0
    for raci in CapabilityActorRaci.query.all():
        a = db.session.get(BusinessActor, raci.actor_id)
        c = db.session.get(BusinessCapability, raci.capability_id)
        if a and c and a.archimate_element_id and c.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "assignment", a.archimate_element_id, c.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["CapabilityActorRaci"] = n

    # 8. InterfaceConsumer -> serving
    # consumer_application_id is a direct FK to archimate_elements.id
    n = 0
    for ic in InterfaceConsumer.query.all():
        iface = db.session.get(ApplicationInterface, ic.interface_id)
        consumer_elem_id = ic.consumer_application_id
        if iface and iface.archimate_element_id and consumer_elem_id:
            rel = _ensure_relationship(
                db.session, "serving", iface.archimate_element_id, consumer_elem_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["InterfaceConsumer"] = n

    # 9. DataObjectStorage -> access
    # application_component_id is a direct FK to archimate_elements.id
    n = 0
    for dos in DataObjectStorage.query.all():
        app_elem_id = dos.application_component_id
        obj = db.session.get(BusinessObject, dos.business_object_id)
        if app_elem_id and obj and obj.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "access", app_elem_id, obj.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["DataObjectStorage"] = n

    # --- db.Table associations (no real-time listeners, backfill only) ---

    # 10. actor_role_assignment -> assignment (BusinessActor -> BusinessRole)
    from app.models.relationship_tables import actor_role_assignment

    n = 0
    for row in db.session.execute(actor_role_assignment.select()).fetchall():  # tenant-exempt: event listener backfill
        a = db.session.get(BusinessActor, row.actor_id)
        r = db.session.get(BusinessRole, row.role_id)
        if a and r and a.archimate_element_id and r.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "assignment", a.archimate_element_id, r.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["actor_role_assignment"] = n

    # 11. ApplicationCapabilityMapping -> serving (ApplicationComponent -> BusinessCapability)
    n = 0
    for acm in ApplicationCapabilityMapping.query.all():
        a = db.session.get(ApplicationComponent, acm.application_component_id)
        c = db.session.get(BusinessCapability, acm.business_capability_id)
        if a and c and a.archimate_element_id and c.archimate_element_id:
            rel = _ensure_relationship(
                db.session, "serving", a.archimate_element_id, c.archimate_element_id
            )
            if rel and rel not in db.session.identity_map.values():
                n += 1
    counts["ApplicationCapabilityMapping"] = n

    db.session.commit()
    return counts


# Backward-compatible alias
backfill_wave1_relationships = backfill_relationships
