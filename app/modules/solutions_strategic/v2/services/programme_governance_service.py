"""Programme governance rollups (PROG-001).

A Transformation Programme is a StrategicInitiative with initiative_type set
(greenfield | brownfield) that groups member Solutions via
Solution.initiative_id. This service computes the programme-level governance
view from data that already lives on the member solutions:

  - membership & delivery state (ADM phase, governance status, maturity)
  - ERP fit-gap rollup -> clean-core score (solution_fit_gap_entries)
  - risk posture (solution_risks)
  - transition timeline (solution_plateaus as waves)
  - ARB pipeline (arb_review_items via governance_status)
  - vendor footprint (solution_vendor_products)

Nothing here writes data; rollups are computed live so the cockpit can never
drift from the blueprints it aggregates.
"""

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_

from app import db
from app.models.solution_models import Solution, SolutionFitGapEntry
from app.models.strategic import StrategicInitiative
from app.models.transformation_programme import ProgrammeWorkstream

logger = logging.getLogger(__name__)

# Clean-core weighting over the SAP ACTIVATE fit-gap taxonomy.
# standard/configuration keep the core clean; extension means side-by-side
# extensibility (acceptable, partial credit); enhancement modifies behaviour
# in-stack (low credit); custom development breaks clean core (zero).
# out_of_scope entries are excluded from the denominator.
CLEAN_CORE_WEIGHTS = {
    "standard": 1.0,
    "configuration": 1.0,
    "isv_solution": 0.8,
    "extension": 0.6,
    "enhancement": 0.3,
    "customization": 0.1,
    "custom": 0.0,
    "custom_development": 0.0,
}

ADM_PHASES = list("ABCDEFGH")


