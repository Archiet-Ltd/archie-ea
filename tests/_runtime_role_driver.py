"""Subprocess driver for tests/test_runtime_role_domain_writes.py.

Boots the application against DATABASE_URL (the restricted runtime role), signs
up a brand-new user and organisation, then makes the web writes a new user makes
first. Prints one ``RESULT <json>`` line of ``{name: [status, detail]}``.

It runs in its own process so the runtime-role connection string never leaks
into the pytest session's engine, and so the app is built the way a web
container builds it: as the runtime role, with no superuser anywhere.
"""

from __future__ import annotations

import json
import os
import uuid

os.environ.setdefault("FLASK_CONFIG", "testing")

from app import create_app, db  # noqa: E402
from tests._session_test_helpers import mint_test_sid  # noqa: E402


WRITES = (
    (
        "application_import_manual",
        "/applications/import-manual",
        {"applications": [{"name": "Payment Gateway", "description": "plain"}]},
    ),
    (
        "application_create",
        "/applications/create",
        {"name": "Payment Gateway Two", "description": "plain"},
    ),
    (
        "risk_create",
        "/api/risks",
        {
            "title": "Vendor outage",
            "name": "Vendor outage",
            "description": "plain",
            "likelihood": 3,
            "probability": 3,
            "impact": 3,
            "category": "operational",
        },
    ),
)


def main() -> None:
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False
    # Answer an unhandled error with a 500 like production, instead of raising
    # into the driver, so every write reports a status.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    results: dict[str, list] = {}
    with app.app_context():
        from app.modules.account.services.account_service import AccountService

        email = f"runtime-{uuid.uuid4().hex[:10]}@example.com"
        with app.test_request_context("/"):
            user = AccountService.register_user(
                "Runtime", "Probe", email, "Passw0rd!long1", confirmed=True
            )
            db.session.commit()
        user_id, org_id = user.id, user.organization_id
        sid = mint_test_sid(user_id, org_id, app=app)
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
            session["_sid"] = sid
        for name, url, body in WRITES:
            response = client.post(url, json=body)
            results[name] = [
                response.status_code,
                response.get_data(as_text=True)[:300],
            ]
        # The ArchiMate element a first application produced must be visible
        # to its own organisation through the runtime role.
        from app.models import ArchiMateElement

        results["elements_visible"] = [
            200,
            str(
                db.session.query(ArchiMateElement)
                .filter(ArchiMateElement.organization_id == org_id)
                .count()
            ),
        ]
    print("RESULT " + json.dumps(results))


if __name__ == "__main__":
    main()
