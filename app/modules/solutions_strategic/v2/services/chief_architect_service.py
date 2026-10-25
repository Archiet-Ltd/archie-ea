"""Chief Architect synthesis (AI-7).

The meta-agent: instead of running each AI architect separately, it
assembles ONE board-ready packet per solution — the technical-conformance
verdict, the recommended decision (ADR), and a portfolio-wide synthesis —
so a review board sees the whole picture in a single view.

Pure orchestration over the existing reviewers (Conformance, Options
Advisor) + the solution context; deterministic and sourced.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from app import db

logger = logging.getLogger(__name__)

def _n(count, singular, plural=None):
    """Big-4 copy: '3 findings' / '1 finding' — never 'finding(s)'."""
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count} {word}"



class ChiefArchitectService:

    @staticmethod
    def _utcnow():
        """Return the repository's naive UTC timestamp without deprecated utcnow()."""
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @classmethod
    def solution_packet(cls, solution_id: int) -> Dict[str, Any]:
        """One board-ready packet for a solution: conformance + decision +
        a synthesised verdict."""
        from app.models.solution_models import Solution
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
            ConformanceReviewer,
        )
        from app.modules.solutions_strategic.v2.services.solution_options_advisor import (
            SolutionOptionsAdvisor,
        )

        solution = db.session.get(Solution, solution_id)
        if solution is None:
            return {"success": False, "error": "Solution not found."}

        conformance = ConformanceReviewer.review(solution_id)
        latest_adr = SolutionOptionsAdvisor.latest(solution_id)
        adr = SolutionOptionsAdvisor.to_dict(latest_adr) if latest_adr else None

        from app.services.solution_design_score_service import compute as design_score

        review_succeeded = bool(conformance.get("success"))
        controls_available = review_succeeded and conformance.get("controls_available", True)
        unassessed = bool(conformance.get("unassessed"))
        flagged = conformance.get("flagged", 0) if review_succeeded else 0
        has_decision = adr is not None
        decision_accepted = bool(adr and adr.get("status") == "accepted")

        # synthesised board verdict
        if not review_succeeded:
            verdict, tone = "Conformance review unavailable", "warn"
        elif not controls_available:
            verdict, tone = "Conformance controls unavailable", "warn"
        elif unassessed:
            verdict, tone = "Not yet reviewable", "info"
        elif flagged == 0 and decision_accepted:
            verdict, tone = "Ready for the board", "good"
        elif flagged == 0 and has_decision:
            verdict, tone = "Conformant — decision pending acceptance", "info"
        elif flagged == 0:
            verdict, tone = "Conformant — no decision recorded yet", "info"
        elif flagged and decision_accepted:
            verdict, tone = f"Decision made, but {_n(flagged, 'conformance issue')} to resolve", "warn"
        else:
            verdict, tone = f"{_n(flagged, 'conformance issue')} — not board-ready", "warn"

        # The board-readiness checklist is the design score: the same ten
        # checks, and the same "n of 10", as the list, the page and the document.
        score = design_score(solution).to_dict()

        return {
            "success": True,
            "solution": {"id": solution.id, "name": solution.name,
                         "adm_phase": solution.adm_phase or "A",
                         "governance_status": getattr(solution, "governance_status", "draft")},
            "verdict": verdict,
            "tone": tone,
            "design_score": score,
            "conformance": {
                "flagged": flagged,
                "findings": conformance.get("findings", []) if review_succeeded else [],
                "summary": conformance.get("summary") if review_succeeded else conformance.get("error"),
                "unassessed": unassessed,
                "controls_available": controls_available,
                "unavailable_checks": conformance.get("unavailable_checks", []) if review_succeeded else [],
            },
            "decision": adr,
            "readiness": [{"label": c["label"], "ok": c["passed"], "key": c["key"]} for c in score["checks"]],
        }

    @staticmethod
    def _summarise_domain_evidence(
        evidence: List[Dict[str, Any]], *, in_scope: int
    ) -> Dict[str, Any]:
        """Summarise five-domain coverage without turning failed reads into zeroes.

        The denominator is the number of solutions whose linked-element
        catalogue was read successfully.  A failed read is disclosed beside
        that denominator, never interpreted as a solution with no coverage.
        """
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
            _DATA_TABLES,
        )

        available = [item for item in evidence if item.get("available")]
        measured = len(available)
        unavailable = max(0, in_scope - measured)
        if not in_scope:
            state = "empty"
        elif not measured:
            state = "unavailable"
        elif unavailable:
            state = "partial"
        else:
            state = "available"

        definitions = (
            ("business", "Business", lambda item: "business" in item["layers"]),
            ("data", "Data", lambda item: bool(item["tables"] & _DATA_TABLES)),
            ("application", "Application", lambda item: "application" in item["layers"]),
            ("technology", "Technology", lambda item: "technology" in item["layers"]),
            ("motivation", "Motivation", lambda item: "motivation" in item["layers"]),
        )
        domains = []
        for key, label, predicate in definitions:
            if measured:
                covered = sum(1 for item in available if predicate(item))
                denominator = measured
                percentage = round(covered / denominator * 100)
            elif not in_scope:
                covered, denominator, percentage = 0, 0, None
            else:
                covered = denominator = percentage = None
            domains.append(
                {
                    "key": key,
                    "label": label,
                    "covered": covered,
                    "denominator": denominator,
                    "percentage": percentage,
                }
            )
        return {
            "state": state,
            "in_scope": in_scope,
            "measured": measured,
            "unavailable": unavailable,
            "domains": domains,
        }

    @classmethod
    def _architecture_posture(cls, solutions) -> Dict[str, Any]:
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
            ConformanceReviewer,
        )

        evidence = []
        for solution in solutions:
            try:
                _total, layers, tables = ConformanceReviewer._element_counts(solution.id)
                evidence.append(
                    {"available": True, "layers": layers, "tables": tables}
                )
            except Exception as exc:  # noqa: BLE001 — partial evidence is explicit
                evidence.append({"available": False, "layers": set(), "tables": set()})
                logger.debug(
                    "domain coverage unavailable for solution %s: %s",
                    solution.id,
                    exc,
                )
        return cls._summarise_domain_evidence(evidence, in_scope=len(solutions))

    @staticmethod
    def _strategic_posture(solutions) -> Dict[str, Any]:
        from app.models.strategic import StrategicInitiative

        try:
            programmes = StrategicInitiative.query.filter(
                StrategicInitiative.initiative_type.isnot(None)
            ).all()
        except Exception as exc:  # noqa: BLE001 — one concern must not hide the page
            logger.debug("strategic programme posture unavailable: %s", exc)
            return {
                "state": "unavailable",
                "programmes_total": None,
                "programmes_in_flight": None,
                "solutions_assigned": None,
                "solutions_denominator": len(solutions),
            }

        in_flight_statuses = {"planning", "in_progress"}
        return {
            "state": "available" if programmes else "empty",
            "programmes_total": len(programmes),
            "programmes_in_flight": sum(
                1 for programme in programmes
                if (programme.status or "").strip().lower() in in_flight_statuses
            ),
            "solutions_assigned": sum(
                1 for solution in solutions if solution.initiative_id is not None
            ),
            "solutions_denominator": len(solutions),
        }

    @staticmethod
    def _delivery_posture(solutions) -> Dict[str, Any]:
        phase_counts: Dict[str, int] = {}
        for solution in solutions:
            phase = (solution.adm_phase or "").strip().upper()
            if phase:
                phase_counts[phase] = phase_counts.get(phase, 0) + 1

        def _matches(solution, statuses, deployment_statuses):
            status = (solution.status or "").strip().lower()
            deployment = (solution.deployment_status or "").strip().lower()
            return status in statuses or deployment in deployment_statuses

        return {
            "state": "available" if solutions else "empty",
            "in_scope": len(solutions),
            "in_progress": sum(
                1 for solution in solutions
                if _matches(solution, {"in_progress"}, {"development", "testing"})
            ),
            "production": sum(
                1 for solution in solutions
                if _matches(solution, {"deployed"}, {"production"})
            ),
            "planned": sum(
                1 for solution in solutions
                if _matches(solution, {"planned"}, {"design"})
            ),
            "blocked": sum(
                1 for solution in solutions
                if _matches(solution, {"blocked", "paused"}, {"blocked"})
            ),
            "phase_counts": dict(sorted(phase_counts.items())),
        }

    @classmethod
    def _age_days(cls, timestamp) -> int | None:
        if timestamp is None:
            return None
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        return max(0, (cls._utcnow() - timestamp).days)

    @staticmethod
    def _prioritise_attention(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        severity_rank = {"critical": 0, "high": 1, "medium": 2, "info": 3}

        def _key(item):
            age = item.get("age_days")
            return (
                severity_rank.get(item.get("severity"), 4),
                age is None,
                -(age or 0),
                (item.get("name") or "").casefold(),
                item.get("id") or 0,
            )

        return sorted(items, key=_key)

    @classmethod
    def _risk_dependency_posture(cls, solutions):
        from app.models.solution_lifecycle_models import SolutionRisk
        from app.models.solution_sad_models import MigrationDependency

        solution_ids = [solution.id for solution in solutions]
        solution_names = {solution.id: solution.name for solution in solutions}
        risk_attention = []

        try:
            risks = (
                SolutionRisk.query.filter(SolutionRisk.solution_id.in_(solution_ids)).all()
                if solution_ids else []
            )
            closed_statuses = {"closed", "mitigated", "resolved"}
            active = [
                risk for risk in risks
                if (risk.status or "").strip().lower() not in closed_statuses
            ]
            material = [
                risk for risk in active
                if (risk.impact or "").strip().lower() in {"critical", "high"}
            ]
            for risk in material:
                impact = (risk.impact or "").strip().lower()
                probability = (risk.probability or "").strip().lower()
                if not risk.owner:
                    next_action = "Assign an owner and agree mitigation"
                elif not risk.mitigation:
                    next_action = "Document the mitigation response"
                else:
                    next_action = "Review mitigation and residual exposure"
                title = (risk.risk_name or risk.risk_description or "Recorded solution risk").strip()
                risk_attention.append(
                    {
                        "id": risk.id,
                        "kind": "risk",
                        "source_label": "Risks and dependencies",
                        "name": solution_names.get(risk.solution_id, "Solution risk"),
                        "title": title,
                        "status": risk.status or "not recorded",
                        "severity": "critical" if impact == "critical" else "high",
                        "age_days": cls._age_days(risk.created_at),
                        "reason": (
                            f"{impact.title()} impact"
                            + (f" · {probability} probability" if probability else "")
                            + (" · no owner" if not risk.owner else "")
                        ),
                        "evidence_url": f"/solutions/{risk.solution_id}/risks/heatmap",
                        "next_action": next_action,
                        "action_url": f"/solutions/{risk.solution_id}/risks/heatmap",
                    }
                )
            risk_summary = {
                "state": "available" if risks else "empty",
                "recorded": len(risks),
                "open": len(active),
                "material_open": len(material),
                "unowned_material": sum(1 for risk in material if not risk.owner),
            }
        except Exception as exc:  # noqa: BLE001 — concern becomes unavailable
            logger.debug("solution risk posture unavailable: %s", exc)
            risk_summary = {
                "state": "unavailable",
                "recorded": None,
                "open": None,
                "material_open": None,
                "unowned_material": None,
            }

        try:
            # MigrationDependency is not TenantMixin-backed.  Restrict it to
            # the already tenant-scoped solution IDs mechanically.
            dependencies = (
                MigrationDependency.query.filter(
                    MigrationDependency.solution_id.in_(solution_ids)
                ).all()
                if solution_ids else []
            )
            dependency_summary = {
                "state": "available" if dependencies else "empty",
                "recorded": len(dependencies),
                "strict_precedence": sum(
                    1 for dependency in dependencies
                    if dependency.dependency_type == "strict_precedence"
                ),
                "with_lag": sum(
                    1 for dependency in dependencies if dependency.lag_days is not None
                ),
            }
        except Exception as exc:  # noqa: BLE001 — concern becomes unavailable
            logger.debug("solution dependency posture unavailable: %s", exc)
            dependency_summary = {
                "state": "unavailable",
                "recorded": None,
                "strict_precedence": None,
                "with_lag": None,
            }

        states = {risk_summary["state"], dependency_summary["state"]}
        if "unavailable" in states:
            state = "unavailable" if states == {"unavailable"} else "partial"
        elif states == {"empty"}:
            state = "empty"
        else:
            state = "available"
        return {
            "state": state,
            "risks": risk_summary,
            "dependencies": dependency_summary,
        }, risk_attention

    @classmethod
    def _arb_posture(cls):
        from app.models.architecture_review_board import (
            ARB_BLOCKED_OR_NOT_READY_STATUSES,
            ARB_DECIDED_STATUSES,
            ARB_OPEN_STATUSES,
            ARB_REVIEW_SLA_DAYS,
            ARBReviewItem,
        )

        reviews = ARBReviewItem.query.all()
        open_reviews = [review for review in reviews if review.status in ARB_OPEN_STATUSES]
        dated_open = [review for review in open_reviews if review.submitted_at]
        undated_open = len(open_reviews) - len(dated_open)
        ages = {review.id: cls._age_days(review.submitted_at) for review in dated_open}
        overdue = [
            review for review in dated_open
            if ages[review.id] is not None and ages[review.id] > ARB_REVIEW_SLA_DAYS
        ]
        oldest_open_age_days = (
            max(ages.values()) if ages and not undated_open else None
        )
        arb = {
            "open": len(open_reviews),
            "decided": sum(1 for review in reviews if review.status in ARB_DECIDED_STATUSES),
            "blocked_or_not_ready": sum(
                1 for review in reviews
                if review.status in ARB_BLOCKED_OR_NOT_READY_STATUSES
            ),
            "dated_open": len(dated_open),
            "overdue_open": len(overdue),
            "oldest_open_age_days": oldest_open_age_days,
            "undated_open": undated_open,
            "sla_days": ARB_REVIEW_SLA_DAYS,
        }

        attention = []
        overdue_ids = {review.id for review in overdue}
        for review in open_reviews:
            blocked = review.status in ARB_BLOCKED_OR_NOT_READY_STATUSES
            if not blocked and review.id not in overdue_ids:
                continue
            age_days = ages.get(review.id)
            if blocked:
                reason = "The review is waiting for information or a readiness decision."
                next_action = "Supply the missing evidence and unblock the review"
            else:
                reason = (
                    f"Open for {age_days} days against the {ARB_REVIEW_SLA_DAYS}-day SLA."
                )
                next_action = "Progress the review to a recorded decision"
            attention.append(
                {
                    "id": review.id,
                    "kind": "governance",
                    "source_label": "Governance and ARB",
                    "name": review.title,
                    "title": review.review_number,
                    "status": review.status,
                    "severity": (
                        "critical" if (review.priority or "").lower() == "critical" else "high"
                    ),
                    "age_days": age_days,
                    "reason": reason,
                    "evidence_url": f"/arb/reviews/{review.id}",
                    "next_action": next_action,
                    "action_url": f"/arb/reviews/{review.id}",
                }
            )
        return arb, attention

    @classmethod
    def portfolio_synthesis(cls) -> Dict[str, Any]:
        """Evidence-led Chief Architect command centre.

        Each concern retains its own source and denominator.  No composite
        enterprise-health score is created from unlike evidence.
        """
        from app.models.solution_models import Solution
        from app.modules.solutions_strategic.v2.services.conformance_reviewer import (
            ConformanceReviewer,
        )

        # The AutoTest exclusion that stood here is removed. Its rationale was
        # that "the weekly AutoTest purge can lag" -- which is true, and is an
        # argument for fixing the purge, not for hiding its backlog from the one
        # screen most likely to prompt someone to fix it. It also silently
        # discarded any customer solution whose name happened to match.
        solution_query = (
            Solution.query
            .filter(
                ~Solution.name.like("ZZ %"),
                ~Solution.name.like("Untitled Solution%"),
            )
        )
        solution_limit = 60
        eligible = solution_query.count()
        solutions = solution_query.order_by(Solution.id.desc()).limit(solution_limit).all()
        scored, flagged_total, attention = [], 0, []
        unassessed = 0
        unavailable = 0
        for s in solutions:
            try:
                r = ConformanceReviewer.review(s.id)
            except Exception as exc:  # noqa: BLE001
                unavailable += 1
                attention.append({
                    "id": s.id,
                    "kind": "conformance",
                    "source_label": "Solution conformance",
                    "name": s.name,
                    "title": "Conformance controls unavailable",
                    "status": "unavailable",
                    "severity": "critical",
                    "age_days": None,
                    "reason": "Conformance review could not be evaluated.",
                    "score": None,
                    "flagged": None,
                    "evidence_url": f"/solutions/{s.id}/review-packet",
                    "next_action": "Restore the required control and rerun the review",
                    "action_url": f"/solutions/{s.id}/review-packet",
                })
                logger.debug("conformance review unavailable for solution %s: %s", s.id, exc)
                continue
            if not r.get("success"):
                unavailable += 1
                attention.append({
                    "id": s.id,
                    "kind": "conformance",
                    "source_label": "Solution conformance",
                    "name": s.name,
                    "title": "Conformance controls unavailable",
                    "status": "unavailable",
                    "severity": "critical",
                    "age_days": None,
                    "reason": r.get("error") or "Conformance review could not be evaluated.",
                    "score": None,
                    "flagged": None,
                    "evidence_url": f"/solutions/{s.id}/review-packet",
                    "next_action": "Restore the required control and rerun the review",
                    "action_url": f"/solutions/{s.id}/review-packet",
                })
                continue
            if not r.get("controls_available", True):
                unavailable += 1
                attention.append({
                    "id": s.id,
                    "kind": "conformance",
                    "source_label": "Solution conformance",
                    "name": s.name,
                    "title": "Required controls unavailable",
                    "status": "unavailable",
                    "severity": "critical",
                    "age_days": None,
                    "reason": "Required controls unavailable: " + ", ".join(r.get("unavailable_checks", [])),
                    "score": None,
                    "flagged": None,
                    "evidence_url": f"/solutions/{s.id}/review-packet",
                    "next_action": "Restore the required control and rerun the review",
                    "action_url": f"/solutions/{s.id}/review-packet",
                })
                continue
            # Empty solutions carry no signal about conformance — an average
            # computed over them would reward emptiness (M-01). Exclude them
            # from the denominator and report the exclusion explicitly.
            if r.get("unassessed"):
                unassessed += 1
                attention.append({
                    "id": s.id,
                    "kind": "conformance",
                    "source_label": "Solution conformance",
                    "name": s.name,
                    "title": "Architecture evidence not yet modelled",
                    "status": "unassessed",
                    "severity": "high",
                    "age_days": None,
                    "reason": "No architecture content is modelled for conformance review.",
                    "score": None,
                    "flagged": 0,
                    "evidence_url": f"/solutions/{s.id}/review-packet",
                    "next_action": "Model the architecture content and run conformance",
                    "action_url": f"/solutions/{s.id}/review-packet",
                })
                continue
            scored.append(s.id)
            flagged_total += r.get("flagged", 0)
            if r.get("flagged"):
                attention.append({
                    "id": s.id,
                    "kind": "conformance",
                    "source_label": "Solution conformance",
                    "name": s.name,
                    "title": _n(r["flagged"], "conformance issue"),
                    "status": "not_ready",
                    "severity": "high",
                    "age_days": None,
                    "reason": f"{_n(r['flagged'], 'conformance issue')} need attention.",
                    "score": None,
                    "flagged": r["flagged"],
                    "evidence_url": f"/solutions/{s.id}/review-packet",
                    "next_action": "Resolve the findings and resubmit the review packet",
                    "action_url": f"/solutions/{s.id}/review-packet",
                })
        # No conformance average: the review counts findings, it does not score.
        avg = None

        architecture = cls._architecture_posture(solutions)
        strategic = cls._strategic_posture(solutions)
        delivery = cls._delivery_posture(solutions)
        risk_dependency, risk_attention = cls._risk_dependency_posture(solutions)
        arb, arb_attention = cls._arb_posture()
        attention.extend(risk_attention)
        attention.extend(arb_attention)
        attention = cls._prioritise_attention(attention)
        attention_total = len(attention)
        attention_displayed = min(attention_total, 10)

        return {
            "success": True,
            "generated_at": cls._utcnow().isoformat() + "Z",
            "scope": {
                "label": "Latest eligible solution records",
                "limit": solution_limit,
                "eligible": eligible,
                "in_scope": len(solutions),
                "truncated": eligible > len(solutions),
            },
            "solutions_reviewed": len(scored),
            "solutions_unassessed": unassessed,
            "solutions_unavailable": unavailable,
            "coverage": {
                "eligible": eligible,
                "in_scope": len(solutions),
                "evaluated": len(scored),
                "unassessed": unassessed,
                "unavailable": unavailable,
            },
            "avg_conformance": avg,
            "flagged_total": flagged_total if scored else None,
            "flagged_evaluated": len(scored),
            "strategic": strategic,
            "architecture": architecture,
            "delivery": delivery,
            "risk_dependency": risk_dependency,
            "attention": attention[:attention_displayed],
            # The untruncated, already-prioritised queue. The Chief Architect
            # Workbench merges enterprise-domain findings into this queue and
            # re-sorts; merging into the display-truncated list instead would let
            # a critical enterprise finding be crowded out by ten medium solution
            # findings that merely sorted first.
            "attention_all": attention,
            "attention_total": attention_total,
            "attention_displayed": attention_displayed,
            "attention_truncated": attention_total > attention_displayed,
            "worst": [item for item in attention if item["status"] == "not_ready"][:5],
            "arb": arb,
            "decisions_made": arb["decided"],
            "in_pipeline": arb["open"],
        }
