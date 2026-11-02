"""Rate limit for the composer endpoints, counted in the shared counter store."""
from functools import wraps

from flask import jsonify, request
from flask_login import current_user

from app.services.rate_limiter import apply_headers, count_hit

_WINDOW = 60  # seconds
_MAX_REQUESTS = 10  # per window


def composer_rate_limit(f):
    """Decorator: allow at most 10 requests per minute per user/IP."""
    @wraps(f)
    def decorated(*args, **kwargs):
        who = str(getattr(current_user, 'id', None) or request.remote_addr or 'anon')
        verdict = count_hit("composer", who, _MAX_REQUESTS, _WINDOW)
        if not verdict.allowed:
            refusal = jsonify({"error": "Rate limit exceeded. Try again later.", "retry_after": verdict.retry_after})
            refusal.status_code = 429
            return apply_headers(refusal, verdict)
        return f(*args, **kwargs)
    return decorated
