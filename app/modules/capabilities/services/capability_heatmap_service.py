"""
DEPRECATED: Import from app.modules.capabilities.services instead.
-> app.modules.capabilities.services.analysis_service

Capability Heatmap Service

Provides aggregated data for capability maturity heatmaps,
gap alerts, and domain health indicators on the framework dashboard.
"""

import logging
import time
from typing import Any, Dict, List

from sqlalchemy import event, func

from app import db
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import BusinessDomain, UnifiedCapability

logger = logging.getLogger(__name__)


# ============================================================================
# QUERY PROFILING (Phase 2: Performance Baseline)
# ============================================================================
class QueryCounter:
    """Count SQL queries executed during service calls."""
    
    def __init__(self):
        self.count = 0
        self.queries = []
    
    def reset(self):
        """Reset counter."""
        self.count = 0
        self.queries = []
    
    def on_before_execute(self, conn, cursor, statement, parameters, context, executemany):
        """Track query execution. Matches SQLAlchemy before_cursor_execute signature."""
        self.count += 1
        query_str = str(statement).replace('\n', ' ')[:100]
        self.queries.append(query_str)
    
    def start(self):
        """Start tracking queries."""
        self.reset()
        event.listen(db.engine, "before_cursor_execute", self.on_before_execute)
    
    def stop(self):
        """Stop tracking queries."""
        event.remove(db.engine, "before_cursor_execute", self.on_before_execute)
    
    def report(self, method_name: str, elapsed_time: float):
        """Log query statistics."""
        logger.info(f"\n{'='*70}")
        logger.info(f"HEATMAP SERVICE: {method_name}")
        logger.info(f"{'='*70}")
        logger.info(f"⏱️  Elapsed: {elapsed_time:.2f}s")
        logger.info(f"📊 Queries: {self.count}")
        logger.info("")
        for i, q in enumerate(self.queries[:5], 1):
            logger.info(f"  {i}. {q}...")
        if len(self.queries) > 5:
            logger.info(f"  ... and {len(self.queries) - 5} more queries")
        logger.info(f"{'='*70}\n")


query_counter = QueryCounter()


