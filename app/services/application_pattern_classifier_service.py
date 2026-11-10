"""
Application Pattern Classifier Service

Classifies each ApplicationComponent in the enterprise portfolio by architectural pattern
(monolith, modular_monolith, microservice, saas, legacy, api_gateway, unknown).

Classification uses the LLM (via LLMService) as the primary engine with a deterministic
rule-based fallback for when no LLM provider is configured.  Results are persisted to
ApplicationComponent.arch_pattern (added by SA-003).

Primary entry points:
  - classify_applications(app_ids=None) -> list[dict]
      Returns per-app classification records.
  - classify_portfolio(batch_size=50) -> dict
      Classifies the full portfolio in batches; returns aggregate statistics.
"""

import concurrent.futures
import contextvars
import json
import logging
import time
from typing import Dict, List, Optional

from flask import current_app, g

from app import db
from app.models.application_portfolio import ApplicationComponent

logger = logging.getLogger(__name__)

# Hard ceiling on how long the LLM call behind classify_portfolio()/classify_applications()
# is allowed to take, end to end (including any internal retries/cross-provider failover in
# LLMService). Bounds the request regardless of which provider is configured or how it is
# misbehaving — see Task 4, P0 wave: this endpoint previously hung a worker indefinitely.
LLM_CLASSIFY_TIMEOUT_SECONDS = 60

# A dedicated small pool so a stalled call leaves an orphaned thread here rather than
# blocking (or competing for) the request-handling thread.
_llm_classify_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="llm-classify"
)


class LLMClassificationTimeoutError(RuntimeError):
    """Raised when the LLM call behind application pattern classification exceeds
    LLM_CLASSIFY_TIMEOUT_SECONDS. Callers must surface this as an error response —
    never silently substitute fabricated/fallback data for a timed-out call."""


def _log_orphaned_future_exception(future: "concurrent.futures.Future") -> None:
    """Done-callback for the classify-batch future.

    Once the caller times out (route already returned a 504), nobody else
    ever calls .result()/.exception() on this future — so without this
    callback, any exception the background call eventually raises (e.g. the
    LLMInteraction/cache writes in llm_service_impl.py failing without an app
    context) is silently dropped: exactly the "73 catch blocks that told
    nobody" anti-pattern CLAUDE.md calls out. Log it instead so it is at
    least observable, even though the request it belonged to is long gone.
    """
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        logger.error(
            "Orphaned application-pattern LLM call failed after the request "
            "that started it had already timed out: %s",
            exc,
            exc_info=exc,
        )


def _call_generate_from_prompt_in_app_context(app, prompt: str, org_id, timeout=None) -> str:
    """Run LLMService.generate_from_prompt inside *app*'s application context.

    The executor thread has no Flask context of its own. generate_from_prompt
    (and the interaction-logging/cache-write paths it calls into) use
    db.session and current_app, which raise "working outside of application
    context" without one — previously this ran bare, so a call that completed
    after its request had already timed out raised inside the thread pool
    with nothing ever observing it (see _log_orphaned_future_exception).

    org_id must be captured on the request thread and passed in explicitly —
    app/middleware/tenant_context.py sets g.current_org_id in a before_request
    handler that never runs for this executor thread. Without it,
    app/middleware/tenant_isolation.py skips tenant filtering entirely (it
    returns unfiltered when g.current_org_id is absent), so the
    APISettings.query.filter_by(enabled=True) lookup inside
    _call_llm_with_key_failover would see every organisation's enabled API
    keys and could bill this call to another tenant's provider account. Same
    fix as app/modules/ai_chat/routes/chat_core.py's run_agent() worker.
    """
    from app.services.llm_service import LLMService  # lazy import to avoid circular

    with app.app_context():
        g.current_org_id = org_id
        return LLMService.generate_from_prompt(
            prompt,
            use_cache=True,
            timeout=LLM_CLASSIFY_TIMEOUT_SECONDS if timeout is None else timeout,
            time_limit=timeout,
        )


VALID_PATTERNS = frozenset(
    {"monolith", "modular_monolith", "microservice", "saas", "legacy", "api_gateway", "unknown"}
)

# Enterprise ERP / CRM categories that imply monolith when no other signal overrides
_ERP_CRM_CATEGORIES = frozenset({"erp", "crm", "scm", "hcm"})

# Keywords in technology_stack or app name that indicate a pattern
_MICROSERVICE_KEYWORDS = frozenset(
    {"microservice", "microservices", "kubernetes", "k8s", "docker", "container", "containers",
     "service mesh", "istio", "envoy", "dapr", "grpc", "event-driven"}
)
_API_GATEWAY_KEYWORDS = frozenset(
    {"api gateway", "kong", "apigee", "mulesoft", "aws api gateway", "azure apim", "3scale",
     "gravitee", "tyk", "wso2"}
)
_LEGACY_KEYWORDS = frozenset(
    {"cobol", "mainframe", "as400", "as/400", "rpg", "fortran", "powerbuilder",
     "delphi", "vb6", "visual basic 6", "foxpro", "clipper"}
)
_MONOLITH_KEYWORDS = frozenset({"monolith", "monolithic"})


