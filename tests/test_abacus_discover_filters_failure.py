"""Fetch Filter Options Now reports a failed fetch as a failure.

The connector answers an empty set when it could not reach Abacus, sign in or read the answer. The
route used to turn that into ``success: true`` with no dimensions, so the page said it had
discovered filters and reloaded. It now answers 502 with a plain reason and changes nothing; a real
answer still succeeds.
"""

from __future__ import annotations

import json

from tests.test_abacus_admin_routes_platform_admin import _login, _world

PATH = "/admin/abacus-settings/discover-filters"


def _saved_connection(db_session):
    from app.models.models import ExternalSystem

    system = ExternalSystem.query.filter_by(system_name="abacus").first()
    system.base_url = "https://abacus.invalid"
    system.credentials = json.dumps({"client_id": "id", "client_secret": "secret"})
    system.config_json = json.dumps({"filters": []})
    db_session.commit()
    return system.id


def _answer(monkeypatch, options):
    from app.connectors.abacus import AbacusConnector

    async def discover(self):
        return options

    monkeypatch.setattr(AbacusConnector, "discover_filter_options", discover)


def test_an_unreachable_abacus_is_reported_as_a_failure_and_saves_nothing(
    app, db_session, make_org, client, login_as, monkeypatch
):
    _tenant_a, _tenant_b, platform_admin_id, _job = _world(db_session, make_org)
    system_id = _saved_connection(db_session)
    _answer(monkeypatch, {})

    _login(db_session, client, login_as, platform_admin_id)
    response = client.post(PATH)

    assert response.status_code == 502
    body = response.get_json()
    assert body["success"] is False
    assert "could not be reached" in body["error"]

    from app.models.models import ExternalSystem

    db_session.expunge_all()
    saved = db_session.get(ExternalSystem, system_id)
    assert "discovered_dimensions" not in json.loads(saved.config_json)


def test_a_real_answer_still_succeeds_and_is_saved(
    app, db_session, make_org, client, login_as, monkeypatch
):
    _tenant_a, _tenant_b, platform_admin_id, _job = _world(db_session, make_org)
    system_id = _saved_connection(db_session)
    _answer(monkeypatch, {"App is used in Country": ["France"]})

    _login(db_session, client, login_as, platform_admin_id)
    response = client.post(PATH)

    assert response.status_code == 200
    assert response.get_json()["dimensions"] == {"App is used in Country": ["France"]}

    from app.models.models import ExternalSystem

    db_session.expunge_all()
    saved = db_session.get(ExternalSystem, system_id)
    assert json.loads(saved.config_json)["discovered_dimensions"] == {"App is used in Country": ["France"]}
