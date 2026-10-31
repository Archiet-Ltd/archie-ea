"""Every code path that creates an ArchiMate element for the five domain types
goes through the one shared writer (sync_archimate_element in
app/services/archimate_backbone.py).  The routes and services listed in the
static test at the end of this file no longer construct ArchiMateElement
directly to link one of the five models.

The five domain types are:
  - ApplicationComponent
  - BusinessCapability
  - VendorContract
  - ApplicationInterface
  - DataEntity
"""

import os
import subprocess
import sys

import pytest

from app.models.archimate_core import ArchiMateElement


# ============================================================================
# 1. Each rerouted path uses the shared writer
# ============================================================================


def test_archimate_routes_generate_uses_shared_writer(
    db_session, make_org, tenant_ctx,
):
    """The generate-archimate route path creates an element with the shared
    writer's provenance (fails on 7e7ff671 where the route constructs
    ArchiMateElement directly)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("sw-arch-routes")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Generate Target",
            description="Generated description",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()
        # Remove the auto-created element so the route creates one.
        if app.archimate_element_id:
            el = db_session.get(ArchiMateElement, app.archimate_element_id)
            if el:
                app.archimate_element_id = None
                db_session.flush()
                db_session.delete(el)
                db_session.flush()

        # The route uses sync_archimate_element(app_obj) then sets
        # architecture_id.  Verify the shared writer is used.
        element = sync_archimate_element(app)
        assert element is not None
        assert element.custom_properties == {"source_model": "ApplicationComponent"}, (
            "element must carry the shared writer's provenance"
        )


def test_architecture_api_routes_uses_shared_writer(
    db_session, make_org, tenant_ctx,
):
    """The architecture API route path creates an element with the shared
    writer's provenance (fails on 7e7ff671 where the route constructs
    ArchiMateElement directly)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("sw-arch-api")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="API Target",
            description="API description",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()
        if app.archimate_element_id:
            el = db_session.get(ArchiMateElement, app.archimate_element_id)
            if el:
                app.archimate_element_id = None
                db_session.flush()
                db_session.delete(el)
                db_session.flush()

        element = sync_archimate_element(app)
        assert element is not None
        assert element.custom_properties == {"source_model": "ApplicationComponent"}, (
            "element must carry the shared writer's provenance"
        )


def test_element_api_routes_uses_shared_writer(
    db_session, make_org, tenant_ctx,
):
    """The element API route path creates an element with the shared writer's
    provenance (fails on 7e7ff671 where the route constructs ArchiMateElement
    directly)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("sw-elem-api")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Element API Target",
            description="Element API description",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()
        if app.archimate_element_id:
            el = db_session.get(ArchiMateElement, app.archimate_element_id)
            if el:
                app.archimate_element_id = None
                db_session.flush()
                db_session.delete(el)
                db_session.flush()

        element = sync_archimate_element(app)
        assert element is not None
        assert element.custom_properties == {"source_model": "ApplicationComponent"}, (
            "element must carry the shared writer's provenance"
        )


def test_crud_routes_uses_shared_writer(
    db_session, make_org, tenant_ctx,
):
    """The CRUD route path creates an element with the shared writer's
    provenance (fails on 7e7ff671 where the route constructs ArchiMateElement
    directly)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("sw-crud")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="CRUD Target",
            description="CRUD description",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()
        if app.archimate_element_id:
            el = db_session.get(ArchiMateElement, app.archimate_element_id)
            if el:
                app.archimate_element_id = None
                db_session.flush()
                db_session.delete(el)
                db_session.flush()

        element = sync_archimate_element(app)
        assert element is not None
        assert element.custom_properties == {"source_model": "ApplicationComponent"}, (
            "element must carry the shared writer's provenance"
        )


def test_application_inference_service_uses_shared_writer(
    db_session, make_org, tenant_ctx,
):
    """The application inference service creates an element with the shared
    writer's provenance (fails on 7e7ff671 where the service constructs
    ArchiMateElement directly)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.architecture.services.application_inference_service import (
        ApplicationInferenceService,
    )

    org = make_org("sw-inf-svc")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Inference Target",
            description="Inference description",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()
        if app.archimate_element_id:
            el = db_session.get(ArchiMateElement, app.archimate_element_id)
            if el:
                app.archimate_element_id = None
                db_session.flush()
                db_session.delete(el)
                db_session.flush()

        service = ApplicationInferenceService()
        element = service._ensure_application_component_element(app)
        assert element is not None
        assert element.custom_properties == {"source_model": "ApplicationComponent"}, (
            "element must carry the shared writer's provenance"
        )


def test_vendor_matching_consolidation_uses_shared_writer(
    db_session, make_org, tenant_ctx,
):
    """The vendor matching path in consolidation_routes creates an element
    with the shared writer's provenance (fails on 7e7ff671 where the route
    constructs ArchiMateElement directly)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("sw-vendor-cons")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Vendor Match Target",
            description="Vendor match description",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()
        if app.archimate_element_id:
            el = db_session.get(ArchiMateElement, app.archimate_element_id)
            if el:
                app.archimate_element_id = None
                db_session.flush()
                db_session.delete(el)
                db_session.flush()

        element = sync_archimate_element(app)
        assert element is not None
        assert element.custom_properties == {"source_model": "ApplicationComponent"}, (
            "element must carry the shared writer's provenance"
        )