class CapabilityHeatmapService:
    """Aggregation service for capability maturity heatmaps and gap analysis."""

    _LEGEND = [
        {"level": 1, "label": "Initial", "color": "#ef4444"},
        {"level": 2, "label": "Managed", "color": "#f97316"},
        {"level": 3, "label": "Defined", "color": "#eab308"},
        {"level": 4, "label": "Quantitatively Managed", "color": "#84cc16"},
        {"level": 5, "label": "Optimizing", "color": "#22c55e"},
    ]

    @classmethod
    def _legend(cls) -> List[Dict[str, Any]]:
        """A fresh copy every call, so a caller mutating the returned list or
        one of its entries cannot corrupt every later response in the process."""
        return [dict(item) for item in cls._LEGEND]

    def get_maturity_heatmap(self) -> Dict[str, Any]:
        """
        Build maturity heatmap data: domains (rows) x maturity levels 1 - 5 (columns).

        Each cell contains the count and names of capabilities at that maturity level.
        Each domain row includes an overall health score.

        Every figure here is either a value actually recorded, or ``None`` with a
        reason code — never an invented Level 1 / Level 3 / zero. T-002 (ADR 0008
        rule 3): ``current_maturity_level`` / ``target_maturity_level`` are read
        as-is, with no ``or 1`` / ``or 3`` fallback. A capability with no
        ``domain_id`` is kept (outer join) and grouped separately rather than
        silently dropped.

        Returns:
            Dict with a ``domains`` list (each with counts, names, averages and
            a denominator per average), a ``legend``, an ``unassessed_legend``,
            summary totals, ``capabilities_without_domain``, and a
            ``tenant_reason_code`` that is ``None`` for a normal, tenant-scoped
            result or ``"no_tenant_context"`` for the fail-closed empty result
            below.
        """
        from app.modules.intelligence.services.reason_codes import validate_reason_code
        from app.utils.tenant_sql import current_org_id

        # PHASE 2: Query profiling - measure execution time and queries
        query_counter.start()
        start_time = time.time()

        try:
            organization_id = current_org_id()
            if organization_id is None:
                # Fail closed: no resolvable tenant means no tenant's rows,
                # not every tenant's rows.
                return {
                    "domains": [],
                    "legend": self._legend(),
                    "total_capabilities": 0,
                    "total_domains": 0,
                    "capabilities_without_domain": 0,
                    "unassessed_legend": {
                        "label": "Not assessed",
                        "reason_code": validate_reason_code("no_maturity_recorded"),
                    },
                    "tenant_reason_code": validate_reason_code("no_tenant_context"),
                }

            # The population read states its own organisation predicate — the
            # same shared-reference rule as UnifiedCapability.visible_to_organization
            # — rather than depending on the ambient do_orm_execute listener alone.
            visibility = UnifiedCapability.visibility_predicate(organization_id)

            # Try UnifiedCapability first (empty), fall back to BusinessCapability
            # (with Abacus data). Outer join: a capability with no domain_id is
            # kept, not silently dropped.
            capabilities = (
                db.session.query(UnifiedCapability, BusinessDomain.name, BusinessDomain.code)
                .outerjoin(BusinessDomain, UnifiedCapability.domain_id == BusinessDomain.id)
                .filter(visibility)
                .all()
            )

            # Fallback: if UnifiedCapability is empty, try BusinessCapability.
            # Left in place: removing it is a larger, separate change (it would
            # alter visible content for any deployment still populating
            # capabilities through BusinessCapability). It is a TenantMixin
            # model, already scoped by its own listener.
            if not capabilities:
                try:
                    from app.models.business_capabilities import BusinessCapability
                    logger.info("UnifiedCapability empty, falling back to BusinessCapability for heatmap")

                    # BusinessCapability has domain name stored directly - no need to join
                    cap_data = db.session.query(BusinessCapability).all()

                    # Transform to same format as UnifiedCapability results
                    # (cap object, domain_name, domain_code)
                    capabilities = [(cap, cap.business_domain or "Unknown", cap.code or "UNK") for cap in cap_data]

                except (ImportError, Exception) as e:
                    logger.warning(f"BusinessCapability fallback failed: {e}, using empty result")
                    capabilities = []

            # Group by domain. ``key is None`` is the no-domain group.
            domain_map: Dict[Any, Dict[str, Any]] = {}
            for cap, domain_name, domain_code in capabilities:
                key = domain_code
                if key not in domain_map:
                    domain_map[key] = {
                        "name": domain_name if domain_code is not None else "No domain",
                        "code": domain_code,
                        "has_domain": domain_code is not None,
                        "capabilities_by_maturity": {1: [], 2: [], 3: [], 4: [], 5: []},
                        "maturity_values": [],
                        "target_values": [],
                        "unassessed": [],
                        "total": 0,
                    }
                entry = domain_map[key]
                entry["total"] += 1

                raw_maturity = cap.current_maturity_level
                if raw_maturity is None:
                    # Unrecorded: never counted or rendered as Level 1.
                    entry["unassessed"].append({"id": cap.id, "name": cap.name})
                else:
                    # The clamp below folds a recorded out-of-range level into
                    # a valid bucket; it is a separate, pre-existing concern
                    # from an unrecorded value, and is only reached here for a
                    # value that was actually recorded.
                    maturity = max(1, min(5, raw_maturity))
                    entry["capabilities_by_maturity"][maturity].append(
                        {"id": cap.id, "name": cap.name}
                    )
                    entry["maturity_values"].append(maturity)

                if cap.target_maturity_level is not None:
                    entry["target_values"].append(cap.target_maturity_level)

            # Build domain rows with health scores. Real domains first, sorted
            # by code as today; the no-domain group (key None) last.
            domains = [
                self._build_domain_row(domain_map[code], validate_reason_code)
                for code in sorted(k for k in domain_map.keys() if k is not None)
            ]
            capabilities_without_domain = 0
            if None in domain_map:
                no_domain_row = self._build_domain_row(domain_map[None], validate_reason_code)
                capabilities_without_domain = no_domain_row["total_capabilities"]
                domains.append(no_domain_row)

            result = {
                "domains": domains,
                "legend": self._legend(),
                "total_capabilities": len(capabilities),
                "total_domains": len([d for d in domains if d["has_domain"]]),
                "capabilities_without_domain": capabilities_without_domain,
                "unassessed_legend": {
                    "label": "Not assessed",
                    "reason_code": validate_reason_code("no_maturity_recorded"),
                },
                "tenant_reason_code": None,
            }

            return result

        finally:
            # PHASE 2: Report query statistics
            elapsed = time.time() - start_time
            query_counter.stop()
            query_counter.report("get_maturity_heatmap", elapsed)

    @staticmethod
    def _build_domain_row(d: Dict[str, Any], validate_reason_code) -> Dict[str, Any]:
        """One domain's (or the no-domain group's) row, honest about absence."""

        maturity_vals = d["maturity_values"]
        target_vals = d["target_values"]

        avg_maturity = sum(maturity_vals) / len(maturity_vals) if maturity_vals else None
        avg_target = sum(target_vals) / len(target_vals) if target_vals else None

        # Health score: ratio of current to target maturity (0 - 100), only
        # when both averages exist and the target average is greater than
        # zero. Never 0 as a stand-in for "not computable".
        if avg_maturity is not None and avg_target is not None and avg_target > 0:
            health_score = min(round((avg_maturity / avg_target) * 100, 1), 100)
        else:
            health_score = None

        counts = {}
        names = {}
        for level in range(1, 6):
            caps_at_level = d["capabilities_by_maturity"][level]
            counts[level] = len(caps_at_level)
            names[level] = [c["name"] for c in caps_at_level]

        return {
            "name": d["name"],
            "code": d["code"],
            "has_domain": d["has_domain"],
            "counts": counts,
            "capability_names": names,
            "avg_maturity": round(avg_maturity, 1) if avg_maturity is not None else None,
            "avg_target": round(avg_target, 1) if avg_target is not None else None,
            "health_score": health_score,
            "total_capabilities": d["total"],
            "unassessed_count": len(d["unassessed"]),
            "unassessed_capability_names": [c["name"] for c in d["unassessed"]],
            "avg_maturity_denominator": len(maturity_vals),
            "avg_target_denominator": len(target_vals),
            "reason_code": (
                validate_reason_code("no_maturity_recorded") if avg_maturity is None else None
            ),
        }

    def get_gap_alerts(self) -> Dict[str, Any]:
        """
        Detect capabilities with gaps requiring attention.

        Three alert categories:
        - Unmapped: capabilities with zero application mappings
        - Low coverage: capabilities where average coverage < 50%
        - Critical maturity gaps: current maturity 2+ levels below target

        Returns:
            Dict with unmapped, low_coverage, maturity_gaps lists and summary.
        """
        # 1. Unmapped capabilities (no entries in mapping table)
        mapped_ids_subq = (
            db.session.query(UnifiedApplicationCapabilityMapping.unified_capability_id)
            .distinct()
            .subquery()
        )

        unmapped_caps = (
            db.session.query(UnifiedCapability, BusinessDomain.name)
            .join(BusinessDomain, UnifiedCapability.domain_id == BusinessDomain.id)
            .filter(~UnifiedCapability.id.in_(db.session.query(mapped_ids_subq)))
            .order_by(UnifiedCapability.strategic_importance.desc())
            .all()
        )

        unmapped = []
        for cap, domain_name in unmapped_caps:
            unmapped.append(
                {
                    "id": cap.id,
                    "name": cap.name,
                    "domain": domain_name,
                    "level": cap.level,
                    "strategic_importance": cap.strategic_importance or "medium",
                    "business_criticality": cap.business_criticality or "supporting",
                }
            )

        # 2. Low coverage capabilities (average coverage_percentage < 50%)
        low_coverage_data = (
            db.session.query(
                UnifiedCapability.id,
                UnifiedCapability.name,
                BusinessDomain.name.label("domain_name"),
                func.avg(UnifiedApplicationCapabilityMapping.coverage_percentage).label(
                    "avg_coverage"
                ),
                func.count(UnifiedApplicationCapabilityMapping.id).label("app_count"),
            )
            .join(BusinessDomain, UnifiedCapability.domain_id == BusinessDomain.id)
            .join(
                UnifiedApplicationCapabilityMapping,
                UnifiedApplicationCapabilityMapping.unified_capability_id == UnifiedCapability.id,
            )
            .group_by(UnifiedCapability.id, UnifiedCapability.name, BusinessDomain.name)
            .having(func.avg(UnifiedApplicationCapabilityMapping.coverage_percentage) < 50)
            .order_by(func.avg(UnifiedApplicationCapabilityMapping.coverage_percentage))
            .all()
        )

        low_coverage = []
        for cap_id, cap_name, domain_name, avg_cov, app_count in low_coverage_data:
            low_coverage.append(
                {
                    "id": cap_id,
                    "name": cap_name,
                    "domain": domain_name,
                    "coverage_avg": round(float(avg_cov or 0), 1),
                    "app_count": app_count,
                }
            )

        # 3. Critical maturity gaps (current 2+ levels below target), read
        # through maturity_for_capability_ids (ADR-MAT-1) so an absence is
        # never rendered as a fabricated zero (ADR-MAT-2). Fails closed with
        # no resolvable tenant, the same shape #91 gave get_maturity_heatmap:
        # no tenant means no tenant's rows, not every tenant's rows.
        from app.modules.intelligence.services.reason_codes import validate_reason_code
        from app.utils.tenant_sql import current_org_id

        organization_id = current_org_id()
        if organization_id is None:
            maturity_gaps = []
            maturity_gap_reason = validate_reason_code("no_tenant_context")
        else:
            # Strict: a shared catalogue row cannot carry this tenant's gap.
            gap_population = (
                db.session.query(UnifiedCapability.id, UnifiedCapability.name, BusinessDomain.name)
                .outerjoin(BusinessDomain, UnifiedCapability.domain_id == BusinessDomain.id)
                .filter(UnifiedCapability.organization_id == organization_id)
                .all()
            )
            identity_by_id = {
                cap_id: (cap_name, domain_name) for cap_id, cap_name, domain_name in gap_population
            }
            blocks = self.maturity_for_capability_ids(
                list(identity_by_id.keys()), organization_id=organization_id
            )
            rows = []
            for cap_id, block in blocks.items():
                if block["under_target"] is True and block["target_gap"] >= 2:
                    cap_name, domain_name = identity_by_id[cap_id]
                    rows.append(
                        {
                            "id": cap_id,
                            "name": cap_name,
                            "domain": domain_name,
                            "current": block["current"],
                            "target": block["target"],
                            "gap": block["target_gap"],
                        }
                    )
            maturity_gaps = sorted(rows, key=lambda row: row["gap"], reverse=True)
            maturity_gap_reason = None

        # Count critical items (strategic_importance=critical or business_criticality=mission_critical)
        critical_unmapped = sum(
            1
            for u in unmapped
            if u["strategic_importance"] == "critical"
            or u["business_criticality"] == "mission_critical"
        )

        return {
            "unmapped": unmapped,
            "low_coverage": low_coverage,
            "maturity_gaps": maturity_gaps,
            "summary": {
                "unmapped_count": len(unmapped),
                "low_coverage_count": len(low_coverage),
                "maturity_gap_count": len(maturity_gaps),
                "maturity_gap_reason": maturity_gap_reason,
                "critical_gaps": critical_unmapped,
                "total_alerts": len(unmapped) + len(low_coverage) + len(maturity_gaps),
            },
        }

    # ------------------------------------------------------------------ #
    # ADR-MAT-1: the one maturity read for every engine.
    #
    # Every reader that needs a capability's maturity -- current, target,
    # whether it was assessed at all, whether it is under target, and by how
    # much -- calls one of the two methods below rather than reading the
    # authority's own current/target maturity columns off ``UnifiedCapability``
    # directly, or either of the two source-provenance accessors. Both are
    # batched: two SELECTs regardless of how many ids are asked for, never one
    # per id, never a query on an empty input.
    #
    # The block returned per id, always exactly these ten keys:
    #     capability_id, element_id, current, target, assessed, assessed_on,
    #     under_target, target_gap, reason, maturity_source
    #
    # ``assessed`` is true only for a row of the caller's own organisation
    # that recorded a current level (the accessor's own strict predicate --
    # never a shared catalogue row, never another tenant's). When not
    # assessed every numeric field is ``None`` -- never ``0`` -- and
    # ``reason`` is ``"no_maturity_recorded"``. When assessed but no target
    # was recorded, ``under_target``/``target_gap`` stay ``None`` and
    # ``reason`` is ``"no_maturity_target_recorded"``. When both are
    # recorded, ``under_target`` and ``target_gap`` are a real comparison and
    # subtraction of the two named inputs -- zero or negative is a real,
    # recorded fact and is never suppressed -- and ``reason`` is ``None``.
    # ``assessed_on`` is the projection's own assessment date when present,
    # independent of whether a level was recorded: it is never the test for
    # ``assessed``.
    # ------------------------------------------------------------------ #

    @staticmethod
    def _maturity_block(
        *,
        capability_id,
        element_id,
        current,
        target,
        reason_code,
        assessment_date,
    ) -> Dict[str, Any]:
        """One tri-state block, from one accessor entry's three values and one
        owner row. Takes ``current``/``target``/``reason_code`` already
        unpacked by the caller (positionally, off the accessor's own stable
        entry shape) rather than a dict keyed by the authority's column
        names, so this file never repeats them.
        """

        assessed = reason_code is None
        assessed_on = assessment_date.isoformat() if assessment_date is not None else None

        if not assessed:
            current = None
            target = None
            under_target = None
            target_gap = None
            reason = reason_code
        else:
            from app.modules.intelligence.services.reason_codes import validate_reason_code

            if target is None:
                under_target = None
                target_gap = None
                reason = validate_reason_code("no_maturity_target_recorded")
            else:
                under_target = current < target
                target_gap = target - current
                reason = None

        return {
            "capability_id": capability_id,
            "element_id": element_id,
            "current": current,
            "target": target,
            "assessed": assessed,
            "assessed_on": assessed_on,
            "under_target": under_target,
            "target_gap": target_gap,
            "reason": reason,
            "maturity_source": "unified_capabilities",
        }

    def maturity_for_capability_ids(
        self, capability_ids: List[int], *, organization_id: int
    ) -> Dict[int, Dict[str, Any]]:
        """The one maturity read keyed by the authority's own id (ADR-MAT-1).

        Two batched selects: (1) the strict accessor
        ``UnifiedCapability.maturity_for_capability_ids`` for
        current/target/assessed; (2) one select of this service's own, on the
        same strict ``organization_id ==`` predicate, for the element id and
        assessment date. Every id asked for is present in the result, keyed
        by capability id; an empty input returns ``{}`` with no select.
        """
        wanted = sorted({int(cid) for cid in capability_ids})
        if not wanted:
            return {}

        accessor_result = UnifiedCapability.maturity_for_capability_ids(
            wanted, organization_id=organization_id
        )

        owner_rows = (
            db.session.query(
                UnifiedCapability.id,
                UnifiedCapability.archimate_element_id,
                UnifiedCapability.maturity_assessment_date,
            )
            .filter(
                UnifiedCapability.id.in_(wanted),
                # Strict, same predicate as the accessor: a shared catalogue
                # row's identity is not this tenant's row either.
                UnifiedCapability.organization_id == organization_id,
            )
            .all()
        )
        owners_by_id = {cap_id: (element_id, assessed_on) for cap_id, element_id, assessed_on in owner_rows}

        result: Dict[int, Dict[str, Any]] = {}
        for cap_id in wanted:
            element_id, assessment_date = owners_by_id.get(cap_id, (None, None))
            # Positional, not by name -- see _maturity_block's own docstring.
            current, target, reason_code = accessor_result[cap_id].values()
            result[cap_id] = self._maturity_block(
                capability_id=cap_id,
                element_id=element_id,
                current=current,
                target=target,
                reason_code=reason_code,
                assessment_date=assessment_date,
            )
        return result

    def maturity_for_elements(
        self, element_ids: List[int], *, organization_id: int
    ) -> Dict[int, Dict[str, Any]]:
        """The one maturity read keyed by ArchiMate element id (ADR-MAT-1).

        Two batched selects: (1) this service's own select of
        ``archimate_element_id IN element_ids`` under the strict
        ``organization_id ==`` predicate, ordered by id, ``setdefault`` so an
        element pointed at by two rows resolves to the same first row every
        time; (2) the strict accessor
        ``UnifiedCapability.maturity_for_capability_ids`` over the capability
        ids found. An element id with no owned row is present with
        ``capability_id: None`` and the not-assessed block -- the FK from a
        capability to its element is not tenant-checked, so a row owned by
        another organisation never contributes its level here. Every id
        asked for is present in the result, keyed by element id; an empty
        input returns ``{}`` with no select.
        """
        wanted = sorted({int(eid) for eid in element_ids})
        if not wanted:
            return {}

        owner_rows = (
            db.session.query(
                UnifiedCapability.id,
                UnifiedCapability.archimate_element_id,
                UnifiedCapability.maturity_assessment_date,
            )
            .filter(
                UnifiedCapability.archimate_element_id.in_(wanted),
                UnifiedCapability.organization_id == organization_id,
            )
            .order_by(UnifiedCapability.id.asc())
            .all()
        )
        owner_by_element: Dict[int, Any] = {}
        for cap_id, element_id, assessed_on in owner_rows:
            owner_by_element.setdefault(element_id, (cap_id, assessed_on))

        capability_ids = sorted({cap_id for cap_id, _assessed_on in owner_by_element.values()})
        accessor_result = UnifiedCapability.maturity_for_capability_ids(
            capability_ids, organization_id=organization_id
        )

        result: Dict[int, Dict[str, Any]] = {}
        for element_id in wanted:
            owner = owner_by_element.get(element_id)
            if owner is None:
                from app.modules.intelligence.services.reason_codes import validate_reason_code

                result[element_id] = self._maturity_block(
                    capability_id=None,
                    element_id=element_id,
                    current=None,
                    target=None,
                    reason_code=validate_reason_code("no_maturity_recorded"),
                    assessment_date=None,
                )
                continue
            cap_id, assessment_date = owner
            # Positional, not by name -- see _maturity_block's own docstring.
            current, target, reason_code = accessor_result[cap_id].values()
            result[element_id] = self._maturity_block(
                capability_id=cap_id,
                element_id=element_id,
                current=current,
                target=target,
                reason_code=reason_code,
                assessment_date=assessment_date,
            )
        return result

    def get_domain_health(self) -> List[Dict[str, Any]]:
        """
        Calculate health score for each business domain.

        Health score = weighted(avg_maturity_ratio * 60% + avg_coverage * 40%)
        Status thresholds: healthy (>=80), attention (60 - 79), at_risk (40 - 59), critical (<40)

        Returns:
            List of domain health dicts sorted by health score ascending (worst first).
        """
        domains = BusinessDomain.query.all()

        results = []
        for domain in domains:
            # Get capabilities for this domain
            caps = UnifiedCapability.query.filter_by(domain_id=domain.id).all()
            if not caps:
                results.append(
                    {
                        "domain_name": domain.name,
                        "domain_code": domain.code,
                        "health_score": 0,
                        "capability_count": 0,
                        "avg_maturity": 0,
                        "avg_coverage": 0,
                        "status": "critical",
                    }
                )
                continue

            cap_ids = [c.id for c in caps]

            # Average maturity ratio
            maturity_ratios = []
            for c in caps:
                current = c.current_maturity_level or 1
                target = c.target_maturity_level or 3
                if target > 0:
                    maturity_ratios.append(current / target)
            avg_maturity_ratio = (
                sum(maturity_ratios) / len(maturity_ratios) if maturity_ratios else 0
            )

            # Average coverage from mappings
            coverage_result = (
                db.session.query(func.avg(UnifiedApplicationCapabilityMapping.coverage_percentage))
                .filter(UnifiedApplicationCapabilityMapping.unified_capability_id.in_(cap_ids))
                .scalar()
            )
            avg_coverage = float(coverage_result or 0) / 100.0  # normalize to 0 - 1

            # Weighted health score
            health_score = round((avg_maturity_ratio * 0.6 + avg_coverage * 0.4) * 100, 1)
            health_score = min(health_score, 100)

            # Status classification
            if health_score >= 80:
                status = "healthy"
            elif health_score >= 60:
                status = "attention"
            elif health_score >= 40:
                status = "at_risk"
            else:
                status = "critical"

            avg_maturity = sum(c.current_maturity_level or 1 for c in caps) / len(caps)

            results.append(
                {
                    "domain_name": domain.name,
                    "domain_code": domain.code,
                    "health_score": health_score,
                    "capability_count": len(caps),
                    "avg_maturity": round(avg_maturity, 1),
                    "avg_coverage": round(avg_coverage * 100, 1),
                    "status": status,
                }
            )

        # Sort by health score ascending (worst first for attention)
        results.sort(key=lambda r: r["health_score"])
        return results
