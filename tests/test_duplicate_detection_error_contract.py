"""A failed AI duplicate-detection run must not be reported as HTTP 200.

The adversarial sweep found ``POST /duplicate-detection/ai/api/detect`` and
``/api/compare`` returning **HTTP 200** with
``{"error": "'NoneType' object has no attribute 'generate_embeddings'"}`` - a
raw Python ``AttributeError`` handed to the client as a successful response.

Three defects sat behind that one payload:

1. ``AIDuplicateDetectionService._ensure_engines()`` existed but was never
   called from anywhere, so ``self.semantic_engine`` was still ``None`` on
   first use.
2. The service's ``except`` branch put ``str(e)`` into the response body,
   leaking an internal traceback detail to an unauthenticated-adjacent caller.
3. The routes did ``return jsonify(result)`` without looking at
   ``result["success"]``, so a failure was served with a 200 status.

These tests pin the resulting contract rather than the bug: a failed run is a
5xx-class *status*, and its message is written in the product's terms with no
Python type or attribute name in it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

# Substrings that would betray an internal object leaking into a response.
INTERNAL_LEAK_MARKERS = (
    "NoneType",
    "Traceback",
    "object has no attribute",
    "sqlalchemy",
    "psycopg2",
    "self.",
)


@pytest.fixture
def dedupe_client(app, db_session, make_org, login_as):
    """A logged-in client; ``login_as`` runs before each request (see conftest)."""
    from app.models.user import Role, User

    org = make_org("dedupe")
    user = User(
        email=f"dedupe-{org.id}@example.com",
        first_name="Dedupe",
        last_name="Probe",
        organization_id=org.id,
        # Unconfirmed users are bounced to the login page with a 302, which
        # would make every assertion below vacuous.
        confirmed=True,
    )
    if hasattr(user, "enterprise_role"):
        user.enterprise_role = "platform_admin"
    # Starting an AI run needs a platform administrator.
    user.is_platform_admin = True
    user.role = Role.query.filter_by(name="Administrator").first() or user.role
    db_session.add(user)
    db_session.flush()

    app.config["WTF_CSRF_ENABLED"] = False
    raw = app.test_client()

    class _Client:
        def post(self, url, **kwargs):
            login_as(raw, user)
            return raw.post(url, **kwargs)

    return _Client()


@pytest.mark.parametrize(
    "url, payload",
    [
        ("/duplicate-detection/ai/api/detect", {"strategy": "ai_enhanced", "threshold": 0.65}),
        ("/duplicate-detection/ai/api/compare", {"threshold": 0.65}),
    ],
)
def test_failed_run_is_not_reported_as_success(dedupe_client, url, payload):
    """Either the run succeeds, or the status says it did not. Never 200-on-failure."""
    response = dedupe_client.post(url, json=payload)
    assert response.status_code != 401, "request never reached the endpoint"
    body = response.get_json() or {}

    if response.status_code == 200:
        # A 200 is only legitimate if the payload actually reports success.
        assert body.get("success") is True, (
            f"{url} returned 200 while reporting failure: {body}"
        )
    else:
        assert response.status_code in (400, 403, 500, 502, 503), response.status_code


@pytest.mark.parametrize(
    "url, payload",
    [
        ("/duplicate-detection/ai/api/detect", {"strategy": "ai_enhanced", "threshold": 0.65}),
        ("/duplicate-detection/ai/api/compare", {"threshold": 0.65}),
    ],
)
def test_error_body_never_leaks_internals(dedupe_client, url, payload):
    text = dedupe_client.post(url, json=payload).get_data(as_text=True)
    for marker in INTERNAL_LEAK_MARKERS:
        assert marker not in text, f"{url} leaked {marker!r} to the client: {text[:300]}"


def test_invalid_strategy_is_a_400_not_a_500(dedupe_client):
    response = dedupe_client.post(
        "/duplicate-detection/ai/api/detect",
        json={"strategy": "../../etc/passwd", "threshold": 0.65},
    )
    assert response.status_code == 400


def test_out_of_range_threshold_is_a_400_not_a_500(dedupe_client):
    response = dedupe_client.post(
        "/duplicate-detection/ai/api/detect",
        json={"strategy": "ai_enhanced", "threshold": 42},
    )
    assert response.status_code == 400


def test_ensure_engines_is_called_before_the_engines_are_used():
    """The original AttributeError: engines are lazy and nothing initialised them."""
    import inspect

    from app.modules.duplicate_detection.services import (
        ai_duplicate_detection_service as mod,
    )

    source = inspect.getsource(mod.AIDuplicateDetectionService.detect_duplicates)
    assert "_ensure_engines()" in source, (
        "detect_duplicates must initialise the lazy engines; without this "
        "self.semantic_engine is None and every call raises AttributeError"
    )


def test_service_failure_message_is_in_product_terms():
    """The service's own error dict must not carry the exception text."""
    import inspect

    from app.modules.duplicate_detection.services import (
        ai_duplicate_detection_service as mod,
    )

    source = inspect.getsource(mod.AIDuplicateDetectionService.detect_duplicates)
    assert '"error": str(e)' not in source
