"""`ensure_capabilities_seeded` must create every new row through
`ensure_capability_record` (the one creator for `UnifiedCapability`
documented in `app/modules/capabilities/services/capability_service.py`),
never by instantiating the model directly.

Uses a small, test-only catalog (monkeypatched over the real
`CATALOG_ROOT`) with no `FunctionSpec` entries, so these tests exercise
exactly the capability-creation path this fix changes
(`get_or_create_capability` in
`app/modules/applications/services/application_capability_catalog.py`)
without tripping over the real catalog's unrelated, pre-existing
`BusinessFunction.capability_id` foreign key (it points at the legacy
`business_capability` table, not `unified_capabilities` -- a separate
defect this brief does not cover).

Uses the shared `db_session` fixture from `tests/conftest.py`: the whole
test runs inside a transaction that is always rolled back, so it is safe
against the shared, persistent test database.
"""

from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.usefixtures("db_session")


def _fixture_catalog_root():
    """A small two-level capability tree, no functions, unique names per call."""
    from app.modules.applications.services.application_capability_catalog import CapabilitySpec

    suffix = uuid.uuid4().hex[:10]
    leaf = CapabilitySpec(
        name=f"Canonical Writer Test Leaf {suffix}",
        description="Test-only leaf capability.",
        level=2,
        domain="Experience",
        category="Core",
        capability_type="core",
        children=[],
        functions=[],
    )
    root = CapabilitySpec(
        name=f"Canonical Writer Test Root {suffix}",
        description="Test-only root capability.",
        level=1,
        domain="Experience",
        category="Core",
        capability_type="core",
        children=[leaf],
        functions=[],
    )
    return root


def _spec_names(spec):
    yield spec.name
    for child in spec.children:
        yield from _spec_names(child)


def test_ensure_capabilities_seeded_creates_every_new_row_through_ensure_capability_record(
    monkeypatch,
):
    from app.models.unified_capability import UnifiedCapability
    from app.modules.applications.services import application_capability_catalog as catalog_module
    from app.modules.capabilities.services import capability_service

    fixture_root = _fixture_catalog_root()
    monkeypatch.setattr(catalog_module, "CATALOG_ROOT", fixture_root)

    real_ensure_capability_record = capability_service.ensure_capability_record
    created_names: list[str] = []

    def _spy(*args, **kwargs):
        capability, created = real_ensure_capability_record(*args, **kwargs)
        if created:
            created_names.append(capability.name)
        return capability, created

    # get_or_create_capability's creation branch does a deferred
    # ``from app.modules.capabilities.services.capability_service import
    # ensure_capability_record`` on every call, so patching the attribute on
    # the source module -- rather than on `catalog_module`, which never binds
    # the name at module scope -- is what actually intercepts the call.
    monkeypatch.setattr(capability_service, "ensure_capability_record", _spy)

    fixture_names = set(_spec_names(fixture_root))
    before = UnifiedCapability.query.filter(UnifiedCapability.name.in_(fixture_names)).count()
    assert before == 0

    catalog_module.ensure_capabilities_seeded()

    after = UnifiedCapability.query.filter(UnifiedCapability.name.in_(fixture_names)).count()

    assert set(created_names) == fixture_names
    # Every row actually added to the table during this call is accounted for
    # by a call to ensure_capability_record. A bypassing direct
    # UnifiedCapability(...) constructor call would grow the table without
    # ever appearing in created_names, so this delta would be larger than
    # len(created_names) if the bypass this fixes ever came back.
    assert after - before == len(created_names)

    rows = UnifiedCapability.query.filter(UnifiedCapability.name.in_(created_names)).all()
    assert {row.name for row in rows} == fixture_names
    # ensure_capability_record is also the only place that stamps `scope`
    # (app/modules/capabilities/services/capability_service.py). The
    # before_flush listener that would otherwise backfill it
    # (_protect_reference_capability_writes in app/models/unified_capability.py)
    # only fires inside a tenant request context, which this seeding call is
    # not, so a row left with scope IS NULL here could only have come from a
    # direct constructor call that skipped ensure_capability_record entirely.
    assert all(row.scope == "reference" for row in rows)
    assert all(row.organization_id is None for row in rows)

    leaf_row = next(row for row in rows if row.name.startswith("Canonical Writer Test Leaf"))
    root_row = next(row for row in rows if row.name.startswith("Canonical Writer Test Root"))
    assert leaf_row.parent_capability_id == root_row.id


def test_ensure_capabilities_seeded_is_idempotent_and_still_canonical_on_rerun(monkeypatch):
    """A second run against rows the first run already created must update in
    place, not create a second row -- and still never bypass
    ensure_capability_record for whatever it does create."""

    from app.models.unified_capability import UnifiedCapability
    from app.modules.applications.services import application_capability_catalog as catalog_module
    from app.modules.capabilities.services import capability_service

    fixture_root = _fixture_catalog_root()
    monkeypatch.setattr(catalog_module, "CATALOG_ROOT", fixture_root)
    fixture_names = set(_spec_names(fixture_root))

    catalog_module.ensure_capabilities_seeded()
    first_run_count = UnifiedCapability.query.filter(UnifiedCapability.name.in_(fixture_names)).count()
    assert first_run_count == len(fixture_names)

    real_ensure_capability_record = capability_service.ensure_capability_record
    create_calls: list[str] = []

    def _spy(*args, **kwargs):
        capability, created = real_ensure_capability_record(*args, **kwargs)
        if created:
            create_calls.append(capability.name)
        return capability, created

    monkeypatch.setattr(capability_service, "ensure_capability_record", _spy)

    catalog_module.ensure_capabilities_seeded()

    second_run_count = UnifiedCapability.query.filter(UnifiedCapability.name.in_(fixture_names)).count()
    assert second_run_count == first_run_count
    assert create_calls == [], "a rerun over already-seeded names must not create anything"
