"""
SECURITY MIDDLEWARE
Runtime security monitoring and enforcement for Flask-ShadCN platform.

This middleware provides real-time security monitoring and blocks malicious requests.
"""

import logging
import re
import threading
import time
from typing import Dict, List, Set

from flask import abort, current_app, g, request

logger = logging.getLogger(__name__)

# IPs that should never be blocked (localhost/loopback)
LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}


class SecurityMiddleware:
    """Runtime security middleware for Flask applications"""

    def __init__(self, app=None):
        self.app = app
        self.suspicious_patterns = self._load_suspicious_patterns()
        self.rate_limits: Dict[str, List[float]] = {}
        self.blocked_ips: Set[str] = set()
        self.sql_injection_attempts = 0
        self.xss_attempts = 0
        self.csrf_attempts = 0

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize middleware with Flask app"""
        app.before_request(self.before_request)
        app.after_request(self.after_request)

        # Configure security settings
        app.config.setdefault("SECURITY_MAX_REQUESTS_PER_MINUTE", 60)
        app.config.setdefault("SECURITY_BLOCK_DURATION", 900)  # 15 minutes
        app.config.setdefault("SECURITY_MAX_CONTENT_LENGTH", 10 * 1024 * 1024)  # 10MB

        # Add CLI command for security status
        @app.cli.command()
        def security_status():
            """Show security middleware status"""
            print("🔒 Security Middleware Status:")
            print(f"  Blocked IPs: {len(self.blocked_ips)}")
            print(f"  SQL Injection Attempts: {self.sql_injection_attempts}")
            print(f"  XSS Attempts: {self.xss_attempts}")
            print(f"  CSRF Attempts: {self.csrf_attempts}")
            print(f"  Active Rate Limits: {len(self.rate_limits)}")

    def before_request(self):
        """Security checks before request processing"""
        client_ip = self._get_client_ip()

        # Never block localhost in debug mode (development)
        if client_ip in LOCALHOST_IPS and current_app.debug:
            g.security_checks_passed = True
            g.client_ip = client_ip
            return

        # Check if IP is blocked
        if client_ip in self.blocked_ips:
            logger.warning(f"Blocked request from {client_ip}")
            abort(403, "Access denied")

        # Rate limiting check
        if not self._check_rate_limit(client_ip):
            logger.warning(f"Rate limit exceeded for {client_ip}")
            self._block_ip(client_ip)
            abort(429, "Too many requests")

        # Content length check
        content_length = request.content_length or 0
        max_length = current_app.config.get("SECURITY_MAX_CONTENT_LENGTH", 10 * 1024 * 1024)
        if content_length > max_length:
            logger.warning(f"Content length exceeded: {content_length} from {client_ip}")
            abort(413, "Payload too large")

        # Security pattern checks
        if self._contains_suspicious_patterns():
            logger.warning(f"Suspicious pattern detected from {client_ip}: {request.url}")
            self._block_ip(client_ip)
            abort(403, "Suspicious request detected")

        # Store security context
        g.security_checks_passed = True
        g.client_ip = client_ip

    def after_request(self, response):
        """Security checks after request processing"""
        # Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "same-origin"

        # HSTS - only in non-debug mode to avoid issues with local development
        if not current_app.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # Add CSP header if configured
        csp = current_app.config.get("CONTENT_SECURITY_POLICY")
        if csp:
            response.headers["Content-Security-Policy"] = csp

        # Log security events
        if hasattr(g, "security_violation"):
            logger.warning(f"Security violation: {g.security_violation}")

        return response

    def _get_client_ip(self) -> str:
        """Get real client IP address"""
        # Check for forwarded headers
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take first IP in case of multiple proxies
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fallback to remote_addr
        return request.remote_addr or "unknown"

    def _check_rate_limit(self, client_ip: str) -> bool:
        """Check if request is within rate limits"""
        now = time.time()
        max_requests = current_app.config.get("SECURITY_MAX_REQUESTS_PER_MINUTE", 60)

        if client_ip not in self.rate_limits:
            self.rate_limits[client_ip] = []

        # Clean old requests (older than 1 minute)
        self.rate_limits[client_ip] = [
            timestamp for timestamp in self.rate_limits[client_ip] if now - timestamp < 60
        ]

        # Check current request count
        if len(self.rate_limits[client_ip]) >= max_requests:
            return False

        # Add current request
        self.rate_limits[client_ip].append(now)
        return True

    def _block_ip(self, client_ip: str):
        """Block an IP address temporarily"""
        # Never block localhost IPs
        if client_ip in LOCALHOST_IPS:
            return

        self.blocked_ips.add(client_ip)

        # Schedule unblock after configured duration
        block_duration = current_app.config.get("SECURITY_BLOCK_DURATION", 900)

        def unblock():
            time.sleep(block_duration)
            self.blocked_ips.discard(client_ip)

        unblock_thread = threading.Thread(target=unblock, daemon=True)
        unblock_thread.start()

        logger.info(f"Blocked IP {client_ip} for {block_duration} seconds")

    def _load_suspicious_patterns(self) -> List[re.Pattern]:
        """Load patterns for detecting suspicious requests"""
        patterns = [
            # SQL Injection patterns
            re.compile(r"union\s+select|select\s+.*\s+from", re.IGNORECASE),  # UNION SELECT
            re.compile(r"(\'\s*(or|and)\s*\')|(\'\s*;\s*(drop|delete|update|insert))", re.IGNORECASE),  # SQL injection via quotes
            re.compile(r"1\s*=\s*1|1\s*=\s*0|or\s+1\s*=\s*1", re.IGNORECASE),  # Tautology injection tests
            # XSS patterns
            re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
            re.compile(r"javascript:", re.IGNORECASE),
            re.compile(r"on\w+\s*=", re.IGNORECASE),  # Event handlers
            re.compile(r"<iframe[^>]*>.*?</iframe>", re.IGNORECASE | re.DOTALL),
            # Path traversal
            re.compile(r"\.\./|\.\.\\", re.IGNORECASE),
            re.compile(r"%2e%2e%2f|%2e%2e%5c", re.IGNORECASE),  # URL encoded ../
            # Command injection
            re.compile(r";\s*(rm|del|format|shutdown|reboot)", re.IGNORECASE),
            re.compile(r"\|\s*(cat|type|dir|ls)", re.IGNORECASE),
            re.compile(r"`.*`", re.IGNORECASE),  # Backticks
            # Directory listing attempts
            re.compile(r"/admin/|/wp-admin/|/phpmyadmin/", re.IGNORECASE),
            re.compile(r"\.env|\.git|\.svn|\.DS_Store", re.IGNORECASE),
        ]
        return patterns

    def _contains_suspicious_patterns(self) -> bool:
        """Check if request contains suspicious patterns"""
        # Check URL
        url_content = request.url + (
            request.query_string.decode("utf-8", errors="ignore") if request.query_string else ""
        )

        # Check POST data (limited to avoid performance issues)
        if request.method in ["POST", "PUT", "PATCH"] and request.content_length:
            if request.content_length < 1024 * 1024:  # Only check small payloads
                try:
                    if request.is_json:
                        post_content = str(request.get_json())
                    elif request.form:
                        post_content = str(dict(request.form))
                    else:
                        post_content = request.get_data(as_text=True, errors="ignore")[
                            :1000
                        ]  # First 1000 chars
                except Exception:
                    post_content = ""
            else:
                post_content = ""
        else:
            post_content = ""

        all_content = url_content + post_content

        for pattern in self.suspicious_patterns:
            if pattern.search(all_content):
                # Categorize the violation
                pattern_str = pattern.pattern
                if "union" in pattern_str or "select" in pattern_str:
                    self.sql_injection_attempts += 1
                    g.security_violation = f"SQL injection attempt: {pattern_str}"
                elif "<script" in pattern_str or "javascript" in pattern_str:
                    self.xss_attempts += 1
                    g.security_violation = f"XSS attempt: {pattern_str}"
                else:
                    g.security_violation = f"Suspicious pattern: {pattern_str}"
                return True

        return False


# Flask extension
class SecurityEnforcement:
    """Flask extension for security enforcement"""

    def __init__(self, app=None):
        self.middleware = None

        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize security enforcement"""
        self.middleware = SecurityMiddleware(app)

        # Store on app
        app.security_enforcement = self

        # Default CSP: same-origin only.
        #
        # This previously allowed cdn.tailwindcss.com, unpkg.com and cdn.jsdelivr.net
        # because _head.html loaded Tailwind, Lucide, Alpine and DOMPurify from them.
        # Those are now vendored under app/static/vendor/ (see the air-gap gate), so
        # the allowances are dead permissions — and a dead script-src allowance is a
        # standing invitation: any injected tag pointing at one of those hosts would
        # still execute.
        #
        # www.google-analytics.com and cdn.segment.com are likewise removed. Both
        # integrations are gated on config that defaults to empty
        # (SEGMENT_API_KEY, POSTHOG_API_KEY), so nothing loads
        # them by default. If a deployment deliberately enables one, it must widen
        # this policy explicitly via the CONTENT_SECURITY_POLICY config key — which
        # makes enabling third-party telemetry a visible, reviewable decision rather
        # than a silent default.
        app.config.setdefault(
            "CONTENT_SECURITY_POLICY",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'",
        )

        logger.info("Security middleware initialized")
