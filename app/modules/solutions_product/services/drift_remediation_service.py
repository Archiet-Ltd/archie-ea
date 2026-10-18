"""Drift Remediation Service — detect and remediate spec drift between platform and deployed services.

Handles:
1. Webhook firing on spec changes and drift detection
2. Scheduled compliance checks
3. Auto-publish on spec confirmation

Uses `requests` (already in requirements.txt) for webhook HTTP calls.
"""

import json
import logging
import os
import re
import time
from datetime import datetime

import requests

from app import db

logger = logging.getLogger(__name__)

# Exponential backoff base delays (seconds)
_RETRY_DELAYS = [1, 2, 4]

# Webhook HTTP timeout
_WEBHOOK_TIMEOUT_SECONDS = 10

# The only environment variables a webhook may name as its auth token source.
# A name outside this pattern is never read, so a stored row cannot be used to
# send DATABASE_URL, SECRET_KEY or an API key to an outside URL.
WEBHOOK_AUTH_ENV_PATTERN = re.compile(r"^WEBHOOK_TOKEN_[A-Z0-9_]{1,40}$")


def is_allowed_auth_header_env(name):
    return isinstance(name, str) and bool(WEBHOOK_AUTH_ENV_PATTERN.fullmatch(name))


# Valid event types
VALID_EVENT_TYPES = {"spec_changed", "drift_detected", "compliance_failed"}


