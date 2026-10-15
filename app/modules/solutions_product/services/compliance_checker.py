"""Compliance Checker — detect drift between published API spec and runtime service.

Three check types:
1. Endpoint coverage — does the service expose all spec'd endpoints?
2. Schema compliance — do response shapes match spec'd schemas?
3. SLA compliance — does the service meet latency/availability targets?

Uses `requests` (already in requirements.txt) for HTTP calls. All requests are
READ-ONLY (GET/HEAD only) — the checker never modifies the target service.
"""

import logging
import time
from datetime import datetime

import requests

from app import db

logger = logging.getLogger(__name__)

# Default SLA thresholds (milliseconds)
DEFAULT_SLA_LATENCY_MS = 2000
DEFAULT_SLA_AVAILABILITY_PCT = 99.0


class ComplianceChecker:
    """Check deployed service compliance against published API spec."""

    def check(self, published_spec_id, service_url, timeout=10, checked_by_id=None):
        """Run all compliance checks against a live service.

        Args:
            published_spec_id: ID of the PublishedAPISpec to check against.
            service_url: Base URL of the deployed service (e.g. http://order-service:8000).
            timeout: HTTP request timeout in seconds.
            checked_by_id: User ID of whoever triggered the check.

        Returns:
            dict with full compliance results (mirrors ComplianceCheck.to_dict()).
        """
        from app.models.compliance_check import RuntimeComplianceCheck
        from app.models.published_api_spec import PublishedAPISpec

        from app.utils.ssrf_guard import (
            BlockedOutboundURL,
            clamp_timeout,
            validate_user_supplied_url,
        )

        # Single choke point (manual and scheduled checks): refuse targets
        # inside the host's network and bound the timeout.
        try:
            validate_user_supplied_url(service_url)
            timeout = clamp_timeout(timeout)
        except BlockedOutboundURL:
            return {"success": False, "error": "service_url refused: URL not allowed"}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        start_time = time.time()

        spec = PublishedAPISpec.query.get(published_spec_id)
        if not spec:
            return {"success": False, "error": f"Published spec {published_spec_id} not found"}

        spec_content = spec.spec_content or {}

        # Normalise service_url (strip trailing slash)
        service_url = service_url.rstrip("/")

        # Try to fetch runtime OpenAPI spec from the service
        runtime_spec = self._fetch_runtime_spec(service_url, timeout)

        # Extract endpoints from published spec
        published_endpoints = self._extract_endpoints(spec_content)
        total_endpoints = len(published_endpoints)

        # If we got a runtime spec, do a full diff
        extra_endpoints = []
        if runtime_spec:
            runtime_endpoints = self._extract_endpoints(runtime_spec)
            extra_endpoints = self._find_extra_endpoints(published_endpoints, runtime_endpoints)

        # Check each published endpoint
        missing_endpoints = []
        schema_mismatches = []
        response_times = []

        for ep in published_endpoints:
            result = self._check_endpoint(
                service_url, ep["method"], ep["path"],
                ep.get("response_schema"), timeout
            )
            if result["status"] == "missing":
                missing_endpoints.append({
                    "method": ep["method"],
                    "path": ep["path"],
                    "reason": result.get("reason", "404 or connection error"),
                })
            elif result["status"] == "schema_mismatch":
                schema_mismatches.append({
                    "method": ep["method"],
                    "path": ep["path"],
                    "details": result.get("details", "Response shape differs from spec"),
                })

            if result.get("latency_ms") is not None:
                response_times.append({
                    "method": ep["method"],
                    "path": ep["path"],
                    "latency_ms": result["latency_ms"],
                })

        # Check SLAs
        sla_violations = self._check_sla(spec.solution_id, response_times)

        # Compute score
        compliance_score = self._compute_score(
            missing_endpoints, extra_endpoints,
            schema_mismatches, sla_violations, total_endpoints
        )

        # Determine status
        if total_endpoints == 0:
            status = "passed"
        elif compliance_score >= 1.0:
            status = "passed"
        elif compliance_score > 0.0:
            status = "drifted"
        else:
            # Check if service was reachable at all
            try:
                requests.head(service_url, timeout=timeout)
                status = "drifted"
            except requests.RequestException:
                status = "unreachable"

        duration_ms = int((time.time() - start_time) * 1000)

        # Persist
        record = RuntimeComplianceCheck(
            solution_id=spec.solution_id,
            published_spec_id=published_spec_id,
            service_url=service_url,
            status=status,
            compliance_score=round(compliance_score, 4),
            missing_endpoints=missing_endpoints or None,
            extra_endpoints=extra_endpoints or None,
            schema_mismatches=schema_mismatches or None,
            sla_violations=sla_violations or None,
            checked_at=datetime.utcnow(),
            checked_by_id=checked_by_id,
            duration_ms=duration_ms,
        )
        db.session.add(record)
        db.session.commit()

        result = record.to_dict()
        result["success"] = True
        result["total_endpoints"] = total_endpoints
        return result

    def _fetch_runtime_spec(self, service_url, timeout):
        """Try to fetch /openapi.json from the service.

        Falls back to /docs, /swagger.json, /api-docs.
        Returns parsed JSON dict or None.
        """
        spec_paths = ["/openapi.json", "/swagger.json", "/api/openapi.json"]
        for path in spec_paths:
            try:
                resp = requests.get(
                    f"{service_url}{path}",
                    timeout=timeout,
                    allow_redirects=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and ("paths" in data or "openapi" in data):
                        return data
            except (requests.RequestException, ValueError):
                continue
        return None

    def _extract_endpoints(self, spec):
        """Extract list of {method, path, response_schema} from an OpenAPI spec dict."""
        endpoints = []
        paths = spec.get("paths", {})
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, details in methods.items():
                method_upper = method.upper()
                if method_upper not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                    continue
                response_schema = None
                if isinstance(details, dict):
                    responses = details.get("responses", {})
                    ok_resp = responses.get("200") or responses.get("201") or responses.get("2XX")
                    if isinstance(ok_resp, dict):
                        content = ok_resp.get("content", {})
                        json_content = content.get("application/json", {})
                        response_schema = json_content.get("schema")
                endpoints.append({
                    "method": method_upper,
                    "path": path,
                    "response_schema": response_schema,
                })
        return endpoints

    def _find_extra_endpoints(self, published_endpoints, runtime_endpoints):
        """Find endpoints in runtime spec but not in published spec."""
        published_set = {(ep["method"], ep["path"]) for ep in published_endpoints}
        extra = []
        for ep in runtime_endpoints:
            key = (ep["method"], ep["path"])
            if key not in published_set:
                extra.append({"method": ep["method"], "path": ep["path"]})
        return extra

    def _diff_specs(self, published_spec, runtime_spec):
        """Diff two OpenAPI specs — find missing/extra/changed endpoints."""
        published_eps = self._extract_endpoints(published_spec)
        runtime_eps = self._extract_endpoints(runtime_spec)

        published_set = {(ep["method"], ep["path"]) for ep in published_eps}
        runtime_set = {(ep["method"], ep["path"]) for ep in runtime_eps}

        missing = [
            {"method": m, "path": p}
            for m, p in (published_set - runtime_set)
        ]
        extra = [
            {"method": m, "path": p}
            for m, p in (runtime_set - published_set)
        ]
        return {"missing": missing, "extra": extra}

    def _check_endpoint(self, base_url, method, path, expected_schema, timeout):
        """Check a single endpoint — exists, schema matches, latency OK.

        Only sends GET or HEAD requests for safety. POST/PUT/etc. endpoints
        are checked with HEAD (existence only, no schema validation).
        """
        url = f"{base_url}{path}"

        # For non-GET methods, only check existence with HEAD
        use_method = "GET" if method == "GET" else "HEAD"

        try:
            start = time.time()
            resp = requests.request(
                use_method, url,
                timeout=timeout,
                allow_redirects=False,
            )
            latency_ms = int((time.time() - start) * 1000)

            if resp.status_code == 404:
                return {"status": "missing", "reason": "404 Not Found", "latency_ms": latency_ms}

            if resp.status_code == 405 and use_method == "HEAD":
                # Method not allowed for HEAD — try GET as fallback for existence
                try:
                    start2 = time.time()
                    resp2 = requests.get(url, timeout=timeout, allow_redirects=False)
                    latency_ms = int((time.time() - start2) * 1000)
                    if resp2.status_code == 404:
                        return {"status": "missing", "reason": "404 Not Found", "latency_ms": latency_ms}
                    # Endpoint exists
                    return {"status": "exists", "latency_ms": latency_ms}
                except requests.RequestException:
                    return {"status": "exists", "latency_ms": latency_ms}  # Original HEAD succeeded

            if resp.status_code >= 500:
                return {"status": "missing", "reason": f"Server error {resp.status_code}", "latency_ms": latency_ms}

            # Schema validation — only for GET with expected schema and 200 response
            if use_method == "GET" and expected_schema and resp.status_code == 200:
                mismatch = self._validate_response_schema(resp, expected_schema)
                if mismatch:
                    return {
                        "status": "schema_mismatch",
                        "details": mismatch,
                        "latency_ms": latency_ms,
                    }

            return {"status": "exists", "latency_ms": latency_ms}

        except requests.ConnectionError:
            return {"status": "missing", "reason": "Connection refused"}
        except requests.Timeout:
            return {"status": "missing", "reason": f"Timeout after {timeout}s"}
        except requests.RequestException as e:
            return {"status": "missing", "reason": str(e)}

    def _validate_response_schema(self, resp, expected_schema):
        """Validate response JSON against expected schema (lightweight check).

        Returns mismatch description string or None if OK.
        Does a structural check — verifies top-level keys and types match.
        """
        try:
            body = resp.json()
        except ValueError:
            return "Response is not valid JSON"

        if not isinstance(expected_schema, dict):
            return None

        schema_type = expected_schema.get("type")
        properties = expected_schema.get("properties", {})
        required = expected_schema.get("required", [])

        if schema_type == "object" and isinstance(body, dict):
            missing_fields = [f for f in required if f not in body]
            if missing_fields:
                return f"Missing required fields: {', '.join(missing_fields)}"

            type_mismatches = []
            for field_name, field_schema in properties.items():
                if field_name in body:
                    expected_type = field_schema.get("type")
                    actual_value = body[field_name]
                    if not self._type_matches(actual_value, expected_type):
                        type_mismatches.append(
                            f"{field_name}: expected {expected_type}, "
                            f"got {type(actual_value).__name__}"
                        )
            if type_mismatches:
                return "; ".join(type_mismatches)

        elif schema_type == "array" and not isinstance(body, list):
            return f"Expected array, got {type(body).__name__}"

        return None

    def _type_matches(self, value, expected_type):
        """Check if a Python value matches a JSON Schema type."""
        if expected_type is None or value is None:
            return True
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected_python = type_map.get(expected_type)
        if expected_python is None:
            return True
        return isinstance(value, expected_python)

    def _check_sla(self, solution_id, response_times):
        """Compare measured response times against SLA thresholds.

        Returns list of SLA violation dicts.
        """
        if not response_times:
            return []

        violations = []
        for rt in response_times:
            if rt["latency_ms"] > DEFAULT_SLA_LATENCY_MS:
                violations.append({
                    "method": rt["method"],
                    "path": rt["path"],
                    "latency_ms": rt["latency_ms"],
                    "threshold_ms": DEFAULT_SLA_LATENCY_MS,
                    "type": "latency",
                })

        return violations

    def _compute_score(self, missing, extra, schema_mismatches, sla_violations, total_endpoints):
        """Compute compliance score (0.0 - 1.0).

        Scoring:
        - Each missing endpoint: -1/total
        - Each extra endpoint: -0.5/total (less severe)
        - Each schema mismatch: -0.3/total
        - Each SLA violation: -0.2/total
        - Floor at 0.0
        """
        if total_endpoints == 0:
            return 1.0

        penalty = 0.0
        penalty += len(missing) * (1.0 / total_endpoints)
        penalty += len(extra) * (0.5 / total_endpoints)
        penalty += len(schema_mismatches) * (0.3 / total_endpoints)
        penalty += len(sla_violations) * (0.2 / total_endpoints)

        return max(0.0, 1.0 - penalty)
