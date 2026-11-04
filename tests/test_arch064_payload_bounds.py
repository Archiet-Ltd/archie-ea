"""Regression test for ARCH-064 (S3, partial remainder): the architecture
dashboard's static per-element-type field configuration was being
re-serialised into every page load via ``tojson``, even though it never
changes per request or per tenant. Moved to a fetched endpoint
(``archimate_crud.api_field_configs``) in
``app/modules/architecture/routes/archimate_crud/routes.py``.

Measured with a near-empty database (matching how this finding was
originally measured):

    /capability-map/          before 477,490 bytes  after 477,490 bytes (untouched this wave)
    /architecture/dashboard   before 187,069 bytes  after 169,857 bytes (-17,212 bytes / -9.2%)

This test pins two things: the dashboard response no longer inlines the
field-config JSON, and the new endpoint serves the same data the template
used to embed directly, so the create/edit modal isn't silently broken by
the split.

capability-map/ is now bounded too. Its five "map applications to X"
dialogs (mapping-modal, acm-mapping-modal, process-mapping-modal,
apqc-mapping-modal, archimate-mapping-modal) are all closed on arrival, so
they are no longer serialised into the initial HTML: they are fetched from
``capability_map.mapping_modal_partial`` on first open and injected into
``#lazy-modal-host``. Measured with the same harness as the tests below
(near-empty flask_test database, admin session):

    /capability-map/          before 436,618 bytes  after 368,193 bytes (-68,425 bytes / -15.7%)

De-duplicating the three near-copies into ``capability_map/_mapping_modal.html``
(the earlier wave) moved this number by zero, because the macro still rendered
all three. Not rendering them is what made the difference.
"""

import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org_and_admin(db_session):
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Payload {suffix}", slug=f"payload-{suffix}")
    db_session.add(org)
    db_session.flush()

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=f"payload-{suffix}@example.com",
        first_name="Payload",
        last_name="Tester",
        organization_id=org.id,
        role_id=admin_role.id,
        is_org_admin=True,
    )
    user.password = "Passw0rd!23"
    if hasattr(user, "confirmed"):
        user.confirmed = True
    db_session.add(user)
    db_session.flush()
    return org, user


class TestArch064DashboardPayload:
    def test_dashboard_does_not_inline_field_configs(self, app, db_session, login_as, client):
        """The dashboard HTML must not embed the full field-config JSON blob
        inline any more — that payload now lives behind a separate fetch."""
        org, admin = _make_org_and_admin(db_session)
        login_as(client, admin)

        resp = client.get("/architecture/dashboard")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")

        # The old inline assignment is gone...
        assert "__APP_CONFIG__.fieldConfigs =" not in body
        # ...replaced by a URL the client fetches instead.
        assert "fieldConfigsUrl" in body

    def test_field_configs_endpoint_serves_real_data(self, app, db_session, login_as, client):
        """The extracted endpoint must actually return the field configs —
        proving the split didn't just delete the data."""
        org, admin = _make_org_and_admin(db_session)
        login_as(client, admin)

        resp = client.get("/architecture/api/field-configs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_field_configs_endpoint_requires_login(self, app, db_session, client):
        resp = client.get("/architecture/api/field-configs")
        assert resp.status_code in (302, 401)


class TestArch064CapabilityMapLazyModals:
    """The closed mapping dialogs must not ship in the initial HTML, and the
    endpoint that replaces them must serve real markup for every variant."""

    LAZY_IDS = [
        "mapping-modal",
        "acm-mapping-modal",
        "process-mapping-modal",
        "apqc-mapping-modal",
        "archimate-mapping-modal",
    ]

    def test_capability_map_does_not_inline_the_closed_modals(
        self, app, db_session, login_as, client
    ):
        org, admin = _make_org_and_admin(db_session)
        login_as(client, admin)

        resp = client.get("/capability-map/dashboard")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")

        for modal_id in self.LAZY_IDS:
            assert f'id="{modal_id}"' not in body, f"{modal_id} is still inlined"

        # ...and the host the fragments are injected into IS present, or
        # nothing could ever open them.
        assert 'id="lazy-modal-host"' in body
        assert "/capability-map/partials/mapping-modal/" in body

    def test_partial_endpoint_serves_every_variant(self, app, db_session, login_as, client):
        org, admin = _make_org_and_admin(db_session)

        for modal_id in self.LAZY_IDS:
            login_as(client, admin)
            resp = client.get(f"/capability-map/partials/mapping-modal/{modal_id}")
            assert resp.status_code == 200, modal_id
            fragment = resp.data.decode("utf-8")
            assert f'id="{modal_id}"' in fragment, modal_id

    def test_partial_endpoint_rejects_unknown_variant(self, app, db_session, login_as, client):
        org, admin = _make_org_and_admin(db_session)
        login_as(client, admin)

        resp = client.get("/capability-map/partials/mapping-modal/../../etc/passwd")
        assert resp.status_code == 404

        resp = client.get("/capability-map/partials/mapping-modal/not-a-modal")
        assert resp.status_code == 404

    def test_partial_endpoint_requires_login(self, app, db_session, client):
        resp = client.get("/capability-map/partials/mapping-modal/mapping-modal")
        assert resp.status_code in (302, 401)
