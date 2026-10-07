"""
-> app.modules.ai_chat.services.llm_service

LLM Cost Tracker and Budget Enforcement Service

Tracks LLM API costs, enforces budgets, and provides cost analytics for Example Corp UK.
Addresses Gap #3: No Cost Control or Budget Management
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Optional, Tuple

from flask import current_app
from sqlalchemy import func

from app import db
from app.models import LLMInteraction
from app.utils.tenant_sql import current_org_id

# from app.services.decorators import transactional  # Temporarily disabled

logger = logging.getLogger(__name__)


class LLMCostTracker:
    """
    Tracks and enforces LLM API cost budgets.

    Features:
    - Real-time cost tracking per user, project, department
    - Budget enforcement with soft/hard limits
    - Cost forecasting and alerts
    - Detailed cost analytics and reporting
    """

    # Budget thresholds (in GBP)
    DEFAULT_MONTHLY_BUDGET = Decimal("500.00")  # £500/month default
    SOFT_LIMIT_THRESHOLD = 0.80  # Alert at 80%
    HARD_LIMIT_THRESHOLD = 0.95  # Block at 95%

    def __init__(self):
        """Initialize the cost tracker."""
        self.budgets = {}  # Cache for budget settings

    def track_interaction(
        self,
        model_name: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        department: Optional[str] = None,
    ) -> Decimal:
        """
        Calculate and track cost for an LLM interaction.

        Args:
            model_name: Name of the model used
            provider: Provider name (openai, anthropic, etc.)
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            user_id: User making the request
            project_id: Project/architecture ID
            department: Department name (e.g., 'Enterprise Architecture')

        Returns:
            Cost in GBP
        """
        cost = self._calculate_cost(provider, model_name, input_tokens, output_tokens)

        # Persist interaction to database for budget tracking and analytics
        try:
            interaction = LLMInteraction(
                model_name=model_name,
                provider=provider,
                token_count_input=input_tokens,
                token_count_output=output_tokens,
                cost=cost,
                user_id=user_id,
                pipeline_stage_id=project_id,
            )
            db.session.add(interaction)
            db.session.commit()
            logger.info(
                f"LLM Cost Tracked: {provider}/{model_name} - "
                f"{input_tokens}in + {output_tokens}out = £{cost:.4f} [DB record #{interaction.id}]"
            )
        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to persist LLM interaction to database: {e}. "
                f"Cost was: {provider}/{model_name} - "
                f"{input_tokens}in + {output_tokens}out = £{cost:.4f}"
            )

        return cost

    def check_budget_before_call(
        self,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        estimated_tokens: int = 2000,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if budget allows for an LLM call before making it.

        Args:
            user_id: User making the request
            project_id: Project ID
            estimated_tokens: Estimated token count for the call

        Returns:
            Tuple of (allowed: bool, message: Optional[str])
        """
        # Get current month's spending
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Calculate spending by user
        if user_id:
            user_spending = self._get_user_spending(user_id, month_start)
            user_budget = self._get_user_budget(user_id)

            if user_spending >= user_budget * Decimal(str(self.HARD_LIMIT_THRESHOLD)):
                return False, (
                    f"User monthly budget limit reached (£{user_spending:.2f} / £{user_budget:.2f}). "
                    "Please contact your administrator to increase your budget."
                )

            if user_spending >= user_budget * Decimal(str(self.SOFT_LIMIT_THRESHOLD)):
                logger.warning(
                    f"User {user_id} approaching budget limit: "
                    f"£{user_spending:.2f} / £{user_budget:.2f}"
                )

        # Calculate spending by project
        if project_id:
            project_spending = self._get_project_spending(project_id, month_start)
            project_budget = self._get_project_budget(project_id)

            if project_spending >= project_budget * Decimal(str(self.HARD_LIMIT_THRESHOLD)):
                return False, (
                    f"Project monthly budget limit reached (£{project_spending:.2f} / £{project_budget:.2f}). "
                    "Please optimize your prompts or request a budget increase."
                )

        # Calculate overall organizational spending
        org_spending = self._get_organization_spending(month_start)
        org_budget = self._get_organization_budget()

        if org_spending >= org_budget * Decimal(str(self.HARD_LIMIT_THRESHOLD)):
            return False, (
                f"Organization monthly budget limit reached (£{org_spending:.2f} / £{org_budget:.2f}). "
                "Please contact the Enterprise Architecture team."
            )

        return True, None

    def _calculate_cost(
        self, provider: str, model: str, input_tokens: int, output_tokens: int
    ) -> Decimal:
        """
        Calculate cost based on provider and model pricing.

        Pricing as of Nov 2025 (in USD, converted to GBP at 1.27 rate):
        """
        # USD to GBP conversion rate
        USD_TO_GBP = Decimal("0.787")  # 1 USD = 0.787 GBP (approximate)

        cost_usd = Decimal("0")
        PER_1K = 1000  # fabricated-ok: literal 1000-token divisor for per-1K pricing, a real constant

        if provider == "openai":
            if "gpt - 4" in model.lower():
                if "turbo" in model.lower() or "1106" in model or "0125" in model:
                    # GPT - 4 Turbo: $0.01/1K input, $0.03/1K output
                    cost_usd = Decimal(input_tokens) / PER_1K * Decimal("0.01") + Decimal(
                        output_tokens
                    ) / PER_1K * Decimal("0.03")
                else:
                    # GPT - 4: $0.03/1K input, $0.06/1K output
                    cost_usd = Decimal(input_tokens) / PER_1K * Decimal("0.03") + Decimal(
                        output_tokens
                    ) / PER_1K * Decimal("0.06")
            elif "gpt - 3.5" in model.lower():
                # GPT - 3.5 Turbo: $0.0005/1K input, $0.0015/1K output
                cost_usd = Decimal(input_tokens) / PER_1K * Decimal("0.0005") + Decimal(
                    output_tokens
                ) / PER_1K * Decimal("0.0015")
            else:
                # Default to GPT - 4 pricing
                cost_usd = Decimal(input_tokens) / PER_1K * Decimal("0.03") + Decimal(
                    output_tokens
                ) / PER_1K * Decimal("0.06")

        elif provider == "anthropic":
            if "opus" in model.lower():
                # Claude 3 Opus: $0.015/1K input, $0.075/1K output
                cost_usd = Decimal(input_tokens) / PER_1K * Decimal("0.015") + Decimal(
                    output_tokens
                ) / PER_1K * Decimal("0.075")
            elif "sonnet" in model.lower():
                # Claude 3.5 Sonnet: $0.003/1K input, $0.015/1K output
                cost_usd = Decimal(input_tokens) / PER_1K * Decimal("0.003") + Decimal(
                    output_tokens
                ) / PER_1K * Decimal("0.015")
            elif "haiku" in model.lower():
                # Claude 3 Haiku: $0.00025/1K input, $0.00125/1K output
                cost_usd = Decimal(input_tokens) / PER_1K * Decimal("0.00025") + Decimal(
                    output_tokens
                ) / PER_1K * Decimal("0.00125")
            else:
                # Default to Sonnet pricing
                cost_usd = Decimal(input_tokens) / PER_1K * Decimal("0.003") + Decimal(
                    output_tokens
                ) / PER_1K * Decimal("0.015")

        # Convert to GBP
        cost_gbp = cost_usd * USD_TO_GBP
        return cost_gbp

    def _get_user_spending(self, user_id: int, since: datetime) -> Decimal:
        """Get total spending for a user since a given date."""
        result = (
            db.session.query(func.sum(LLMInteraction.cost))
            .filter(LLMInteraction.user_id == user_id, LLMInteraction.created_at >= since)
            .scalar()
        )

        return Decimal(str(result)) if result else Decimal("0")

    def _get_project_spending(self, project_id: int, since: datetime) -> Decimal:
        """Get total spending for a project since a given date."""
        # Join with pipeline_stages to get architecture_id
        from app.models import PipelineStage

        result = (
            db.session.query(func.sum(LLMInteraction.cost))
            .join(PipelineStage, LLMInteraction.pipeline_stage_id == PipelineStage.id)
            .filter(PipelineStage.architecture_id == project_id, LLMInteraction.created_at >= since)
            .scalar()
        )

        return Decimal(str(result)) if result else Decimal("0")

    def _get_organization_spending(self, since: datetime) -> Decimal:
        """Get total organization spending since a given date."""
        org_id = current_org_id()
        if org_id is None:
            return Decimal("0")

        result = (
            db.session.query(func.sum(LLMInteraction.cost))
            .filter(
                LLMInteraction.created_at >= since,
                LLMInteraction.organization_id == org_id,
            )
            .scalar()
        )

        return Decimal(str(result)) if result else Decimal("0")

    def _get_user_budget(self, user_id: int) -> Decimal:
        """Get monthly budget for a user (configurable via LLM_USER_MONTHLY_BUDGET)."""
        budget = current_app.config.get("LLM_USER_MONTHLY_BUDGET", self.DEFAULT_MONTHLY_BUDGET)
        return Decimal(str(budget))

    def _get_project_budget(self, project_id: int) -> Decimal:
        """Get monthly budget for a project (configurable via LLM_PROJECT_MONTHLY_BUDGET)."""
        default = self.DEFAULT_MONTHLY_BUDGET * Decimal("5")  # £2500 per project
        budget = current_app.config.get("LLM_PROJECT_MONTHLY_BUDGET", default)
        return Decimal(str(budget))

    def _get_organization_budget(self) -> Decimal:
        """Get organization-wide monthly budget (configurable via LLM_ORG_MONTHLY_BUDGET)."""
        default = self.DEFAULT_MONTHLY_BUDGET * Decimal("100")  # £50,000 org-wide
        budget = current_app.config.get("LLM_ORG_MONTHLY_BUDGET", default)
        return Decimal(str(budget))

    def get_cost_report(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        group_by: str = "provider",
        organization_id: Optional[int] = None,  # TRNT-072: tenant scoping
    ) -> Dict:
        """
        Generate cost report for specified time period.

        Args:
            start_date: Start of reporting period (default: start of month)
            end_date: End of reporting period (default: now)
            group_by: Grouping dimension ('provider', 'model', 'day', 'user')

        Returns:
            Dict with cost breakdown and analytics
        """
        if not start_date:
            start_date = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if not end_date:
            end_date = datetime.utcnow()

        # Get all interactions in period
        interactions_q = LLMInteraction.query.filter(
            LLMInteraction.created_at >= start_date, LLMInteraction.created_at <= end_date
        )
        if organization_id is not None:
            interactions_q = interactions_q.filter(LLMInteraction.organization_id == organization_id)
        interactions = interactions_q.all()

        # Calculate totals
        total_cost = sum(i.cost for i in interactions if i.cost)
        total_calls = len(interactions)
        total_input_tokens = sum(i.token_count_input for i in interactions if i.token_count_input)
        total_output_tokens = sum(
            i.token_count_output for i in interactions if i.token_count_output
        )

        # Group by specified dimension
        grouped = {}
        for interaction in interactions:
            if group_by == "provider":
                key = interaction.provider
            elif group_by == "model":
                key = interaction.model_name
            elif group_by == "day":
                key = interaction.created_at.strftime("%Y-%m-%d")
            else:
                key = "unknown"

            if key not in grouped:
                grouped[key] = {
                    "cost": Decimal("0"),
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }

            grouped[key]["cost"] += interaction.cost if interaction.cost else Decimal("0")
            grouped[key]["calls"] += 1
            grouped[key]["input_tokens"] += interaction.token_count_input or 0
            grouped[key]["output_tokens"] += interaction.token_count_output or 0

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "totals": {
                "cost": float(total_cost) if total_cost else 0,
                "calls": total_calls,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "avg_cost_per_call": float(total_cost / total_calls) if total_calls > 0 else 0,
            },
            "breakdown": {
                key: {
                    "cost": float(data["cost"]),
                    "calls": data["calls"],
                    "input_tokens": data["input_tokens"],
                    "output_tokens": data["output_tokens"],
                }
                for key, data in grouped.items()
            },
        }

    def get_budget_status(self) -> Dict:
        """
        Get current budget status for the organization.

        Returns:
            Dict with budget utilization metrics
        """
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        org_spending = self._get_organization_spending(month_start)
        org_budget = self._get_organization_budget()

        days_in_month = (
            month_start.replace(month=month_start.month % 12 + 1, day=1) - timedelta(days=1)
        ).day
        days_elapsed = (datetime.utcnow() - month_start).days + 1

        # Calculate projected spending
        if days_elapsed > 0:
            daily_rate = org_spending / Decimal(str(days_elapsed))
            projected_monthly = daily_rate * Decimal(str(days_in_month))
        else:
            projected_monthly = Decimal("0")

        return {
            "current_month": {
                "spent": float(org_spending),
                "budget": float(org_budget),
                "percentage": float((org_spending / org_budget * 100) if org_budget > 0 else 0),
                "remaining": float(org_budget - org_spending),
            },
            "projection": {
                "estimated_monthly_total": float(projected_monthly),
                "projected_overage": float(max(Decimal("0"), projected_monthly - org_budget)),
                "on_track": projected_monthly <= org_budget,
            },
            "alerts": {
                "soft_limit_reached": org_spending
                >= org_budget * Decimal(str(self.SOFT_LIMIT_THRESHOLD)),
                "hard_limit_reached": org_spending
                >= org_budget * Decimal(str(self.HARD_LIMIT_THRESHOLD)),
            },
        }