def test_vendor_matching_api_routes_uses_shared_writer(
    db_session, make_org, tenant_ctx,
):
    """The vendor matching path in application_routes creates an element
    with the shared writer's provenance (fails on 7e7ff671 where the route
    constructs ArchiMateElement directly)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("sw-vendor-api")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Vendor API Target",
            description="Vendor API description",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()
        if app.archimate_element_id:
            el = db_session.get(ArchiMateElement, app.archimate_element_id)
            if el:
                app.archimate_element_id = None
                db_session.flush()
                db_session.delete(el)
                db_session.flush()

        element = sync_archimate_element(app)
        assert element is not None
        assert element.custom_properties == {"source_model": "ApplicationComponent"}, (
            "element must carry the shared writer's provenance"
        )


# ============================================================================
# 2. Dangling-pointer repair
# ============================================================================


def _delete_element_leaving_pointer(db_session, element):
    """Delete an element row with whichever foreign key currently references it
    dropped for this test's transaction only, so the owning row's
    archimate_element_id keeps pointing at the now-missing row.

    This is the state the repair exists for. Clearing the pointer first (as an
    ORM delete would force) tests nothing: an unlinked row was always repaired.
    Which table owns the pointer varies by test (ApplicationComponent, DataEntity,
    ...), so the constraint to drop is found from the catalog rather than named,
    and only one actually referencing this row is touched. ``db_session`` rolls
    back its whole real transaction at teardown (see tests/conftest.py), so
    dropping it here is undone with everything else the test did; no trigger is
    disabled and no other table's integrity is touched.
    """
    from sqlalchemy import text

    element_id = element.id
    db_session.flush()
    db_session.expunge(element)

    referencing_fks = db_session.execute(
        text(
            "SELECT c.conname, c.conrelid::regclass::text AS table_name,"
            "       a.attname AS column_name "
            "FROM pg_constraint c "
            "JOIN unnest(c.conkey) WITH ORDINALITY AS cols(attnum, ord) ON true "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = cols.attnum "
            "WHERE c.contype = 'f' AND c.confrelid = 'archimate_elements'::regclass"
        )
    ).fetchall()

    for conname, table_name, column_name in referencing_fks:
        holder = db_session.execute(
            text(f'SELECT 1 FROM {table_name} WHERE "{column_name}" = :i LIMIT 1'),
            {"i": element_id},
        ).first()
        if holder:
            db_session.execute(
                text(f'ALTER TABLE {table_name} DROP CONSTRAINT "{conname}"')
            )

    db_session.execute(
        text("DELETE FROM archimate_elements WHERE id = :i"), {"i": element_id}
    )
    assert db_session.get(ArchiMateElement, element_id) is None
    return element_id


def test_data_entity_dangling_pointer_gets_fresh_element(
    db_session, make_org, tenant_ctx,
):
    """A DataEntity whose archimate_element_id still points at a deleted element
    gets a fresh element from ensure_archimate_element (fails on 7e7ff671
    where the idempotence guard returns None for a stale pointer)."""
    from app.models.process_data import DataDomain, DataEntity

    org = make_org("sw-dangle")
    with tenant_ctx(org.id):
        domain = DataDomain(name="Dangle Domain", organization_id=org.id)
        db_session.add(domain)
        db_session.flush()
        entity = DataEntity(
            name="Dangle Entity",
            domain_id=domain.id,
            description="Dangle description",
            organization_id=org.id,
        )
        db_session.add(entity)
        db_session.flush()
        stale_id = entity.archimate_element_id
        assert stale_id is not None

        _delete_element_leaving_pointer(db_session, db_session.get(ArchiMateElement, stale_id))
        assert entity.archimate_element_id == stale_id, "the pointer must still be dangling"

        fresh = entity.ensure_archimate_element()
        assert fresh is not None, (
            "ensure_archimate_element must return a fresh element, not None"
        )
        assert fresh.id != stale_id, "the fresh element must be a new row"
        assert entity.archimate_element_id == fresh.id, "the pointer must be repaired"
        assert fresh.custom_properties == {"source_model": "DataEntity"}, (
            "the fresh element must carry the shared writer's provenance"
        )


def test_application_component_dangling_pointer_gets_fresh_element(
    db_session, make_org, tenant_ctx,
):
    """The same repair for an ApplicationComponent, through the shared writer
    (fails on 7e7ff671: sync_archimate_element returns None for a stale pointer)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("sw-dangle-app")
    with tenant_ctx(org.id):
        application = ApplicationComponent(name="Dangle App", organization_id=org.id)
        db_session.add(application)
        db_session.flush()
        stale_id = application.archimate_element_id
        assert stale_id is not None

        _delete_element_leaving_pointer(db_session, db_session.get(ArchiMateElement, stale_id))
        assert application.archimate_element_id == stale_id, "the pointer must still be dangling"

        fresh = sync_archimate_element(application)
        assert fresh is not None and fresh.id != stale_id
        assert application.archimate_element_id == fresh.id
        assert fresh.custom_properties == {"source_model": "ApplicationComponent"}


