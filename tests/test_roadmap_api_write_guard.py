"""The roadmap API's two bulk writers, generating work packages and syncing applications into deliverables.

A Viewer is refused with the product's read-only sentence, an architect is not refused, and an architect
cannot point the generator at another organisation's capability.
"""

import pytest

from app._bootstrap.write_access import GENERAL_REASON as REFUSAL
from tests.test_ba_tenant_and_authz import _cleanup_ids, _login, _make_org_id, _make_user_id

GENERATE = "/api/roadmap/automation/generate-work-packages"
SYNC = "/api/roadmap/sync/applications"


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture(scope="module")
def world(app):
    from app import db
    from app.models.organization import Organization
    from app.models.unified_capability import UnifiedCapability
    from app.models.user import User

    w = {}
    with app.app_context():
        w["org_a"] = _make_org_id(db, "RGA")
        w["org_b"] = _make_org_id(db, "RGB")
        w["viewer"] = _make_user_id(db, w["org_a"], "RgViewer", enterprise_role="procurement", role_name="Viewer")
        w["architect"] = _make_user_id(db, w["org_a"], "RgArchitect", enterprise_role="enterprise_architect", role_name="Architect")
        own = UnifiedCapability(name="Own capability", organization_id=w["org_a"], level=1)
        other = UnifiedCapability(name="Other capability", organization_id=w["org_b"], level=1)
        db.session.add_all([own, other])
        db.session.commit()
        w["cap_own"], w["cap_other"] = own.id, other.id
    yield w
    with app.app_context():
        _cleanup_ids(db, UnifiedCapability, [w["cap_own"], w["cap_other"]])
        _cleanup_ids(db, User, [w["viewer"], w["architect"]])
        _cleanup_ids(db, Organization, [w["org_a"], w["org_b"]])


def _post(app, world, who, path, body):
    client = app.test_client()
    with app.app_context():
        _login(client, world[who])
        return client.post(path, json=body)


@pytest.mark.parametrize("path,body", [(GENERATE, {"source_type": "capability", "source_id": 0}), (SYNC, {})])
def test_viewer_is_refused(app, world, path, body):
    response = _post(app, world, "viewer", path, {**body, "source_id": world["cap_own"]} if body else body)
    assert response.status_code == 403
    assert response.get_json()["error"] == REFUSAL


@pytest.mark.parametrize("path,body", [(GENERATE, {"source_type": "capability", "source_id": 0}), (SYNC, {})])
def test_architect_is_not_refused(app, world, path, body):
    response = _post(app, world, "architect", path, {**body, "source_id": world["cap_own"]} if body else body)
    assert response.status_code != 403
    assert response.status_code != 404


def test_other_organisations_capability_is_refused(app, world):
    body = {"source_type": "capability", "source_id": world["cap_other"]}
    response = _post(app, world, "architect", GENERATE, body)
    assert response.status_code == 404
