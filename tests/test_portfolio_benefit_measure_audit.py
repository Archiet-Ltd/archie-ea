"""The portfolio measure form writes through the room's audited outcome writer."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from tests.test_portfolio_crud_roundtrip import (
    _login,
    _make_org_id,
    _make_programme_id,
    _make_user_id,
    app,  # noqa: F401
    client,  # noqa: F401
)


def _make_benefit(db, org_id, programme_id, owner_id, *, baseline=100, target=50):
    from app.models.benefit import Benefit

    benefit = Benefit(
        name=f"Audit {uuid.uuid4().hex[:6]}",
        organization_id=org_id,
        strategic_initiative_id=programme_id,
        owner_id=owner_id,
        unit="GBP",
        baseline_value=baseline,
        target_value=target,
        status="identified",
    )
    db.session.add(benefit)
    db.session.commit()
    return benefit.id


def _history(db, benefit_id):
    from app.models.transformation_execution import OutcomeMeasurement

    return list(
        db.session.scalars(
            select(OutcomeMeasurement).where(OutcomeMeasurement.benefit_id == benefit_id)
        )
    )


@pytest.fixture
def two_orgs(app):  # noqa: F811
    from app import db

    with app.app_context():
        out = {}
        for label in ("AuditA", "AuditB"):
            org_id = _make_org_id(db, label)
            user_id = _make_user_id(db, org_id, label)
            programme_id = _make_programme_id(db, org_id, user_id)
            out[label] = {
                "org_id": org_id,
                "user_id": user_id,
                "benefit_id": _make_benefit(db, org_id, programme_id, user_id),
                "programme_id": programme_id,
            }
        return out


def test_measure_form_appends_audited_history(app, client, two_orgs):  # noqa: F811
    """Catches a direct write to Benefit.actual_value with no measurement row."""
    from app import db
    from app.models.benefit import Benefit

    a = two_orgs["AuditA"]
    _login(client, a["user_id"])
    client.post(f"/portfolio/benefits/{a['benefit_id']}/measure", data={"actual_value": "75"})

    with app.app_context():
        history = _history(db, a["benefit_id"])
        assert len(history) == 1
        assert history[0].value == Decimal("75")
        assert history[0].recorded_by_id == a["user_id"]
        assert history[0].organization_id == a["org_id"]
        benefit = db.session.get(Benefit, a["benefit_id"])
        assert float(benefit.actual_value) == 75


def test_measure_form_and_service_produce_the_same_projection(app, client, two_orgs):  # noqa: F811
    """Both paths on identical benefits leave the same value, status and history size."""
    from app import db
    from app.models.benefit import Benefit
    from app.modules.transformation_room.domain import ActorContext
    from app.modules.transformation_room.outcome_service import OutcomeMeasurementService

    a = two_orgs["AuditA"]
    with app.app_context():
        via_service = _make_benefit(db, a["org_id"], a["programme_id"], a["user_id"])
        via_form = _make_benefit(db, a["org_id"], a["programme_id"], a["user_id"])
        OutcomeMeasurementService.record(
            actor=ActorContext(
                user_id=a["user_id"],
                organization_id=a["org_id"],
                roles=frozenset(),
                request_id=str(uuid.uuid4()),
            ),
            benefit_id=via_service,
            value=Decimal("40"),
            unavailable_reason=None,
            observed_at=datetime.now(timezone.utc),
            source_identity="portfolio:benefit-form",
            source_version="1",
            command_key=str(uuid.uuid4()),
        )
    _login(client, a["user_id"])
    client.post(f"/portfolio/benefits/{via_form}/measure", data={"actual_value": "40"})

    with app.app_context():
        one = db.session.get(Benefit, via_service)
        two = db.session.get(Benefit, via_form)
        assert (one.actual_value, one.status) == (two.actual_value, two.status)
        assert one.status == "realised"
        assert len(_history(db, via_service)) == len(_history(db, via_form)) == 1


def test_measure_form_cannot_touch_another_organisations_benefit(app, client, two_orgs):  # noqa: F811
    """Fresh-session cross-organisation request: not found, nothing written."""
    from app import db
    from app.models.benefit import Benefit

    a, b = two_orgs["AuditA"], two_orgs["AuditB"]
    _login(client, b["user_id"])
    response = client.post(
        f"/portfolio/benefits/{a['benefit_id']}/measure", data={"actual_value": "1"}
    )

    assert response.status_code in (302, 404)
    with app.app_context():
        assert db.session.get(Benefit, a["benefit_id"]).actual_value is None
        assert _history(db, a["benefit_id"]) == []


def test_measure_form_ignores_a_warm_identity_map_from_another_organisation(
    app, client, two_orgs  # noqa: F811
):
    """A session that already holds org A's row must not let org B write through it.

    ``Session.get`` only applies the tenant filter on an identity-map miss; a
    request that shares a session with an earlier tenant would otherwise be
    served the cached foreign row.
    """
    from app import db
    from app.models.benefit import Benefit

    a, b = two_orgs["AuditA"], two_orgs["AuditB"]
    with app.app_context():
        db.session.remove()
        _login(client, a["user_id"])
        warm = db.session.get(Benefit, a["benefit_id"])
        assert warm is not None  # org A's row is now in the shared identity map

        _login(client, b["user_id"])
        client.post(f"/portfolio/benefits/{a['benefit_id']}/measure", data={"actual_value": "1"})

        db.session.expire_all()
        assert db.session.get(Benefit, a["benefit_id"]).actual_value is None
        assert _history(db, a["benefit_id"]) == []
        db.session.remove()