def _safe_lower(value) -> str:
    """Return lowercased string, tolerating None / non-string values."""
    if not value:
        return ""
    return str(value).lower()


def _extract_tech_stack_tokens(app: ApplicationComponent) -> frozenset:
    """
    Flatten technology_stack (stored as JSON text) and related fields into a
    single set of lower-cased tokens for keyword matching.
    """
    tokens: List[str] = []

    for field in (app.technology_stack, app.frameworks, app.programming_languages,
                  app.database_platforms, app.integration_methods):
        if not field:
            continue
        try:
            parsed = json.loads(field)
            if isinstance(parsed, list):
                tokens.extend(_safe_lower(t) for t in parsed)
            else:
                tokens.append(_safe_lower(parsed))
        except (json.JSONDecodeError, TypeError):
            tokens.append(_safe_lower(field))

    # Include name and description for keyword signals
    tokens.append(_safe_lower(app.name))
    tokens.append(_safe_lower(app.description or ""))
    tokens.append(_safe_lower(app.integration_pattern or ""))
    tokens.append(_safe_lower(app.message_queue or ""))

    return frozenset(" ".join(tokens).split())


def _rule_based_classify(app: ApplicationComponent) -> tuple:
    """
    Deterministic classification based on structured fields.

    Returns (arch_pattern: str, confidence: float, signals: list[str]).
    """
    signals: List[str] = []
    deployment = _safe_lower(app.deployment_model)
    category = _safe_lower(app.application_category or "")
    tokens = _extract_tech_stack_tokens(app)
    joined_tech = " ".join(tokens)

    # Rule 1 – SaaS deployment model is a high-confidence signal
    if deployment in ("saas", "cloud_saas"):
        return "saas", 0.90, ["deployment_model=saas"]  # fabricated-ok: designed confidence weight for this deterministic classifier rule

    # Rule 2 – explicit API gateway signals
    if any(kw in joined_tech for kw in _API_GATEWAY_KEYWORDS) or "api_gateway" in _safe_lower(
        app.application_type or ""
    ):
        return "api_gateway", 0.85, ["api_gateway_keyword"]  # fabricated-ok: designed confidence weight for this deterministic classifier rule

    # Rule 3 – microservice keywords in tech stack
    if any(kw in joined_tech for kw in _MICROSERVICE_KEYWORDS):
        return "microservice", 0.80, ["microservice_keyword"]  # fabricated-ok: designed confidence weight for this deterministic classifier rule

    # Rule 4 – legacy technology detected
    if any(kw in joined_tech for kw in _LEGACY_KEYWORDS):
        return "legacy", 0.85, ["legacy_keyword"]  # fabricated-ok: designed confidence weight for this deterministic classifier rule

    # Rule 5 – explicit monolith keyword
    if any(kw in joined_tech for kw in _MONOLITH_KEYWORDS):
        return "monolith", 0.80, ["monolith_keyword"]  # fabricated-ok: designed confidence weight for this deterministic classifier rule

    # Rule 6 – enterprise ERP/CRM category → monolith
    if category in _ERP_CRM_CATEGORIES:
        signals.append(f"category={category}")
        return "monolith", 0.70, signals  # fabricated-ok: designed confidence weight for this deterministic classifier rule

    # Rule 7 – on-premise commercial apps without microservice signal → monolith
    if deployment in ("on_premise", "on-premise", "on_prem") and app.vendor_name:
        signals.append("on_premise_commercial")
        return "monolith", 0.60, signals  # fabricated-ok: designed confidence weight for this deterministic classifier rule

    return "unknown", 0.40, ["no_signal"]  # fabricated-ok: designed confidence weight for the no-signal classifier fallthrough


# Per-request ceiling set by classify_applications(llm_timeout_seconds=...).
_llm_timeout_override: contextvars.ContextVar = contextvars.ContextVar(
    "llm_classify_timeout_override", default=None
)


