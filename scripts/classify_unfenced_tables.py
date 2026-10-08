#!/usr/bin/env python
"""Classify every table in scripts/unfenced_tables.txt as TENANT_CHILD or GLOBAL.

Build-report-pr430-round4: mechanical, FK-tracing classification pass so the
write-guard allow-list (app/middleware/platform_write_allowlist.py) is built
from real foreign keys read off the live SQLAlchemy metadata, not guesses.

For every table name in scripts/unfenced_tables.txt, finds its mapped class
(there can be more than one mapper per table; any one is representative for
column/FK purposes) and classifies it:

  TENANT_CHILD  -- the table itself has an ``organization_id`` column, OR it
                   has a foreign key (direct, or through exactly one
                   intermediate join) to a table that itself has
                   ``organization_id`` or is a TenantMixin/HybridTenantMixin
                   model. The FK chain found is recorded so a human reviewer
                   can read the actual path, not just the verdict.
  GLOBAL        -- no such path was found within one hop. Needs a human
                   reason in the build report, not an allow-list entry.

Usage: python scripts/classify_unfenced_tables.py [--json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.tenant_table_registry import listed_table_names  # noqa: E402


def main() -> int:
    import app.models  # noqa: F401 -- populate the mapper registry
    # Not re-exported by app/models/__init__.py -- see tenant_table_registry.py.
    # adm_phase_approval first: adm_audit_log's ADMAuditLog.approval
    # relationship names it by string, and mapper configuration fails for
    # every mapper if that class was never imported by anything.
    import app.models.adm_phase_approval  # noqa: F401
    import app.models.adm_audit_log  # noqa: F401
    import app.models.adm_portfolio  # noqa: F401
    import app.models.adm_kanban_junctions  # noqa: F401
    from app import db
    from app.models.mixins.core import TenantMixin

    tenant_bases: tuple = (TenantMixin,)

    # table_name -> representative mapped class, for every mapped table
    # (including TenantMixin ones -- needed below as FK-chain targets).
    all_tables: dict[str, type] = {}
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        table = getattr(cls, "__table__", None)
        if table is None:
            continue
        all_tables.setdefault(table.name, cls)

    # "users" is deliberately excluded even though it has organization_id:
    # a FK to users.id (created_by_id, updated_by_id, reviewed_by_id, ...)
    # proves attribution ("who acted"), not that the CHILD table's rows are
    # themselves confined to that user's organisation's data -- a global
    # catalogue row can be "created_by" a particular user and still be
    # shared across every organisation. Proven wrong the expensive way: the
    # first version of this classifier counted vendor_organizations.
    # created_by_id -> users.id as proof of tenant-ownership, directly
    # contradicting that model's own docstring ("DELIBERATELY NOT
    # TenantMixin -- this is shared reference data") and its dedicated
    # pinned policy test (tests/test_vendor_tenancy_policy.py). A FK chain
    # through an actual business entity (applications, solutions,
    # requirements, archimate_elements, ...) is real evidence; a FK to
    # "whoever happened to click the button" is not.
    _ATTRIBUTION_ONLY_TABLES = {"users"}

    tenant_table_names: set[str] = set()
    for name, cls in all_tables.items():
        if name in _ATTRIBUTION_ONLY_TABLES:
            continue
        if issubclass(cls, tenant_bases):
            tenant_table_names.add(name)
        elif "organization_id" in cls.__table__.columns:
            # Has the column but not the mixin (e.g. predates it, or is
            # write-scoped by hand) -- still a tenant-owned table for FK-chain
            # purposes; a child FK'd to this is tenant-scoped too.
            tenant_table_names.add(name)

    wanted = sorted(listed_table_names())
    results = {}
    for name in wanted:
        cls = all_tables.get(name)
        if cls is None:
            results[name] = {"verdict": "NOT_FOUND", "reason": "no mapped class for this table name (renamed/dropped/typo)"}
            continue

        table = cls.__table__
        cols = table.columns

        if "organization_id" in cols:
            results[name] = {
                "verdict": "TENANT_CHILD",
                "reason": f"{name}.organization_id (own column)",
            }
            continue

        # Direct FK to a tenant table
        direct_hit = None
        for fk in table.foreign_keys:
            target_table = fk.column.table.name
            if target_table in tenant_table_names:
                direct_hit = (fk.parent.name, target_table, fk.column.name)
                break
        if direct_hit:
            col, target, target_col = direct_hit
            results[name] = {
                "verdict": "TENANT_CHILD",
                "reason": f"{name}.{col} -> {target}.{target_col} (direct FK to a tenant-owned table)",
            }
            continue

        # One-hop: FK to some other non-tenant table, which itself has a
        # tenant FK or organization_id. "users" is excluded here too, for
        # the same attribution-only reason as above -- a FK straight to
        # users.id is handled (and excluded) by the direct-FK check already,
        # but a FK to a table that FKs to users is exactly the same
        # attribution signal one hop removed and must not count either.
        one_hop_hit = None
        for fk in table.foreign_keys:
            mid_table_name = fk.column.table.name
            if mid_table_name in _ATTRIBUTION_ONLY_TABLES:
                continue
            mid_cls = all_tables.get(mid_table_name)
            if mid_cls is None:
                continue
            mid_table = mid_cls.__table__
            if "organization_id" in mid_table.columns:
                one_hop_hit = (fk.parent.name, mid_table_name, "organization_id")
                break
            for fk2 in mid_table.foreign_keys:
                target_table = fk2.column.table.name
                if target_table in tenant_table_names:
                    one_hop_hit = (fk.parent.name, mid_table_name, f"{fk2.parent.name}->{target_table}.{fk2.column.name}")
                    break
            if one_hop_hit:
                break
        if one_hop_hit:
            col, mid, chain = one_hop_hit
            results[name] = {
                "verdict": "TENANT_CHILD",
                "reason": f"{name}.{col} -> {mid} -> ({chain}) (one-hop FK chain to a tenant-owned table)",
            }
            continue

        results[name] = {
            "verdict": "GLOBAL",
            "reason": "no organization_id column and no FK chain (<=1 hop) to a tenant-owned table found",
        }

    if "--json" in sys.argv:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        tenant_child = sorted(k for k, v in results.items() if v["verdict"] == "TENANT_CHILD")
        global_cat = sorted(k for k, v in results.items() if v["verdict"] == "GLOBAL")
        not_found = sorted(k for k, v in results.items() if v["verdict"] == "NOT_FOUND")
        print(f"TENANT_CHILD: {len(tenant_child)}")
        print(f"GLOBAL: {len(global_cat)}")
        print(f"NOT_FOUND: {len(not_found)}")
        print()
        for name in tenant_child:
            print(f"TENANT_CHILD\t{name}\t{results[name]['reason']}")
        for name in global_cat:
            print(f"GLOBAL\t{name}\t{results[name]['reason']}")
        for name in not_found:
            print(f"NOT_FOUND\t{name}\t{results[name]['reason']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
