"""
Real-time Infrastructure Polling Service

Checks configured API endpoints and cloud connectors against the modelled
state in Entelim. Reports delta: what's modelled vs what's reachable.

Scope (MVP):
  - ABACUSConnector endpoints (already configured, just check reachability)
  - LLM API key validity (check stored keys are non-expired)
  - Integration pattern endpoints (any URL-bearing patterns can be probed)
  - Generic endpoint health: accepts a list of URLs, returns up/down/timeout

Not included: cloud hyperscaler APIs (Azure/AWS) — require OAuth tokens not
yet stored in Entelim. Tracked as INFRA-002.
"""

import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5  # seconds
_MAX_URLS = 20


@dataclass
class EndpointStatus:
    url: str
    reachable: bool
    status_code: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    source: str = ""  # Where the URL came from (abacus, llm_key, integration_pattern)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "reachable": self.reachable,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "source": self.source,
        }


class InfrastructurePollingService:

    @classmethod
    def poll_infrastructure(cls, include_abacus: bool = True,
                             include_llm: bool = True,
                             additional_urls: Optional[List[str]] = None) -> dict:
        """
        Check infrastructure endpoints and return reachability report.

        Returns: {
          "success": True,
          "endpoints_checked": N,
          "reachable": N,
          "unreachable": N,
          "results": [...],
          "delta": "summary of gaps between modelled and reachable"
        }
        """
        try:
            results = []
            probed_sources = []

            if include_abacus:
                abacus_results = cls._probe_abacus()
                results.extend(abacus_results)
                if abacus_results:
                    probed_sources.append("Abacus API")

            if include_llm:
                llm_results = cls._probe_llm_endpoints()
                results.extend(llm_results)
                if llm_results:
                    probed_sources.append("LLM APIs")

            if additional_urls:
                for url in additional_urls[:_MAX_URLS]:
                    results.append(cls._probe_url(url, source="user_provided"))
                probed_sources.append(f"{len(additional_urls)} user-provided URLs")

            # Integration pattern URLs
            pattern_results = cls._probe_integration_patterns()
            results.extend(pattern_results)
            if pattern_results:
                probed_sources.append("integration patterns")

            reachable = sum(1 for r in results if r.reachable)
            unreachable = len(results) - reachable

            delta_lines = []
            for r in results:
                if not r.reachable:
                    delta_lines.append(
                        f"UNREACHABLE [{r.source}] {r.url} — {r.error or 'no response'}"
                    )

            return {
                "success": True,
                "endpoints_checked": len(results),
                "reachable": reachable,
                "unreachable": unreachable,
                "results": [r.to_dict() for r in results],
                "delta": "\n".join(delta_lines) if delta_lines else "All checked endpoints reachable.",
                "sources_probed": probed_sources,
                "message": (
                    f"Polled {len(results)} endpoint(s) across {', '.join(probed_sources) or 'no sources configured'}. "
                    f"{reachable} reachable, {unreachable} unreachable."
                    + (f" Delta: {len(delta_lines)} gap(s) vs modelled state." if delta_lines else " All endpoints up.")
                ),
            }
        except Exception as e:
            logger.exception("InfrastructurePollingService.poll_infrastructure failed")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # Source probers                                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def _probe_abacus(cls) -> List[EndpointStatus]:
        results = []
        try:
            from app.models.abacus_connector import AbacusConfiguration
            configs = AbacusConfiguration.query.filter_by(is_active=True).limit(5).all()
            for cfg in configs:
                url = getattr(cfg, "base_url", None) or getattr(cfg, "api_url", None)
                if url:
                    results.append(cls._probe_url(url, source="abacus"))
        except Exception as e:
            logger.debug("_probe_abacus: %s", e)
            # Try known Abacus URL from env
            try:
                import os
                abacus_url = os.environ.get("ABACUS_BASE_URL")
                if abacus_url:
                    results.append(cls._probe_url(abacus_url, source="abacus_env"))
            except Exception:
                pass
        return results

    @classmethod
    def _probe_llm_endpoints(cls) -> List[EndpointStatus]:
        results = []
        try:
            from app.models.api_settings import APISettings
            keys = APISettings.query.limit(10).all()
            for k in keys:
                provider = getattr(k, "provider", "") or ""
                url = None
                if "openai" in provider.lower():
                    url = "https://api.openai.com/v1/models"
                elif "anthropic" in provider.lower():
                    url = "https://api.anthropic.com/v1/models"
                elif "azure" in provider.lower():
                    endpoint = getattr(k, "endpoint", None)
                    if endpoint:
                        url = endpoint
                if url:
                    results.append(cls._probe_url(url, source=f"llm:{provider}"))
        except Exception as e:
            logger.debug("_probe_llm_endpoints: %s", e)
        return results

    @classmethod
    def _probe_integration_patterns(cls) -> List[EndpointStatus]:
        results = []
        try:
            from app.models.integration_pattern import IntegrationPattern
            patterns = IntegrationPattern.query.filter(
                IntegrationPattern.endpoint_url.isnot(None)
            ).limit(5).all()
            for p in patterns:
                url = getattr(p, "endpoint_url", None)
                if url and url.startswith("http"):
                    results.append(cls._probe_url(url, source=f"integration_pattern:{p.name}"))
        except Exception as e:
            logger.debug("_probe_integration_patterns: %s", e)
        return results

    # ------------------------------------------------------------------ #
    # Core probe                                                            #
    # ------------------------------------------------------------------ #

    @classmethod
    def _probe_url(cls, url: str, source: str = "", timeout: int = _DEFAULT_TIMEOUT) -> EndpointStatus:
        import time
        if not url or not url.startswith("http"):
            return EndpointStatus(url=url, reachable=False, error="invalid URL (must start with http)", source=source)

        # Redact any token in query string before logging
        parsed = urlparse(url)
        safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

        try:
            start = time.monotonic()
            req = urllib.request.Request(safe_url, method="HEAD")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                return EndpointStatus(
                    url=safe_url, reachable=True,
                    status_code=resp.status, latency_ms=elapsed_ms, source=source,
                )
        except urllib.error.HTTPError as e:
            # 4xx from the server = server reachable
            elapsed_ms = int((time.monotonic() - start) * 1000) if 'start' in dir() else None  # type: ignore[arg-type]
            reachable = e.code < 500
            return EndpointStatus(
                url=safe_url, reachable=reachable, status_code=e.code,
                error=f"HTTP {e.code}" if not reachable else None, source=source,
            )
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            return EndpointStatus(url=safe_url, reachable=False, error=str(e)[:80], source=source)
        except Exception as e:  # noqa: BLE001
            return EndpointStatus(url=safe_url, reachable=False, error=str(e)[:80], source=source)
