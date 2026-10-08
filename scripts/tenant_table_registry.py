"""Single source of truth: the runtime set of non-tenant-fenced table names.

PR 430 review v2 (D-07): the write-guard's enumeration ratchet
(``tests/test_platform_write_guard_enumeration.py``) and the read-side ratchet
(``scripts/check_untenanted_reads.py``) each had their own, independent way of
answering "which tables have no tenant fence" -- one walked the live
SQLAlchemy mapper registry, the other parsed model source with ``ast``. They
drifted (the committed ledger, ``scripts/unfenced_tables.txt``, held 477
names; the registry walk below found 482; the static scanner found a
different 467) because nothing ever compared them.

This module is the one place that walks the live mapper registry. Both the
write-guard enumeration test and ``scripts/classify_unfenced_tables.py`` call
it instead of each keeping their own copy, and
``tests/test_tenant_table_registry_sync.py`` asserts this module's answer,
the static scanner's answer, and ``scripts/unfenced_tables.txt`` all name the
same set of tables -- so a future drift fails a test immediately instead of
silently diverging again.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TABLE_LIST = REPO / "scripts" / "unfenced_tables.txt"


def non_tenant_table_classes() -> dict[str, type]:
    """{table_name: one representative mapped class} for every non-fenced model.

    "Non-fenced" means precisely what ``tenant_isolation.py``'s guard means by
    it: not a ``TenantMixin`` subclass. A ``HybridTenantMixin`` /
    ``HybridCapabilityTenantMixin`` model (``app/models/framework.py``,
    ``app/models/unified_capability.py`` and others -- a *different*,
    nullable-organization_id tenant-fencing mechanism with its own
    ``do_orm_execute``/``before_flush`` listeners) is NOT a ``TenantMixin``
    subclass either, so the guard does not know to exempt it -- this registry
    does not pretend otherwise. Its tables still need a column/FK-chain-based
    classification and, where that's TENANT_CHILD, an allow-list entry, same
    as any other non-``TenantMixin`` table (see
    ``build-report-pr430-round4-v1.md``'s unified_capabilities finding).

    Imports every model module reachable from ``app.models`` plus the three
    modules that package's own ``__init__.py`` does not import (see the
    comment at each import below) so this is the complete mapper registry, not
    a partial one.
    """
    import app.models  # noqa: F401 -- populate the registry for every re-exported module

    # Not re-exported by app/models/__init__.py, so a bare `import app.models`
    # never registers their mappers, and neither this registry nor (before
    # this fix) the write-guard enumeration test ever saw these tables at
    # all. adm_phase_approval must be imported BEFORE adm_audit_log: the
    # latter's `ADMAuditLog.approval` relationship names 'ADMPhaseApproval'
    # as a string, and SQLAlchemy configures every currently-registered
    # mapper together on first use -- if that class were never imported by
    # anything, mapper configuration would fail for the WHOLE app the next
    # time any query ran, not just for this registry's walk.
    import app.models.adm_phase_approval  # noqa: F401
    import app.models.adm_audit_log  # noqa: F401
    import app.models.adm_portfolio  # noqa: F401
    import app.models.adm_kanban_junctions  # noqa: F401

    from app import db
    from app.models.mixins.core import TenantMixin

    out: dict[str, type] = {}
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, TenantMixin):
            continue
        table = getattr(cls, "__table__", None)
        if table is None:
            continue
        # Joined/single-table inheritance can register more than one mapped
        # class for the same table; one representative is enough.
        out.setdefault(table.name, cls)
    return out


def listed_table_names() -> set[str]:
    """The committed ledger, scripts/unfenced_tables.txt, as a name set."""
    names = set()
    for line in TABLE_LIST.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line)
    return names
