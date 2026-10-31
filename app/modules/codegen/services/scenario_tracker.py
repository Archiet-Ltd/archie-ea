"""Record BA's pass/fail verdicts on test scenarios.

Stores results in codegen_scenario_results table. Links failures to
rule names so AutoFixer can target specific broken rules.
"""
import logging
from datetime import datetime

from app.extensions import db
from app.models.mixins import TenantMixin

logger = logging.getLogger(__name__)


class ScenarioResult(TenantMixin, db.Model):
    """BA scenario test result. migration-exempt — db.create_all()"""
    __tablename__ = "codegen_scenario_results"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, nullable=False, index=True)
    scenario_id = db.Column(db.Integer, nullable=False)
    verdict = db.Column(db.String(20), nullable=False)  # pass, fail, partial
    notes = db.Column(db.Text)
    rule_name = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ScenarioTracker:
    """Record and query BA scenario verdicts."""

    def record(
        self,
        solution_id: int,
        scenario_id: int,
        verdict: str,
        notes: str | None = None,
        rule_name: str | None = None,
    ) -> dict:
        result = ScenarioResult(
            solution_id=solution_id,
            scenario_id=scenario_id,
            verdict=verdict,
            notes=notes,
            rule_name=rule_name,
        )
        db.session.add(result)
        db.session.commit()
        return {
            "id": result.id,
            "scenario_id": result.scenario_id,
            "verdict": result.verdict,
            "notes": result.notes,
            "rule_name": result.rule_name,
        }

    def get_results(self, solution_id: int) -> list[dict]:
        rows = ScenarioResult.query.filter_by(solution_id=solution_id).order_by(ScenarioResult.created_at).all()
        return [
            {"id": r.id, "scenario_id": r.scenario_id, "verdict": r.verdict, "notes": r.notes, "rule_name": r.rule_name}
            for r in rows
        ]

    def get_summary(self, solution_id: int) -> dict:
        results = self.get_results(solution_id)
        summary = {"pass": 0, "fail": 0, "partial": 0, "total": len(results)}
        for r in results:
            v = r["verdict"]
            if v in summary:
                summary[v] += 1
        return summary

    def get_failed_rules(self, solution_id: int) -> list[str]:
        results = self.get_results(solution_id)
        return [r["rule_name"] for r in results if r["verdict"] == "fail" and r["rule_name"]]

    def get_trend(self, solution_id: int, limit: int = 30) -> list[dict]:
        """Get daily pass/fail trend for a solution.

        Groups results by date, returns chronological list of daily summaries.
        """
        from sqlalchemy import func

        rows = (
            db.session.query(
                func.date(ScenarioResult.created_at).label("date"),
                ScenarioResult.verdict,
                func.count().label("count"),
            )
            .filter(ScenarioResult.solution_id == solution_id)
            .group_by(func.date(ScenarioResult.created_at), ScenarioResult.verdict)
            .order_by(func.date(ScenarioResult.created_at))
            .all()
        )

        # Pivot into per-day summaries
        by_date: dict[str, dict] = {}
        for row in rows:
            date_str = str(row.date)
            if date_str not in by_date:
                by_date[date_str] = {"date": date_str, "pass": 0, "fail": 0, "partial": 0}
            if row.verdict in by_date[date_str]:
                by_date[date_str][row.verdict] = row.count

        trend = list(by_date.values())
        return trend[-limit:] if len(trend) > limit else trend

    def get_failure_correlation(self, solution_id: int) -> list[dict]:
        """Get failure counts grouped by rule_name, ranked by frequency.

        Returns list of {rule_name, failure_count} sorted descending by count.
        Only includes results with verdict='fail' and a non-null rule_name.
        """
        from sqlalchemy import func

        rows = (
            db.session.query(
                ScenarioResult.rule_name,
                func.count().label("failure_count"),
            )
            .filter(
                ScenarioResult.solution_id == solution_id,
                ScenarioResult.verdict == "fail",
                ScenarioResult.rule_name.isnot(None),
            )
            .group_by(ScenarioResult.rule_name)
            .order_by(func.count().desc())
            .all()
        )

        return [{"rule_name": r.rule_name, "failure_count": r.failure_count} for r in rows]