def _llm_classify_batch(apps: List[ApplicationComponent]) -> List[Dict]:
    """
    Classify a batch of apps via LLM.

    Returns a list of dicts with keys: id, arch_pattern, confidence, source='llm'.
    Falls back to rule-based on any LLM failure.
    """
    app_summaries = []
    for app in apps:
        tech_preview = ""
        if app.technology_stack:
            try:
                stack = json.loads(app.technology_stack)
                tech_preview = ", ".join(stack[:5]) if isinstance(stack, list) else str(stack)
            except (json.JSONDecodeError, TypeError):
                tech_preview = str(app.technology_stack)[:100]

        app_summaries.append(
            f"- ID {app.id}: name={app.name!r}, "
            f"deployment_model={app.deployment_model!r}, "
            f"application_type={app.application_type!r}, "
            f"application_category={app.application_category!r}, "
            f"tech_stack=[{tech_preview}], "
            f"vendor={app.vendor_name!r}"
        )

    prompt = (
        "You are an enterprise architecture analyst. "
        "Classify each application by its architectural pattern. "
        "Valid patterns: monolith, modular_monolith, microservice, saas, legacy, api_gateway, unknown.\n\n"
        "Rules to apply:\n"
        "- deployment_model='saas' or similar → saas\n"
        "- microservice/kubernetes/container keywords → microservice\n"
        "- api gateway / APIM products → api_gateway\n"
        "- mainframe/cobol/AS400 → legacy\n"
        "- ERP/CRM/SCM commercial on-premise → monolith\n"
        "- otherwise → unknown\n\n"
        "Applications:\n"
        + "\n".join(app_summaries)
        + "\n\n"
        "Respond ONLY with a JSON array. Each item must have exactly these keys: "
        '"id" (integer), "arch_pattern" (string), "confidence" (float 0-1), "reasoning" (string). '
        "Example: [{\"id\": 1, \"arch_pattern\": \"saas\", \"confidence\": 0.9, "
        "\"reasoning\": \"deployment_model is saas\"}]"
    )

    try:
        app = current_app._get_current_object()
        # Capture the tenant on the request thread before handing off to the
        # executor - see _call_generate_from_prompt_in_app_context for why.
        from app.middleware.tenant_context import current_org_id as _current_org_id

        org_id = _current_org_id()
        timeout_seconds = _llm_timeout_override.get()
        limit = (
            LLM_CLASSIFY_TIMEOUT_SECONDS
            if timeout_seconds is None
            else min(timeout_seconds, LLM_CLASSIFY_TIMEOUT_SECONDS)
        )
        future = _llm_classify_executor.submit(
            _call_generate_from_prompt_in_app_context, app, prompt, org_id, limit
        )
        future.add_done_callback(_log_orphaned_future_exception)
        try:
            raw = future.result(timeout=limit)
        except concurrent.futures.TimeoutError as exc:
            raise LLMClassificationTimeoutError(
                f"LLM classification call exceeded the {limit}s timeout"
            ) from exc
        # Extract JSON array from response (LLM may wrap in markdown fences)
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                ln for ln in lines if not ln.startswith("```")
            )
        results = json.loads(raw)
        if not isinstance(results, list):
            raise ValueError("LLM response is not a JSON array")

        # Build lookup by id
        lookup = {int(r["id"]): r for r in results if "id" in r and "arch_pattern" in r}
        output = []
        for app in apps:
            if app.id in lookup:
                r = lookup[app.id]
                pattern = r.get("arch_pattern", "unknown")
                if pattern not in VALID_PATTERNS:
                    pattern = "unknown"
                output.append({
                    "id": app.id,
                    "arch_pattern": pattern,
                    "confidence": float(r.get("confidence", 0.5)),
                    "source": "llm",
                })
            else:
                # App not included in LLM response — fall back to rules
                pattern, confidence, _ = _rule_based_classify(app)
                output.append({
                    "id": app.id,
                    "arch_pattern": pattern,
                    "confidence": confidence,
                    "source": "rule_fallback",
                })
        return output

    except LLMClassificationTimeoutError:
        # Never silently substitute rule-based data for a call that timed out — the
        # caller (classify_applications/classify_portfolio) must let this propagate so
        # the route can return an explicit 5xx instead of a fabricated 200.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM batch classification failed (%s); falling back to rules", exc)
        return [
            {
                "id": app.id,
                "arch_pattern": p,
                "confidence": c,
                "source": "rule_fallback",
            }
            for app in apps
            for p, c, _ in [_rule_based_classify(app)]
        ]


