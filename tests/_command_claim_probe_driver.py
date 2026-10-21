"""Subprocess driver for tests/test_command_claim_runtime_role.py.

Boots the application against DATABASE_URL (the restricted runtime role),
creates a brand-new user/organisation, then calls CommandService.execute
directly -- the exact state machine behind every Transformation Room
command, including the ``/solutions/create-programme`` wizard submit -- to
prove whether a first-ever claim can actually transition from "claimed" to
a committed, succeeded result under the real, restricted role connection.

It runs in its own process for the same reason tests/_runtime_role_driver.py
does: the runtime-role connection string must never leak into the pytest
session's own (superuser) engine, and the app must be built exactly the way
a web container builds it -- as the runtime role, with no superuser anywhere.

Prints one ``RESULT <json>`` line describing the outcome.
"""

from __future__ import annotations

import json
import os
import uuid

os.environ.setdefault("FLASK_CONFIG", "testing")

from app import create_app, db  # noqa: E402
from app.modules.transformation_room.command_service import CommandService  # noqa: E402
from app.modules.transformation_room.domain import (  # noqa: E402
    ActorContext,
    DomainMutationResult,
    TransformationError,
)


def _noop_authorizer(session, actor, operation, natural_key) -> None:
    return None


def _noop_handler(session, claim) -> DomainMutationResult:
    return DomainMutationResult(object_ids={}, response={}, outbox_events=())


def main() -> None:
    app = create_app("testing")
    app.config["PROPAGATE_EXCEPTIONS"] = False
    result: dict[str, object] = {}
    with app.app_context():
        from app.modules.account.services.account_service import AccountService

        email = f"claim-probe-{uuid.uuid4().hex[:10]}@example.com"
        with app.test_request_context("/"):
            user = AccountService.register_user(
                "Claim", "Probe", email, "Passw0rd!long1", confirmed=True
            )
            db.session.commit()
        actor = ActorContext(
            user_id=user.id,
            organization_id=user.organization_id,
            roles=frozenset(),
            request_id=str(uuid.uuid4()),
        )
        idempotency_key = f"claim-probe-{uuid.uuid4().hex}"

        try:
            command_result = CommandService.execute(
                actor=actor,
                operation="diagnostic.claim_probe",
                idempotency_key=idempotency_key,
                payload={"probe": True},
                natural_key=f"diagnostic-claim-probe:{idempotency_key}",
                authorizer=_noop_authorizer,
                handler=_noop_handler,
            )
        except TransformationError as exc:
            result["outcome"] = "transformation_error"
            result["error_class"] = type(exc).__name__
            result["reason"] = exc.reason
        except Exception as exc:  # noqa: BLE001 -- report every failure shape
            result["outcome"] = "exception"
            result["error_class"] = type(exc).__name__
            result["reason"] = str(exc)
        else:
            result["outcome"] = "succeeded"
            result["created"] = command_result.created
            result["operation_result_id"] = command_result.operation_result_id

        # A second call with the SAME idempotency key must now be answered
        # idempotently (the claim having gone live and completed), not with
        # "active_lease" -- the exact symptom the production bug reported.
        try:
            replay_result = CommandService.execute(
                actor=actor,
                operation="diagnostic.claim_probe",
                idempotency_key=idempotency_key,
                payload={"probe": True},
                natural_key=f"diagnostic-claim-probe:{idempotency_key}",
                authorizer=_noop_authorizer,
                handler=_noop_handler,
            )
        except TransformationError as exc:
            result["replay_outcome"] = "transformation_error"
            result["replay_error_class"] = type(exc).__name__
            result["replay_reason"] = exc.reason
        except Exception as exc:  # noqa: BLE001
            result["replay_outcome"] = "exception"
            result["replay_error_class"] = type(exc).__name__
            result["replay_reason"] = str(exc)
        else:
            result["replay_outcome"] = "succeeded"
            result["replay_idempotent"] = replay_result.idempotent

    print("RESULT " + json.dumps(result))


if __name__ == "__main__":
    main()
