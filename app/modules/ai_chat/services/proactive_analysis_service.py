"""
ProactiveAnalysisService — rule-based insight generation. No LLM calls.

Called in a background daemon thread on blueprint page load.
Stores CopilotInsight records for unseen findings.
Target: < 200ms per solution analysis.
"""
import logging
from datetime import datetime, timedelta

from app import db

logger = logging.getLogger(__name__)

COMPLETENESS_GAP_THRESHOLD_DAYS = 14
STALENESS_THRESHOLD_DAYS = 30
ARB_DEADLINE_WARNING_DAYS = 7
ARB_READY_COMPLETENESS_PCT = 60


class ProactiveAnalysisService:
    def analyse_solution(self, solution_id: int) -> list:
        """
        Run all rule checks. Returns list of CopilotInsight instances (NOT saved to DB yet).
        Returns [] on any error — never raises.
        """
        try:
            sol = self._get_solution(solution_id)
            if not sol:
                return []
            insights = []
            insights += self._check_completeness_gap(sol)
            insights += self._check_arb_deadline(sol)
            insights += self._check_staleness(sol)
            insights += self._check_portfolio_duplicates(sol)
            insights += self._check_available_patterns(sol)
            return insights
        except Exception as exc:
            logger.debug("ProactiveAnalysisService.analyse_solution(%s) failed: %s", solution_id, exc)
            return []

    def _get_solution(self, solution_id: int):
        from app.models.solution_models import Solution
        return Solution.query.get(solution_id)

    def _get_completeness_pct(self, solution_id: int):
        """Rough completeness: linked ArchiMate elements / 20, capped at 100.

        Returns None — never 0 — when the count cannot be read. This figure is
        printed verbatim into insight titles, bodies and the suggested_query
        that seeds the assistant, so a fabricated 0 would surface as a CRITICAL
        "blueprint 0% complete" warning about a solution nobody has measured.
        Every caller must skip the check when it is None.
        """
        try:
            from app.models.solution_models import SolutionArchiMateElement
            count = SolutionArchiMateElement.query.filter_by(solution_id=solution_id).count()
            return min(100, count * 5)
        except Exception:
            logger.exception("_get_completeness_pct(%s) failed", solution_id)
            return None

    def _check_completeness_gap(self, sol) -> list:
        from app.models.copilot_insight import CopilotInsight, InsightType, InsightSeverity
        age_days = (datetime.utcnow() - (sol.created_at or datetime.utcnow())).days
        if age_days < COMPLETENESS_GAP_THRESHOLD_DAYS:
            return []
        pct = self._get_completeness_pct(sol.id)
        if pct is None or pct > 5:
            # Unknown completeness is not near-zero completeness: raise nothing.
            return []
        return [CopilotInsight(
            solution_id=sol.id,
            insight_type=InsightType.COMPLETENESS_GAP.value,
            title=f"Blueprint has no content after {age_days} days",
            body=(
                f"This solution was created {age_days} days ago but has near-zero "
                f"completeness. The blueprint sections need ArchiMate elements and relationships."
            ),
            suggested_query=(
                f"This solution has been in progress for {age_days} days but the blueprint "
                f"completeness is near zero. Help me identify what I need to add to get started. "
                f"What are the essential elements for the current phase?"
            ),
            severity=InsightSeverity.WARNING.value,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )]

    def _check_arb_deadline(self, sol) -> list:
        from app.models.copilot_insight import CopilotInsight, InsightType, InsightSeverity
        try:
            from app.models.architecture_review_board import ARBReviewRequest
            review = (
                ARBReviewRequest.query
                .filter_by(solution_id=sol.id)
                .filter(ARBReviewRequest.status == "pending")
                .first()
            )
            if not review or not hasattr(review, 'review_date') or not review.review_date:
                return []
            days_to_review = (review.review_date - datetime.utcnow()).days
            if days_to_review > ARB_DEADLINE_WARNING_DAYS:
                return []
            pct = self._get_completeness_pct(sol.id)
            if pct is None or pct >= ARB_READY_COMPLETENESS_PCT:
                # pct is quoted three times below — never quote a guess.
                return []
            return [CopilotInsight(
                solution_id=sol.id,
                insight_type=InsightType.ARB_DEADLINE_RISK.value,
                title=f"ARB review in {days_to_review} days — blueprint {pct}% complete",
                body=(
                    f"ARB review is scheduled in {days_to_review} days. "
                    f"The blueprint is only {pct}% complete. "
                    f"Minimum recommended: {ARB_READY_COMPLETENESS_PCT}%."
                ),
                suggested_query=(
                    f"My ARB review is in {days_to_review} days and the blueprint is {pct}% complete. "
                    f"What are the most critical sections I need to complete before ARB review? "
                    f"Help me prioritise."
                ),
                severity=InsightSeverity.CRITICAL.value,
                expires_at=review.review_date,
            )]
        except Exception:
            logger.exception("_check_arb_deadline(%s) failed", getattr(sol, "id", None))
            return []

    def _check_staleness(self, sol) -> list:
        from app.models.copilot_insight import CopilotInsight, InsightType, InsightSeverity
        if (getattr(sol, 'status', '') or '') != 'in_progress':
            return []
        last_update = getattr(sol, 'updated_at', None) or getattr(sol, 'created_at', None)
        if not last_update:
            return []
        stale_days = (datetime.utcnow() - last_update).days
        if stale_days < STALENESS_THRESHOLD_DAYS:
            return []
        return [CopilotInsight(
            solution_id=sol.id,
            insight_type=InsightType.STALE_SOLUTION.value,
            title=f"No updates in {stale_days} days",
            body=f"This solution is marked in-progress but has not been updated in {stale_days} days.",
            suggested_query=(
                f"This solution has not been updated in {stale_days} days. "
                f"Review the current state: what is complete, what is missing, "
                f"and what are the next steps to move forward?"
            ),
            severity=InsightSeverity.INFO.value,
            expires_at=datetime.utcnow() + timedelta(days=14),
        )]

    def _check_portfolio_duplicates(self, sol) -> list:
        from app.models.copilot_insight import CopilotInsight, InsightType, InsightSeverity
        try:
            from app.models.solution_models import Solution
            domain = getattr(sol, 'business_domain', None)
            phase = getattr(sol, 'adm_phase', None)
            org_id = getattr(sol, 'organization_id', None)
            if not domain or not phase or org_id is None:
                return []
            first_word = (sol.name or '').split()[0].lower() if sol.name else ''
            if len(first_word) < 4:
                return []
            candidates = (
                Solution.query
                .filter(
                    Solution.organization_id == org_id,
                    Solution.business_domain == domain,
                    Solution.adm_phase == phase,
                    Solution.id != sol.id,
                )
                .limit(20)
                .all()
            )
            similar = [
                c for c in candidates
                if (c.name or '').lower().startswith(first_word)
            ]
            if not similar:
                return []
            names = ', '.join(f'"{c.name}"' for c in similar[:3])
            return [CopilotInsight(
                solution_id=sol.id,
                insight_type=InsightType.PORTFOLIO_DUPLICATE.value,
                title=f"Potentially similar solution exists: {similar[0].name}",
                body=f"Solutions with similar names exist in the same domain and phase: {names}",
                suggested_query=(
                    f"There are potentially similar solutions in our portfolio: {names}. "
                    f"How does this solution differ from them? Should we merge or differentiate?"
                ),
                severity=InsightSeverity.INFO.value,
                expires_at=datetime.utcnow() + timedelta(days=30),
            )]
        except Exception:
            logger.exception("_check_portfolio_duplicates(%s) failed", getattr(sol, "id", None))
            return []

    def _check_available_patterns(self, sol) -> list:
        from app.models.copilot_insight import CopilotInsight, InsightType, InsightSeverity
        try:
            from app.models.solution_models import Solution, solution_applications
            org_id = getattr(sol, 'organization_id', None)
            if org_id is None:
                return []
            my_apps = {
                row.application_component_id
                for row in db.session.execute(
                    solution_applications.select().where(
                        solution_applications.c.solution_id == sol.id
                    )
                ).fetchall()
            }
            if not my_apps:
                return []
            domain = getattr(sol, 'business_domain', None)
            if not domain:
                return []
            candidate_solutions = Solution.query.filter(
                Solution.organization_id == org_id,
                Solution.business_domain == domain,
                Solution.id != sol.id,
            ).limit(20).all()
            # A solution whose completeness could not be read is not evidence of
            # high completeness, so it is left out rather than counted as 0.
            high_completeness_solutions = []
            for s in candidate_solutions:
                s_pct = self._get_completeness_pct(s.id)
                if s_pct is not None and s_pct >= 60:
                    high_completeness_solutions.append(s)
            matches = []
            for s in high_completeness_solutions:
                their_apps = {
                    row.application_component_id
                    for row in db.session.execute(
                        solution_applications.select().where(
                            solution_applications.c.solution_id == s.id
                        )
                    ).fetchall()
                }
                if len(my_apps & their_apps) >= 2:
                    matches.append(s)
            if not matches:
                return []
            match_names = ', '.join(f'"{m.name}"' for m in matches[:2])
            return [CopilotInsight(
                solution_id=sol.id,
                insight_type=InsightType.PATTERN_AVAILABLE.value,
                title=f"Reusable pattern available from {matches[0].name}",
                body=(
                    f"High-completeness solutions in the same domain share applications with this one: "
                    f"{match_names}. Their architecture patterns may be reusable."
                ),
                suggested_query=(
                    f"Solutions {match_names} share applications with this solution and are highly complete. "
                    f"What architecture patterns from those solutions should I adopt here?"
                ),
                severity=InsightSeverity.INFO.value,
                expires_at=datetime.utcnow() + timedelta(days=14),
            )]
        except Exception:
            logger.exception("_check_available_patterns(%s) failed", getattr(sol, "id", None))
            return []