class ApplicationPatternClassifierService:
    """
    Classifies ApplicationComponent records by architectural pattern.

    Uses LLM as primary engine; deterministic rule-based fallback for apps that
    cannot be reached via LLM or when no LLM provider is configured.
    """

    def __init__(self):
        # True when a time budget stopped LLM classification partway; the
        # remaining apps were classified by rules and labelled as such.
        self.llm_truncated = False
        # Plain line for the apps the AI provider did not reach in time; None when all were.
        self.unfinished_note = None

    def classify_applications(
        self,
        app_ids: Optional[List[int]] = None,
        batch_size: int = 50,
        use_llm: bool = True,
        time_budget_seconds: Optional[float] = None,
        llm_timeout_seconds: Optional[float] = None,
    ) -> List[Dict]:
        """
        Classify applications and persist arch_pattern to the database.

        Args:
            app_ids: Optional list of ApplicationComponent IDs to process.
                     When None, all applications are processed.
            batch_size: Number of apps to send to the LLM per request.
            use_llm: Whether to attempt LLM classification (True by default).
                     Set to False to force rule-based classification only.

        Returns:
            List of dicts, one per application:
                {app_id, app_name, arch_pattern, confidence}
        """
        query = ApplicationComponent.query
        if app_ids:
            query = query.filter(ApplicationComponent.id.in_(app_ids))
        apps: List[ApplicationComponent] = query.all()

        self.llm_truncated = False
        self.unfinished_note = None
        token = _llm_timeout_override.set(llm_timeout_seconds)
        try:
            return self._classify_batches(apps, batch_size, use_llm, time_budget_seconds)
        finally:
            _llm_timeout_override.reset(token)

    def _classify_batches(self, apps, batch_size, use_llm, time_budget_seconds) -> List[Dict]:
        results: List[Dict] = []
        deadline = (
            time.monotonic() + time_budget_seconds
            if time_budget_seconds is not None
            else None
        )

        # Process in batches. Each LLM batch is one outbound call with a client
        # timeout in the 60-90s range — but 920 apps at batch_size=50 is ~19
        # sequential calls, which is what stalled this endpoint for 10+ minutes.
        # The optional wall-clock budget stops issuing LLM calls once spent;
        # remaining apps get the service's documented deterministic fallback,
        # and every record says which engine produced it.
        llm_answered = False
        rules_count = 0
        base_limit = _llm_timeout_override.get()
        for batch_start in range(0, len(apps), batch_size):
            batch = apps[batch_start: batch_start + batch_size]

            llm_this_batch = use_llm and not self.llm_truncated
            if llm_this_batch and deadline is not None and time.monotonic() > deadline:
                llm_this_batch = False
                self.llm_truncated = True

            id_map = {}
            if llm_this_batch:
                # Each call gets only what is left of the one request budget.
                if deadline is not None:
                    left = max(0.1, deadline - time.monotonic())
                    token = _llm_timeout_override.set(left if base_limit is None else min(base_limit, left))
                else:
                    token = None
                try:
                    classified = _llm_classify_batch(batch)
                    id_map = {r["id"]: r for r in classified}
                    llm_answered = True
                except LLMClassificationTimeoutError:
                    if not llm_answered:
                        raise  # nothing finished: an explicit error, never made-up answers
                    # Keep the batches that finished; the rest are labelled as rules-based.
                    self.llm_truncated = True
                finally:
                    if token is not None:
                        _llm_timeout_override.reset(token)

            for app in batch:
                if app.id in id_map:
                    pattern = id_map[app.id]["arch_pattern"]
                    confidence = id_map[app.id]["confidence"]
                    source = id_map[app.id].get("source", "llm")
                else:
                    pattern, confidence, _ = _rule_based_classify(app)
                    source = "rules"
                    rules_count += 1

                # Persist
                app.arch_pattern = pattern
                results.append({
                    "app_id": app.id,
                    "app_name": app.name,
                    "arch_pattern": pattern,
                    "confidence": round(confidence, 4),
                    "source": source,
                })

        if self.llm_truncated and rules_count:
            self.unfinished_note = (
                "The AI provider did not answer for %d applications in time, so their patterns come "
                "from the built-in rules instead. Try again to have the AI classify them."
                % rules_count
            )

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return results

    def classify_portfolio(
        self,
        batch_size: int = 50,
        time_budget_seconds: Optional[float] = None,
        llm_timeout_seconds: Optional[float] = None,
    ) -> Dict:
        """
        Classify the full application portfolio in batches.

        Returns aggregate statistics:
            {classified: N, by_pattern: {pattern: count},
             confidence_distribution: {high/medium/low: count},
             by_source: {llm/rules: count}, llm_truncated: bool}
        """
        records = self.classify_applications(
            app_ids=None,
            batch_size=batch_size,
            time_budget_seconds=time_budget_seconds,
            llm_timeout_seconds=llm_timeout_seconds,
        )

        by_pattern: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        confidence_distribution: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}

        for rec in records:
            pattern = rec["arch_pattern"]
            by_pattern[pattern] = by_pattern.get(pattern, 0) + 1
            source = rec["source"]
            by_source[source] = by_source.get(source, 0) + 1

            confidence = rec["confidence"]
            if confidence >= 0.75:  # fabricated-ok: threshold constant for bucketing, not a displayed value
                confidence_distribution["high"] += 1
            elif confidence >= 0.50:
                confidence_distribution["medium"] += 1
            else:
                confidence_distribution["low"] += 1

        return {
            "classified": len(records),
            "by_pattern": by_pattern,
            "by_source": by_source,
            "llm_truncated": self.llm_truncated,
            "note": self.unfinished_note,
            "confidence_distribution": confidence_distribution,
        }