class DriftRemediationService:
    """Detect and remediate spec drift between platform and deployed services."""

    def on_spec_confirmed(self, solution_id, element_id):
        """Called when an architect confirms a spec change.

        1. Auto-publish new spec version (if auto_publish enabled)
        2. Fire webhooks for spec_changed event
        3. Log the change
        """
        from app.models.solution_models import Solution
        from app.models.published_api_spec import PublishedAPISpec

        solution = Solution.query.get(solution_id)
        if not solution:
            logger.warning("on_spec_confirmed: solution %s not found", solution_id)
            return

        # Check if auto_publish is enabled in solution metadata
        meta = self._get_metadata(solution)
        auto_publish = meta.get("compliance_schedule", {}).get("auto_publish", False)

        spec_version = None
        if auto_publish:
            # Find latest published spec and bump version
            latest_spec = (
                PublishedAPISpec.query
                .filter_by(solution_id=solution_id)
                .order_by(PublishedAPISpec.published_at.desc())
                .first()
            )
            if latest_spec:
                spec_version = latest_spec.spec_version
                logger.info(
                    "Auto-publish enabled for solution %s, latest spec version: %s",
                    solution_id, spec_version,
                )

        # Build webhook payload
        element_name = self._get_element_name(solution_id, element_id)
        payload = {
            "event": "spec_changed",
            "solution_id": solution_id,
            "solution_name": solution.name if hasattr(solution, "name") else str(solution_id),
            "spec_version": spec_version,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "changes": {
                "element_id": element_id,
                "element_name": element_name,
                "change_type": "spec_confirmed",
            },
            "platform_url": f"https://archie.company.com/solutions/{solution_id}",
        }

        # Fire webhooks
        self.fire_webhooks(solution_id, "spec_changed", payload)

        logger.info(
            "Spec confirmed for solution %s element %s, webhooks fired",
            solution_id, element_id,
        )

    def fire_webhooks(self, solution_id, event_type, payload):
        """Fire all enabled webhooks for a solution matching the event type.

        - POST to webhook URL with JSON payload
        - Include event_type, solution_id, timestamp
        - Retry up to retry_count times with exponential backoff
        - Log success/failure, update last_triggered_at and last_status
        - Use auth header from env var if configured
        """
        from app.models.spec_webhook import SpecWebhook

        webhooks = (
            SpecWebhook.query
            .filter_by(solution_id=solution_id, enabled=True)
            .all()
        )

        results = []
        for webhook in webhooks:
            # Check if this webhook subscribes to this event type
            event_types = webhook.event_types or []
            if event_type not in event_types:
                continue

            success = self._fire_single_webhook(webhook, payload)
            results.append({
                "webhook_id": webhook.id,
                "url": webhook.url,
                "success": success,
            })

        return results

    def _fire_single_webhook(self, webhook, payload):
        """Fire a single webhook with retry logic.

        Returns True on success, False on failure.
        """
        from app.utils.ssrf_guard import BlockedOutboundURL, validate_user_supplied_url

        headers = {"Content-Type": "application/json"}

        # Re-check the target at send time: rows stored before validation
        # existed, or a host whose DNS now points inside, must not be called.
        try:
            validate_user_supplied_url(webhook.url)
        except (BlockedOutboundURL, ValueError) as exc:
            logger.warning("Webhook %s not sent: target refused (%s)", webhook.id, exc)
            webhook.last_triggered_at = datetime.utcnow()
            webhook.last_status = "failed"
            webhook.failure_count = (webhook.failure_count or 0) + 1
            db.session.commit()
            return False

        # Inject auth header from env var if configured. Only allow-listed
        # names are read; anything else is ignored without touching os.environ.
        if webhook.auth_header_env:
            if is_allowed_auth_header_env(webhook.auth_header_env):
                auth_token = os.environ.get(webhook.auth_header_env)
                if auth_token:
                    headers["Authorization"] = f"Bearer {auth_token}"
            else:
                logger.warning(
                    "Webhook %s has a non-allow-listed auth_header_env; ignored",
                    webhook.id,
                )

        max_retries = webhook.retry_count or 3
        last_error = None

        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    webhook.url,
                    json=payload,
                    headers=headers,
                    timeout=_WEBHOOK_TIMEOUT_SECONDS,
                    verify=True,
                    allow_redirects=False,
                )

                if resp.status_code < 400:
                    # Success
                    webhook.last_triggered_at = datetime.utcnow()
                    webhook.last_status = "success"
                    webhook.failure_count = 0
                    db.session.commit()
                    logger.info(
                        "Webhook %s fired successfully (status %s)",
                        webhook.id, resp.status_code,
                    )
                    return True

                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    "Webhook %s attempt %d failed: %s",
                    webhook.id, attempt + 1, last_error,
                )

            except requests.Timeout:
                last_error = "Timeout"
                logger.warning(
                    "Webhook %s attempt %d timed out", webhook.id, attempt + 1
                )
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(
                    "Webhook %s attempt %d error: %s",
                    webhook.id, attempt + 1, last_error,
                )

            # Exponential backoff before retry
            if attempt < max_retries - 1:
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                time.sleep(delay)

        # All retries exhausted
        webhook.last_triggered_at = datetime.utcnow()
        webhook.last_status = "failed"
        webhook.failure_count = (webhook.failure_count or 0) + 1
        db.session.commit()
        logger.error(
            "Webhook %s failed after %d attempts: %s",
            webhook.id, max_retries, last_error,
        )
        return False

    def run_scheduled_check(self, solution_id):
        """Run a compliance check and fire webhooks if drift detected.

        1. Find latest published spec for solution
        2. Find service_url from solution metadata or last compliance check
        3. Run ComplianceChecker.check()
        4. If drift detected, fire drift_detected webhooks
        5. Return check result
        """
        from app.models.published_api_spec import PublishedAPISpec
        from app.models.compliance_check import RuntimeComplianceCheck
        from app.models.solution_models import Solution
        from app.modules.solutions_product.services.compliance_checker import ComplianceChecker

        # Find latest published spec
        latest_spec = (
            PublishedAPISpec.query
            .filter_by(solution_id=solution_id, status="published")
            .order_by(PublishedAPISpec.published_at.desc())
            .first()
        )
        if not latest_spec:
            logger.info("No published spec for solution %s, skipping check", solution_id)
            return {"success": False, "error": "No published spec"}

        # Get service URL from metadata or last check
        solution = Solution.query.get(solution_id)
        if not solution:
            return {"success": False, "error": "Solution not found"}

        meta = self._get_metadata(solution)
        schedule_config = meta.get("compliance_schedule", {})
        service_url = schedule_config.get("service_url")

        if not service_url:
            # Fall back to last compliance check's service URL
            last_check = (
                RuntimeComplianceCheck.query
                .filter_by(solution_id=solution_id)
                .order_by(RuntimeComplianceCheck.checked_at.desc())
                .first()
            )
            if last_check:
                service_url = last_check.service_url

        if not service_url:
            logger.info("No service_url for solution %s, skipping check", solution_id)
            return {"success": False, "error": "No service_url configured"}

        # Run compliance check
        checker = ComplianceChecker()
        result = checker.check(
            published_spec_id=latest_spec.id,
            service_url=service_url,
            timeout=10,
        )

        # Fire webhooks if drift detected
        if result.get("status") == "drifted":
            payload = {
                "event": "drift_detected",
                "solution_id": solution_id,
                "solution_name": solution.name if hasattr(solution, "name") else str(solution_id),
                "compliance_score": result.get("compliance_score"),
                "missing_endpoints": len(result.get("missing_endpoints") or []),
                "schema_mismatches": len(result.get("schema_mismatches") or []),
                "service_url": service_url,
                "check_id": result.get("id"),
                "platform_url": f"https://archie.company.com/solutions/{solution_id}",
            }
            self.fire_webhooks(solution_id, "drift_detected", payload)

        elif result.get("status") == "unreachable":
            payload = {
                "event": "compliance_failed",
                "solution_id": solution_id,
                "solution_name": solution.name if hasattr(solution, "name") else str(solution_id),
                "service_url": service_url,
                "reason": "Service unreachable",
                "check_id": result.get("id"),
                "platform_url": f"https://archie.company.com/solutions/{solution_id}",
            }
            self.fire_webhooks(solution_id, "compliance_failed", payload)

        return result

    def schedule_checks(self, organization_id=None):
        """Find solutions with scheduled checks and run them.

        Reads schedule from solution metadata_json.compliance_schedule.
        ``organization_id`` limits the run to one organisation's solutions
        (every request-driven caller passes it); left as None it covers every
        solution, which only a system job running outside any tenant may do.
        """
        from app.models.solution_models import Solution

        query = Solution.query
        if organization_id is not None:
            query = query.filter(Solution.organization_id == organization_id)
        solutions = query.all()
        results = []

        for solution in solutions:
            meta = self._get_metadata(solution)
            schedule_config = meta.get("compliance_schedule", {})

            if not schedule_config.get("enabled", False):
                continue

            if not schedule_config.get("service_url"):
                continue

            try:
                result = self.run_scheduled_check(solution.id)
                results.append({
                    "solution_id": solution.id,
                    "success": result.get("success", False),
                    "status": result.get("status"),
                })
            except Exception as e:
                logger.error(
                    "Scheduled check failed for solution %s: %s",
                    solution.id, e,
                )
                results.append({
                    "solution_id": solution.id,
                    "success": False,
                    "error": str(e),
                })

        return results

    def _get_metadata(self, solution):
        """Safely extract metadata_json dict from a Solution."""
        meta = getattr(solution, "metadata_json", None) or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        return meta

    def _get_element_name(self, solution_id, element_id):
        """Get the name of an ArchiMate element linked to a solution."""
        try:
            from app.models.solution_archimate_element import SolutionArchiMateElement
            from app.models.archimate_core import ArchiMateElement

            junction = (
                SolutionArchiMateElement.query
                .filter_by(solution_id=solution_id, archimate_element_id=element_id)
                .first()
            )
            if junction and junction.archimate_element_id:
                element = ArchiMateElement.query.get(junction.archimate_element_id)
                if element:
                    return getattr(element, "name", None) or f"Element {element_id}"
        except Exception as e:
            logger.debug("Could not resolve element name for %s: %s", element_id, e)
        return f"Element {element_id}"