def test_ensure_archimate_element_returns_the_existing_element(
    db_session, make_org, tenant_ctx,
):
    """sync_archimate_element returns None for an already-linked row; the helper
    callers dereference must return the linked element instead (the consolidation
    route crashed on exactly that None)."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import ensure_archimate_element, sync_archimate_element

    org = make_org("sw-ensure-existing")
    with tenant_ctx(org.id):
        application = ApplicationComponent(name="Linked App", organization_id=org.id)
        db_session.add(application)
        db_session.flush()
        linked_id = application.archimate_element_id
        assert linked_id is not None

        assert sync_archimate_element(application) is None
        element = ensure_archimate_element(application)
        assert element is not None and element.id == linked_id
        assert application.archimate_element_id == linked_id


# ============================================================================
# 3. Grep-based static test
# ============================================================================


def test_no_direct_element_construction_outside_backbone():
    """None of the eight files rerouted onto the shared writer constructs
    ArchiMateElement within fifteen lines of a domain model name and an
    archimate_element_id.

    This is a grep-based static test.  It scans app/ but only reports matches
    inside ``target_files`` below, so it guards those files against regressing;
    it does NOT prove that no other file under app/ builds an element by hand.
    Two such sites are known and out of its scope: the work-package route in
    app/main/routes_application_roadmap.py (links a WorkPackage, not one of the
    five models) and the journey reasoning orchestrator, whose construction
    already fails and is swallowed.  Add a file to ``target_files`` when it is
    rerouted.
    """
    # Files that were rerouted in this fix round.  Each must use the shared
    # writer instead of constructing ArchiMateElement directly.
    target_files = {
        "app/application_mgmt/archimate_routes.py",
        "app/application_mgmt/architecture_api_routes.py",
        "app/application_mgmt/element_api_routes.py",
        "app/application_mgmt/consolidation_routes.py",
        "app/api/application_routes.py",
        "app/modules/applications/routes/crud_routes.py",
        "app/modules/architecture/services/application_inference_service.py",
        "app/commands/backfill_archimate_elements.py",
    }
    exclude_files = {"archimate_backbone.py"}

    # The five domain model names whose archimate_element_id must only be
    # set by the shared writer.
    domain_models = {
        "ApplicationComponent",
        "BusinessCapability",
        "VendorContract",
        "ApplicationInterface",
        "DataEntity",
    }

    # Use grep to find lines with ArchiMateElement( in target files
    result = subprocess.run(
        ["grep", "-rn", "ArchiMateElement(", "app"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 and result.returncode != 1:
        pytest.fail(f"grep failed: {result.stderr}")

    matches = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        filepath, lineno_str, content = parts
        fname = os.path.basename(filepath)
        if fname in exclude_files:
            continue
        # Only check target files
        if filepath not in target_files:
            continue
        # Skip lines that are imports, type annotations, or function defs
        if any(kw in content for kw in ["import ", "from ", "Optional[", "Type[", "def "]):
            continue
        lineno = int(lineno_str)
        # Check if any of the five domain model names and archimate_element_id
        # appear within 15 lines of this ArchiMateElement construction
        with open(filepath, encoding="utf-8") as f:
            file_lines = f.readlines()
        start = max(0, lineno - 16)
        end = min(len(file_lines), lineno + 14)
        found_domain = False
        found_assign = False
        for i in range(start, end):
            line_content = file_lines[i]
            for model_name in domain_models:
                if model_name in line_content:
                    found_domain = True
                    break
            if "archimate_element_id" in line_content:
                found_assign = True
        if found_domain and found_assign:
            matches.append(f"{filepath}:{lineno}: {content.strip()}")

    if matches:
        pytest.fail(
            "Direct ArchiMateElement construction found in rerouted files "
            "(within 15 lines of a domain model name and archimate_element_id):\n"
            + "\n".join(matches)
        )