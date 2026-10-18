"""Data entities must mirror into the ArchiMate layer.

ArchiMate is the backbone, not a view (CLAUDE.md): every backend CREATE for an
architecturally meaningful entity is supposed to leave a matching
``ArchiMateElement`` row. ``DataEntity`` has carried an ``archimate_element_id``
column since it was added, but ``create_data_entity``
(app/modules/architecture/routes/data_architecture_routes.py) never populated
it — the entity catalog page listed data entities that the AI assistant,
reading only the ArchiMate layer, could not see. Same shape of bug as the
value-stream gap fixed in app/models/strategy_layer.py, and fixed the same
way: the shared flush listener in app/services/archimate_backbone.py now
creates elements on INSERT, and ``flask backfill-domain-elements`` repairs
rows written before the fix.

ArchiMate 3.2 has no "DataEntity" element type, so the mirror is the closest
3.2 fit: an Application-layer "DataObject" — see
app/services/archimate_backbone.py::ELEMENT_TYPES for the mapping.
"""

import uuid

from app.models.process_data import DataDomain, DataEntity


def _make_domain(db_session, label="Customer"):
    domain = DataDomain(name=f"{label} {uuid.uuid4().hex[:10]}")
    db_session.add(domain)
    db_session.flush()
    return domain


def _create_entity(db_session, domain, name="Customer"):
    entity = DataEntity(name=name, domain_id=domain.id)
    db_session.add(entity)
    db_session.flush()
    return entity


def test_create_data_entity_creates_linked_archimate_element(db_session, make_org, tenant_ctx):
    """ArchiMate is the backbone: the data entity mirrors as a DataObject element."""
    from app.models.models import ArchiMateElement

    org = make_org("data-entity-archimate")

    with tenant_ctx(org.id):
        domain = _make_domain(db_session)
        entity = _create_entity(db_session, domain, name="Invoice")

        assert entity.archimate_element_id is not None, (
            "no ArchiMateElement was created for the data entity; the AI "
            "assistant reads the ArchiMate layer and would report that no "
            "data entities exist"
        )

        element = db_session.get(ArchiMateElement, entity.archimate_element_id)

    assert element is not None
    assert element.name == "Invoice"
    # ArchiMate 3.2 has no "DataEntity" type — the closest fit is the
    # Application-layer DataObject.
    assert element.type == "DataObject"
    assert element.layer == "application"
    assert element.organization_id == org.id


def test_create_data_entity_without_archimate_element_id_is_not_overwritten(
    db_session, make_org, tenant_ctx
):
    """A caller that already links an element (e.g. via ensure_archimate_element)
    is left alone — the listener only fills a genuinely missing link."""
    from app.models.models import ArchiMateElement

    org = make_org("data-entity-archimate-preset")

    with tenant_ctx(org.id):
        domain = _make_domain(db_session)

        preset_element = ArchiMateElement(
            name="Preset Element", type="DataObject", layer="application"
        )
        db_session.add(preset_element)
        db_session.flush()

        entity = DataEntity(
            name="Preset Entity", domain_id=domain.id, archimate_element_id=preset_element.id
        )
        db_session.add(entity)
        db_session.flush()

        assert entity.archimate_element_id == preset_element.id


def test_backfill_links_data_entities_created_before_the_fix(db_session, make_org, tenant_ctx):
    """Rows written before the listener existed get their element retroactively."""
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.models import ArchiMateElement

    org = make_org("data-entity-backfill")

    with tenant_ctx(org.id):
        domain = _make_domain(db_session)

        # Reproduce a pre-fix row exactly: a Core insert fires no mapper events,
        # so it lands with archimate_element_id NULL.
        inserted = db_session.execute(
            DataEntity.__table__.insert().values(name="Order", domain_id=domain.id)
        )
        entity_id = inserted.inserted_primary_key[0]
        db_session.flush()

        stats = backfill_domain_elements(org.id, session=db_session)
        assert stats["DataEntity"]["created"] == 1

        db_session.expire_all()
        entity = db_session.get(DataEntity, entity_id)
        assert entity.archimate_element_id is not None

        element = db_session.get(ArchiMateElement, entity.archimate_element_id)
        assert element.name == "Order"
        assert element.type == "DataObject"
        assert element.layer == "application"
        assert element.organization_id == org.id

        # Idempotent: a second run finds nothing left to do and creates no duplicate.
        stats2 = backfill_domain_elements(org.id, session=db_session)
        assert stats2["DataEntity"]["created"] == 0


def test_backfill_dry_run_changes_nothing(db_session, make_org, tenant_ctx):
    """--dry-run reports the count without creating any ArchiMateElement."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("data-entity-backfill-dry-run")

    with tenant_ctx(org.id):
        domain = _make_domain(db_session)

        inserted = db_session.execute(
            DataEntity.__table__.insert().values(name="Shipment", domain_id=domain.id)
        )
        entity_id = inserted.inserted_primary_key[0]
        db_session.flush()

        stats = backfill_domain_elements(org.id, dry_run=True, session=db_session)
        assert stats["DataEntity"]["created"] == 0
        assert stats["DataEntity"]["scanned"] >= 1

        db_session.expire_all()
        entity = db_session.get(DataEntity, entity_id)
        assert entity.archimate_element_id is None
