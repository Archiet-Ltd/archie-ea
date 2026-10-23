"""
Base API Client Framework

Provides common functionality for all external API integrations.
Implements rate limiting, caching, error handling, and standardized responses.
"""

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class APIClientError(Exception):
    """Base exception for API client errors."""

    pass


class RateLimitError(APIClientError):
    """Raised when API rate limit is exceeded."""

    pass


class AuthenticationError(APIClientError):
    """Raised when API authentication fails."""

    pass


class APIResponse:
    """Standardized API response wrapper."""

    def __init__(
        self,
        success: bool,
        data: Any = None,
        error: str = None,
        rate_limit_remaining: int = None,
        rate_limit_reset: datetime = None,
        status_code: int = None,
    ):
        self.status_code = status_code
        self.success = success
        self.data = data
        self.error = error
        self.rate_limit_remaining = rate_limit_remaining
        self.rate_limit_reset = rate_limit_reset
        self.timestamp = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_reset": self.rate_limit_reset.isoformat()
            if self.rate_limit_reset
            else None,
            "timestamp": self.timestamp.isoformat(),
        }


class BaseAPIClient(ABC):
    """
    Abstract base class for all API clients.

    Provides common functionality:
    - HTTP session management with retries
    - Rate limiting and backoff
    - Response caching
    - Error handling and logging
    - Authentication management
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        rate_limit_per_minute: int = 60,
        cache_ttl_seconds: int = 3600,
    ):
        """
        Initialize the API client.

        Args:
            base_url: Base URL for the API
            api_key: API key for authentication (if required)
            rate_limit_per_minute: Maximum requests per minute
            cache_ttl_seconds: Cache TTL in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.rate_limit_per_minute = rate_limit_per_minute
        self.cache_ttl_seconds = cache_ttl_seconds

        # Rate limiting
        self.request_times = []
        self.min_interval = 60.0 / rate_limit_per_minute

        # Caching
        self.cache = {}

        # HTTP session with retries
        self.session = self._create_session()

        # Authentication
        self._setup_authentication()

    def _create_session(self) -> requests.Session:
        """Create HTTP session with retry strategy."""
        session = requests.Session()

        # Configure retries
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Set default headers
        session.headers.update(
            {
                "User-Agent": "Flask-Enterprise-Architecture-API/1.0.0",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

        return session

    def _setup_authentication(self):
        """Setup authentication headers. Override in subclasses."""
        if self.api_key:
            self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    def _scrub(self, text: str) -> str:
        """Remove anything secret from text about to be logged or returned.

        The base client has nothing to hide; a client that sends a credential
        overrides this so the credential cannot leave through an error message.
        """
        return text

    def _check_rate_limit(self):
        """Check and enforce rate limiting."""
        now = time.time()

        # Remove old requests outside the window
        self.request_times = [t for t in self.request_times if now - t < 60]

        if len(self.request_times) >= self.rate_limit_per_minute:
            # Calculate wait time
            oldest_request = min(self.request_times)
            wait_time = 60 - (now - oldest_request)

            if wait_time > 0:
                logger.warning(f"Rate limit reached, waiting {wait_time:.2f} seconds")
                time.sleep(wait_time)
                return True  # Waited

        return False  # No wait needed

    def _record_request(self):
        """Record a request for rate limiting."""
        self.request_times.append(time.time())

        # Keep only recent requests
        now = time.time()
        self.request_times = [t for t in self.request_times if now - t < 60]

    def _get_cache_key(self, method: str, url: str, params: Dict = None, data: Dict = None) -> str:
        """Generate cache key for request."""
        key_parts = [method, url]
        if params:
            key_parts.append(str(sorted(params.items())))
        if data:
            key_parts.append(str(sorted(data.items())))
        return "|".join(key_parts)

    def _get_cached_response(self, cache_key: str) -> Optional[APIResponse]:
        """Get cached response if available and not expired."""
        if cache_key in self.cache:
            cached_item = self.cache[cache_key]
            if datetime.utcnow() - cached_item["timestamp"] < timedelta(
                seconds=self.cache_ttl_seconds
            ):
                logger.debug(f"Cache hit for {cache_key}")
                return cached_item["response"]
            else:
                # Expired, remove from cache
                del self.cache[cache_key]

        return None

    def _cache_response(self, cache_key: str, response: APIResponse):
        """Cache a response."""
        self.cache[cache_key] = {"response": response, "timestamp": datetime.utcnow()}

        # Clean up old cache entries (keep last 1000)
        if len(self.cache) > 1000:
            oldest_keys = sorted(self.cache.keys(), key=lambda k: self.cache[k]["timestamp"])[:100]
            for key in oldest_keys:
                del self.cache[key]

    @staticmethod
    def _external_calls_allowed() -> bool:
        """Whether this deployment permits outbound calls to third parties.

        Fails CLOSED: outside an application context, or with the key absent,
        the answer is no. An air-gap control that defaults open is not a
        control.
        """
        try:
            from flask import current_app

            return bool(current_app.config.get("ALLOW_EXTERNAL_API_CALLS", False))
        except Exception:
            return False

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Dict = None,
        data: Dict = None,
        json_data: Dict = None,
        headers: Dict = None,
        use_cache: bool = True,
    ) -> APIResponse:
        """
        Make HTTP request with rate limiting, caching, and error handling.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (relative to base_url)
            params: Query parameters
            data: Form data
            json_data: JSON data
            headers: Additional headers
            use_cache: Whether to use caching

        Returns:
            APIResponse object
        """
        # Egress guard. ADR-0005 commits this product to an air-gapped posture,
        # and until 31 Aug 2026 nothing enforced it on the SERVER side: the
        # air-gap gate checks that UI assets are not loaded from a CDN, and says
        # nothing about Python making outbound calls.
        #
        # An adversarial sweep found GET /api/pipeline/market-analysis/<category>
        # calling https://api.github.com live, inside a logged-in request, with
        # the URL path segment as a search term -- and echoing ~776KB of
        # third-party repository descriptions back as this product's "market
        # analysis". For a category that does not exist it still returned
        # success:true, including the description of a credential-phishing
        # script, presented to the user as analysis.
        #
        # Default CLOSED. An enterprise deploying this behind a firewall should
        # not discover its EA tool talks to the public internet; a deployment
        # that wants the enrichment opts in explicitly.
        if not self._external_calls_allowed():
            message = (
                "External API calls are disabled (ALLOW_EXTERNAL_API_CALLS). "
                "No data was retrieved from %s." % self.base_url
            )
            logger.info("egress refused: %s %s/%s", method, self.base_url, endpoint)
            return APIResponse(success=False, error=message)

        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        # Check cache for GET requests
        cache_key = None
        if use_cache and method.upper() == "GET":
            cache_key = self._get_cache_key(method, url, params, json_data)
            cached_response = self._get_cached_response(cache_key)
            if cached_response:
                return cached_response

        # Rate limiting
        self._check_rate_limit()

        failed_status = None
        try:
            # Prepare request
            request_kwargs = {
                "method": method,
                "url": url,
                "params": params,
                "data": data,
                "json": json_data,
                "headers": headers,
            }

            logger.debug(f"Making {method} request to {url}")

            # Make request
            start_time = time.time()
            response = self.session.request(**request_kwargs)
            request_time = time.time() - start_time

            self._record_request()

            # Handle rate limiting headers
            rate_limit_remaining = None
            rate_limit_reset = None

            if "X-RateLimit-Remaining" in response.headers:
                rate_limit_remaining = int(response.headers["X-RateLimit-Remaining"])

            if "X-RateLimit-Reset" in response.headers:
                reset_timestamp = int(response.headers["X-RateLimit-Reset"])
                rate_limit_reset = datetime.fromtimestamp(reset_timestamp)

            # Handle response
            if response.status_code == 429:
                failed_status = 429
                # Rate limited
                reset_time = response.headers.get("Retry-After")
                if reset_time:
                    wait_seconds = int(reset_time)
                    logger.warning(f"Rate limited, waiting {wait_seconds} seconds")
                    time.sleep(wait_seconds)
                    # Retry once
                    return self._make_request(
                        method, endpoint, params, data, json_data, headers, use_cache
                    )

                raise RateLimitError("API rate limit exceeded")

            elif response.status_code == 401:
                failed_status = 401
                raise AuthenticationError("API authentication failed")

            elif response.status_code >= 400:
                failed_status = response.status_code
                error_msg = f"API request failed: {response.status_code} {response.reason}"
                if response.text:
                    # Scrub before cutting: a secret split by the cut would survive it.
                    error_msg += f" - {self._scrub(response.text)[:200]}"
                raise APIClientError(error_msg)

            # Parse response
            try:
                response_data = response.json()
            except ValueError:
                response_data = response.text

            api_response = APIResponse(
                success=True,
                data=response_data,
                rate_limit_remaining=rate_limit_remaining,
                rate_limit_reset=rate_limit_reset,
            )

            # Cache successful GET responses
            if use_cache and method.upper() == "GET" and cache_key:
                self._cache_response(cache_key, api_response)

            logger.debug(f"Request completed in {request_time:.2f}s")
            return api_response

        except requests.RequestException as e:
            error_msg = self._scrub(f"Request failed: {e}")
            logger.error(error_msg)
            return APIResponse(success=False, error=error_msg)

        except (RateLimitError, AuthenticationError, APIClientError) as e:
            error_msg = self._scrub(str(e))
            logger.error(error_msg)
            return APIResponse(success=False, error=error_msg, status_code=failed_status)

    def get(
        self, endpoint: str, params: Dict = None, headers: Dict = None, use_cache: bool = True
    ) -> APIResponse:
        """Make GET request."""
        return self._make_request(
            "GET", endpoint, params=params, headers=headers, use_cache=use_cache
        )

    def post(
        self, endpoint: str, data: Dict = None, json_data: Dict = None, headers: Dict = None
    ) -> APIResponse:
        """Make POST request."""
        return self._make_request("POST", endpoint, data=data, json_data=json_data, headers=headers)

    def put(
        self, endpoint: str, data: Dict = None, json_data: Dict = None, headers: Dict = None
    ) -> APIResponse:
        """Make PUT request."""
        return self._make_request("PUT", endpoint, data=data, json_data=json_data, headers=headers)

    def delete(self, endpoint: str, headers: Dict = None) -> APIResponse:
        """Make DELETE request."""
        return self._make_request("DELETE", endpoint, headers=headers)

    @abstractmethod
    def health_check(self) -> bool:
        """
        Perform health check for the API.

        Returns:
            bool: True if API is healthy
        """
        pass

    def get_rate_limit_status(self) -> Dict[str, Any]:
        """Get current rate limiting status."""
        now = time.time()
        recent_requests = len([t for t in self.request_times if now - t < 60])

        return {
            "requests_in_last_minute": recent_requests,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "min_interval_seconds": self.min_interval,
            "cache_entries": len(self.cache),
        }

    def clear_cache(self):
        """Clear the response cache."""
        self.cache.clear()
        logger.info("API client cache cleared")