class ProgrammeGovernanceService:
    """Rollup computations + baseline linkage for Transformation Programmes."""

    BASELINE_SUFFIX = " — Current-State Baseline"

    # ------------------------------------------------------------------ #
    # Baseline linkage (shared by batch import + discovery connectors)    #
    # ------------------------------------------------------------------ #

    @classmethod
    def link_apps_to_baseline(cls, initiative_id: int, app_ids: List[int], user_id: int) -> int:
        """Link ApplicationComponents into the programme's Current-State
        Baseline solution (created on first use, reused thereafter).

        Idempotent — existing (solution, app) pairs are skipped via
        ON CONFLICT DO NOTHING. Returns the number of link statements issued.
        Flushes but does NOT commit — callers own the transaction.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.models.solution_models import solution_applications

        initiative = db.session.get(StrategicInitiative, initiative_id)
        if initiative is None:
            logger.warning("Programme %s not found — skipping baseline linkage", initiative_id)
            return 0

        baseline_name = f"{initiative.name}{cls.BASELINE_SUFFIX}"
        baseline = Solution.query.filter_by(
            initiative_id=initiative.id, name=baseline_name
        ).first()
        if baseline is None:
            baseline = Solution(
                name=baseline_name,
                description=(
                    "Auto-created by landscape discovery import. Holds the "
                    "discovered current-state systems for this programme."
                ),
                solution_type="Platform",
                status="planned",
                adm_phase="B",  # baseline architecture = Phase B territory
                governance_status="draft",
                initiative_id=initiative.id,
                created_by_id=user_id,
            )
            db.session.add(baseline)
            db.session.flush()
            logger.info("Created programme baseline solution %s", baseline.id)

        linked = 0
        for app_id in app_ids:
            if not app_id:
                continue
            db.session.execute(
                pg_insert(solution_applications)
                .values(solution_id=baseline.id, application_component_id=app_id)
                .on_conflict_do_nothing()
            )
            linked += 1
        logger.info(
            "Programme %s: %d app link(s) issued to baseline solution %s",
            initiative.id, linked, baseline.id,
        )
        return linked

    # ------------------------------------------------------------------ #
    # Listing                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def list_programmes() -> List[Dict[str, Any]]:
        """All initiatives acting as programmes, with member counts."""
        rows = (
            db.session.query(
                StrategicInitiative,
                func.count(Solution.id),
            )
            .outerjoin(Solution, Solution.initiative_id == StrategicInitiative.id)
            .filter(
                or_(
                    StrategicInitiative.record_kind == "transformation_programme",
                    StrategicInitiative.initiative_type.isnot(None),
                )
            )
            .group_by(StrategicInitiative.id)
            .order_by(StrategicInitiative.created_at.desc())
            .all()
        )
        out = []
        for initiative, member_count in rows:
            is_transformation = (
                initiative.record_kind == "transformation_programme"
            )
            workstream_count = None
            if is_transformation:
                workstream_count = ProgrammeWorkstream.query.filter(
                    ProgrammeWorkstream.organization_id
                    == initiative.organization_id,
                    ProgrammeWorkstream.programme_id == initiative.id,
                ).count()
            out.append({
                "id": initiative.id,
                "name": initiative.name,
                "description": initiative.description or "",
                "initiative_type": initiative.initiative_type,
                "target_platform": initiative.target_platform or "",
                "vendor_key": initiative.vendor_key or "",
                "status": initiative.status,
                "priority": initiative.priority,
                "start_date": initiative.start_date.isoformat() if initiative.start_date else None,
                "target_completion_date": (
                    initiative.target_completion_date.isoformat()
                    if initiative.target_completion_date else None
                ),
                "member_count": member_count,
                "workstream_count": workstream_count,
                "is_transformation": is_transformation,
            })
        return out

    # ------------------------------------------------------------------ #
    # Fit-gap workbench (PROG-004)                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clean_core_score(counts: Dict[str, int]) -> Optional[int]:
        """Weighted clean-core % over a fit_type→count map (None if no scored rows)."""
        scored = {ft: n for ft, n in counts.items() if ft in CLEAN_CORE_WEIGHTS}
        denominator = sum(scored.values())
        if not denominator:
            return None
        return round(
            sum(CLEAN_CORE_WEIGHTS[ft] * n for ft, n in scored.items())
            / denominator * 100
        )

    @staticmethod
    def fit_gap_entries(initiative_id: int) -> Optional[List[Dict[str, Any]]]:
        """All fit-gap entries across member solutions. None if programme missing."""
        if db.session.get(StrategicInitiative, initiative_id) is None:
            return None
        rows = (
            db.session.query(SolutionFitGapEntry, Solution.name)
            .join(Solution, Solution.id == SolutionFitGapEntry.solution_id)
            .filter(Solution.initiative_id == initiative_id)
            .order_by(SolutionFitGapEntry.erp_module, SolutionFitGapEntry.sort_order,
                      SolutionFitGapEntry.id)
            .all()
        )
        return [
            {**entry.to_dict(), "solution_name": sol_name}
            for entry, sol_name in rows
        ]

    @staticmethod
    def bulk_update_fit_gap(
        initiative_id: int,
        entry_ids: List[int],
        fit_type: Optional[str] = None,
        status: Optional[str] = None,
        erp_module: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Bulk-update fit-gap entries — ONLY those belonging to the programme's
        member solutions (membership is the authorisation boundary).

        Returns {"updated": N, "skipped": M}. Raises ValueError on invalid
        fit_type/status values.
        """
        if fit_type is not None and fit_type not in SolutionFitGapEntry.FIT_TYPES:
            raise ValueError(
                f"Invalid fit_type {fit_type!r}. Allowed: {SolutionFitGapEntry.FIT_TYPES}"
            )
        if status is not None and status not in SolutionFitGapEntry.STATUSES:
            raise ValueError(
                f"Invalid status {status!r}. Allowed: {SolutionFitGapEntry.STATUSES}"
            )
        if fit_type is None and status is None and erp_module is None:
            raise ValueError("Nothing to update — provide fit_type, status, or erp_module.")

        member_ids = [
            s.id for s in Solution.query.filter_by(initiative_id=initiative_id)
            .with_entities(Solution.id).all()
        ]
        entries = SolutionFitGapEntry.query.filter(
            SolutionFitGapEntry.id.in_(entry_ids or []),
            SolutionFitGapEntry.solution_id.in_(member_ids or [0]),
        ).all()

        for e in entries:
            if fit_type is not None:
                e.fit_type = fit_type
            if status is not None:
                e.status = status
            if erp_module is not None:
                e.erp_module = erp_module.strip() or None
        db.session.commit()
        updated = len(entries)
        return {"updated": updated, "skipped": len(entry_ids or []) - updated}

    # ------------------------------------------------------------------ #
    # Snapshots + drift detection (PROG-005)                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _capture_ai_review(baseline_solution_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """PROG-013: run the conformance + data-stewardship reviewers against a
        freshly-imported landscape and return a compact, persistable summary.

        Returns None (never raises) if the reviewers are unavailable or error —
        an import must never be blocked by its AI review.
        """
        from datetime import datetime

        def _top(findings, n=3):
            # keep flagged (critical/high) first, then info; trim to essentials
            order = {"critical": 0, "high": 1, "info": 2}
            ranked = sorted(findings or [], key=lambda f: order.get(f.get("severity"), 3))
            return [
                {"severity": f.get("severity"), "title": f.get("title"),
                 "category": f.get("category")}
                for f in ranked[:n]
            ]

        result: Dict[str, Any] = {
            "reviewed_at": datetime.utcnow().isoformat() + "Z",
            "conformance": None,
            "stewardship": None,
            "flagged_total": 0,
        }
        flagged = 0

        if baseline_solution_id is not None:
            try:
                from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
                    ConformanceReviewer,
                )
                c = ConformanceReviewer.review(baseline_solution_id)
                if c.get("success"):
                    result["conformance"] = {
                        "solution_id": baseline_solution_id,
                        "score": c.get("score"),
                        "flagged": c.get("flagged", 0),
                        "findings": _top(c.get("findings")),
                    }
                    flagged += int(c.get("flagged", 0) or 0)
            except Exception as exc:
                logger.error("AI-on-contact conformance review failed: %s", exc)

        try:
            from app.modules.solutions_strategic.v2.services.data_stewardship_reviewer import (
                DataStewardshipReviewer,
            )
            s = DataStewardshipReviewer.review()
            if s.get("success"):
                result["stewardship"] = {
                    "flagged": s.get("flagged", 0),
                    "finding_count": s.get("finding_count", 0),
                    "findings": _top(s.get("findings")),
                }
                flagged += int(s.get("flagged", 0) or 0)
        except Exception as exc:
            logger.error("AI-on-contact stewardship review failed: %s", exc)

        if result["conformance"] is None and result["stewardship"] is None:
            return None
        result["flagged_total"] = flagged
        return result

    @classmethod
    def snapshot_programme(
        cls, initiative_id: int, user_id: int, source: str = "manual",
        ai_review: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Capture a governance snapshot and compute drift vs the previous one.

        Drift signals (any -> snapshot.drift.flagged=True + ARBAuditLog row):
          - clean-core score regressed since the last snapshot
          - clean-core score below the programme target
          - baseline estate changed (systems appeared/disappeared)

        When ``ai_review`` is True (PROG-013, the landscape-import path), Entelim
        also runs the conformance + data-stewardship reviewers the moment the
        estate lands and persists a compact result on snapshot.ai_review — the
        "AI on contact" review. Off by default so manual/scheduled snapshots stay
        cheap.

        Returns the snapshot dict, or None if the programme doesn't exist.
        FLUSHES but does not commit — callers own the transaction (the batch
        import pipeline calls this inside its savepoint structure; a failed
        import correctly rolls its snapshot back too).
        """
        from app.models.solution_models import solution_applications
        from app.models.strategic import ProgrammeSnapshot

        rollup = cls.rollup(initiative_id)
        if rollup is None:
            return None
        initiative = db.session.get(StrategicInitiative, initiative_id)

        # Baseline estate = apps linked to the Current-State Baseline solution
        baseline_ids: List[int] = []
        baseline = Solution.query.filter_by(
            initiative_id=initiative_id,
            name=f"{initiative.name}{cls.BASELINE_SUFFIX}",
        ).first()
        if baseline is not None:
            baseline_ids = [
                row[0] for row in db.session.query(
                    solution_applications.c.application_component_id
                ).filter(solution_applications.c.solution_id == baseline.id).all()
            ]

        previous = (
            ProgrammeSnapshot.query.filter_by(initiative_id=initiative_id)
            .order_by(ProgrammeSnapshot.taken_at.desc(), ProgrammeSnapshot.id.desc())
            .first()
        )

        score = rollup["fit_gap"]["clean_core_score"]
        target = rollup["fit_gap"]["clean_core_target"]
        drift: Dict[str, Any] = {"flagged": False, "reasons": []}
        if previous is not None:
            prev_score = previous.clean_core_score
            if score is not None and prev_score is not None and score < prev_score:
                drift["score_delta"] = score - prev_score
                drift["flagged"] = True
                drift["reasons"].append(
                    f"Clean-core regressed {prev_score}% → {score}%"
                )
            prev_ids = set(previous.baseline_app_ids or [])
            added = sorted(set(baseline_ids) - prev_ids)
            removed = sorted(prev_ids - set(baseline_ids))
            if added or removed:
                drift["apps_added"] = added
                drift["apps_removed"] = removed
                drift["flagged"] = True
                drift["reasons"].append(
                    f"Baseline estate changed: +{len(added)} / -{len(removed)} system(s)"
                )
        if score is not None and target and score < target:
            drift["below_target"] = True
            drift["flagged"] = True
            drift["reasons"].append(f"Clean-core {score}% below target {target}%")

        snap = ProgrammeSnapshot(
            initiative_id=initiative_id,
            source=source,
            clean_core_score=score,
            clean_core_target=target,
            fit_counts=rollup["fit_gap"]["counts"],
            member_count=rollup["member_count"],
            arb_approved=rollup["arb_pipeline"]["approved"],
            risk_total=rollup["risks"]["total"],
            baseline_app_ids=baseline_ids,
            baseline_app_count=len(baseline_ids),
            drift=drift,
        )
        db.session.add(snap)
        db.session.flush()

        # PROG-013: AI on contact — review the freshly-landed estate and pin the
        # result to this import snapshot. Never allowed to break the import.
        if ai_review:
            snap.ai_review = cls._capture_ai_review(
                baseline.id if baseline is not None else None
            )
            db.session.flush()

        if drift["flagged"]:
            try:
                from app.models.architecture_review_board import ARBAuditLog

                db.session.add(ARBAuditLog(
                    entity_type="Programme",
                    entity_id=initiative_id,
                    action="programme_drift_detected",
                    action_description=(
                        f"Drift detected on programme '{initiative.name}' "
                        f"({source} snapshot {snap.id}): " + "; ".join(drift["reasons"])
                    ),
                    user_id=user_id,
                    new_value=drift,
                ))
            except Exception as exc:
                logger.error("Drift ARB escalation failed (snapshot kept): %s", exc)

        db.session.flush()
        logger.info(
            "Programme %s snapshot %s captured (source=%s, flagged=%s)",
            initiative_id, snap.id, source, drift["flagged"],
        )
        return snap.to_dict()

    @staticmethod
    def list_snapshots(initiative_id: int, limit: int = 20) -> Optional[List[Dict[str, Any]]]:
        """Most-recent-first snapshot history. None if programme missing."""
        from app.models.strategic import ProgrammeSnapshot

        if db.session.get(StrategicInitiative, initiative_id) is None:
            return None
        rows = (
            ProgrammeSnapshot.query.filter_by(initiative_id=initiative_id)
            .order_by(ProgrammeSnapshot.taken_at.desc(), ProgrammeSnapshot.id.desc())
            .limit(limit)
            .all()
        )
        return [s.to_dict() for s in rows]

    # ------------------------------------------------------------------ #
    # Rollup                                                               #
    # ------------------------------------------------------------------ #

    @classmethod
    def rollup(cls, initiative_id: int) -> Optional[Dict[str, Any]]:
        """Full governance rollup for one programme. None if not found."""
        initiative = db.session.get(StrategicInitiative, initiative_id)
        if initiative is None:
            return None

        members: List[Solution] = (
            Solution.query.filter_by(initiative_id=initiative_id)
            .order_by(Solution.name)
            .all()
        )
        member_ids = [s.id for s in members]

        # --- membership & delivery state --------------------------------
        phase_counts = Counter((s.adm_phase or "?").strip() for s in members)
        gov_counts = Counter((s.governance_status or "draft") for s in members)
        maturities = [s.maturity_current for s in members if s.maturity_current]
        member_rows = [
            {
                "id": s.id,
                "name": s.name,
                "adm_phase": (s.adm_phase or "").strip(),
                "governance_status": s.governance_status or "draft",
                "status": s.status,
                "maturity": s.maturity_current or 0,
                "solution_type": s.solution_type,
                "business_domain": s.business_domain,
            }
            for s in members
        ]

        # --- fit-gap rollup -> clean-core score --------------------------
        fit_counts: Dict[str, int] = {}
        clean_core_score = None
        by_module: List[Dict[str, Any]] = []
        if member_ids:
            rows = (
                db.session.query(SolutionFitGapEntry.fit_type, func.count())
                .filter(SolutionFitGapEntry.solution_id.in_(member_ids))
                .group_by(SolutionFitGapEntry.fit_type)
                .all()
            )
            fit_counts = {(ft or "unclassified"): n for ft, n in rows}
            clean_core_score = cls._clean_core_score(fit_counts)

            # Per-module clean-core (PROG-004): module -> counts + score
            mod_rows = (
                db.session.query(
                    SolutionFitGapEntry.erp_module,
                    SolutionFitGapEntry.fit_type,
                    func.count(),
                )
                .filter(SolutionFitGapEntry.solution_id.in_(member_ids))
                .group_by(SolutionFitGapEntry.erp_module, SolutionFitGapEntry.fit_type)
                .all()
            )
            modules: Dict[str, Dict[str, int]] = {}
            for module, ft, n in mod_rows:
                modules.setdefault(module or "Unassigned", {})[ft or "unclassified"] = n
            for module, counts in sorted(modules.items()):
                by_module.append({
                    "module": module,
                    "total": sum(counts.values()),
                    "counts": counts,
                    "clean_core_score": cls._clean_core_score(counts),
                })

        # --- risk posture -------------------------------------------------
        risk_total = 0
        risk_by_impact: Dict[str, int] = {}
        if member_ids:
            from app.models.solution_lifecycle_models import SolutionRisk

            rows = (
                db.session.query(SolutionRisk.impact, func.count())
                .filter(SolutionRisk.solution_id.in_(member_ids))
                .group_by(SolutionRisk.impact)
                .all()
            )
            risk_by_impact = {(i or "unrated"): n for i, n in rows}
            risk_total = sum(risk_by_impact.values())

        # --- transition timeline (plateaus as programme waves) -----------
        waves: List[Dict[str, Any]] = []
        if member_ids:
            from app.models.solution_lifecycle_models import SolutionPlateau

            sol_names = {s.id: s.name for s in members}
            for p in (
                SolutionPlateau.query
                .filter(SolutionPlateau.solution_id.in_(member_ids))
                .order_by(SolutionPlateau.order, SolutionPlateau.id)
                .all()
            ):
                waves.append({
                    "id": p.id,
                    "name": p.name,
                    "solution_id": p.solution_id,
                    "solution_name": sol_names.get(p.solution_id, ""),
                    "target_date": p.target_date.isoformat() if p.target_date else None,
                })

        # --- vendor footprint ---------------------------------------------
        vendors: List[Dict[str, Any]] = []
        if member_ids:
            try:
                from app.models.solution_models import solution_vendor_products
                from app.models.vendor.vendor_organization import VendorProduct

                rows = (
                    db.session.query(
                        VendorProduct.name,
                        func.count(solution_vendor_products.c.solution_id),
                    )
                    .join(solution_vendor_products,
                          solution_vendor_products.c.vendor_product_id == VendorProduct.id)
                    .filter(solution_vendor_products.c.solution_id.in_(member_ids))
                    .group_by(VendorProduct.name)
                    .order_by(func.count(solution_vendor_products.c.solution_id).desc())
                    .limit(12)
                    .all()
                )
                vendors = [{"name": n, "count": c} for n, c in rows]
            except Exception as exc:
                logger.debug("vendor footprint rollup unavailable: %s", exc)

        # --- ARB pipeline --------------------------------------------------
        arb_pipeline = {
            "draft": gov_counts.get("draft", 0),
            "arb_review": gov_counts.get("arb_review", 0),
            "under_review": gov_counts.get("under_review", 0),
            "approved": gov_counts.get("approved", 0),
            "rejected": gov_counts.get("rejected", 0),
        }

        return {
            "programme": {
                "id": initiative.id,
                "name": initiative.name,
                "description": initiative.description or "",
                "initiative_type": initiative.initiative_type,
                "target_platform": initiative.target_platform or "",
                "vendor_key": initiative.vendor_key or "",
                "status": initiative.status,
                "priority": initiative.priority,
                "risk_level": initiative.risk_level,
                "start_date": initiative.start_date.isoformat() if initiative.start_date else None,
                "target_completion_date": (
                    initiative.target_completion_date.isoformat()
                    if initiative.target_completion_date else None
                ),
                "budget_allocated": initiative.budget_allocated or 0,
                "budget_spent": initiative.budget_spent or 0,
            },
            "members": member_rows,
            "member_count": len(members),
            "phase_distribution": [
                {"phase": p, "count": phase_counts.get(p, 0)} for p in ADM_PHASES
            ],
            "arb_pipeline": arb_pipeline,
            "avg_maturity": round(sum(maturities) / len(maturities)) if maturities else 0,
            "fit_gap": {
                "counts": fit_counts,
                "total": sum(fit_counts.values()),
                "clean_core_score": clean_core_score,
                "clean_core_target": initiative.clean_core_target,
                "by_module": by_module,
                "weights": CLEAN_CORE_WEIGHTS,
            },
            "risks": {"total": risk_total, "by_impact": risk_by_impact},
            "waves": waves,
            "vendors": vendors,
        }
