"""Single source of truth for "capability coverage %" across dashboard surfaces.

4 Sep 2026: the CTO-tab hero health-score panel (`dashboard_views.py::overview`)
and the lazy-loaded Executive Summary fragment
(`ExecutiveDashboardService._get_capability_coverage`) both labelled a number
"Capability Coverage %" and fed it into an identically-weighted "Health Score"
composite (40% phase maturity / 30% risk / 20% capability coverage / 10%
governance) — but computed it from two different tables: the hero counted L1
capabilities with >=1 mapped ApplicationCapabilityMapping row, the fragment
counted capabilities with >=1 mapped SolutionCapabilityMapping row (a much
sparser relationship). Same label, same weight, same page, genuinely different
number (100% vs 0%, observed live) — an ADR-0008 violation for a computed
metric rather than a stored table. This module is the fix: one query, two
callers.
"""

from __future__ import annotations

from app import db


def compute_l1_capability_coverage() -> dict:
    """Percentage of L1 business capabilities with at least one mapped
    application, rolled up through their sub-capability subtree.

    Returns {"total": int, "covered": int, "percentage": float|None}.
    `percentage` is None when there are no L1 capabilities to measure against
    (0 of 0 is undefined, not 0% coverage) or the query itself fails.
    """
    try:
        from app.models.application_capability import ApplicationCapabilityMapping
        from app.models.business_capability import BusinessCapability

        l1_caps = (
            BusinessCapability.query
            .filter(BusinessCapability.level == 1)
            .order_by(BusinessCapability.name)
            .all()
        )
        if not l1_caps:
            return {"total": 0, "covered": 0, "percentage": None}

        all_caps = {c.id: c for c in BusinessCapability.query.all()}
        children: dict = {}
        for cid, c in all_caps.items():
            pid = getattr(c, "parent_capability_id", None)
            if pid:
                children.setdefault(pid, []).append(cid)

        def _subtree_ids(root_id: int) -> list:
            ids, stack = [], [root_id]
            while stack:
                cur = stack.pop()
                ids.append(cur)
                stack.extend(children.get(cur, []))
            return ids

        # One query for every capability that has a mapped application, then each L1 asks its own subtree;
        # this used to count the mappings of every L1 subtree with a query apiece.
        # tenant-scoping-ok: scoped via TenantMixin FK parent BusinessCapability,
        # ApplicationCapabilityMapping.organization_id is NULL in prod (see e622d36).
        mapped_ids = {
            row[0]
            for row in db.session.query(ApplicationCapabilityMapping.business_capability_id)
            .filter(ApplicationCapabilityMapping.business_capability_id.in_(list(all_caps)))
            .distinct()
            .all()
        }
        covered = sum(1 for cap in l1_caps if mapped_ids.intersection(_subtree_ids(cap.id)))

        total = len(l1_caps)
        return {
            "total": total,
            "covered": covered,
            "percentage": round((covered / total) * 100, 1) if total else None,
        }
    except Exception:
        db.session.rollback()
        return {"total": None, "covered": None, "percentage": None}
