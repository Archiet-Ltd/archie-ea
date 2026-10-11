"""Pin the one Jira connection the product describes (register concept `jira-connection`).

The canonical Jira connection is the ConnectorConfig row with connector_type
"jira", saved per organisation on the Jira settings screen
(app/modules/admin/connector_routes.py, jira_config_save) with the API token
encrypted, and read by JiraConnectorService in app/services/jira_connector_service.py.

Test A pins that store: what an organisation saves is what the service reads,
only for that organisation, and the token is never stored in plaintext.

Test B is a ratchet. Other files still read a Jira credential from somewhere
else (Flask config, environment variables, the instance-wide ExternalSystem
row). They are listed below, as found on main. The list can only shrink: a new
file that reads a Jira credential outside JiraConnectorService fails here.
Repointing or retiring the listed files is a separate decision (product-manager).
"""

from __future__ import annotations

import base64
import os
import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")

# --------------------------------------------------------------------- #
# Test A: the canonical store
# --------------------------------------------------------------------- #


def _make_admin(db_session, org_id, label):
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="platform_administrator",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(
            name="Administrator",
            permissions=Permission.ADMINISTER,
            index="main",
            default=False,
        )
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


@pytest.fixture
def org_a(make_org):
    return make_org("jira-canon-a")


@pytest.fixture
def org_b(make_org):
    return make_org("jira-canon-b")


@pytest.fixture
def admin_a(db_session, org_a):
    return _make_admin(db_session, org_a.id, "JiraAdminA")


def test_saved_jira_connection_is_the_one_the_service_reads(
    db_session, org_a, org_b, admin_a, client, login_as, tenant_ctx
):
    """jira-connection: settings screen -> ConnectorConfig -> JiraConnectorService."""
    from app.services.jira_connector_service import JiraConnectorService

    instance_url = "https://canonical-a.atlassian.net"
    email = "jira-a@example.com"
    token = "tok-" + uuid.uuid4().hex

    login_as(client, admin_a)
    resp = client.post(
        "/admin/connectors/jira",
        data={
            "instance_url": instance_url + "/",
            "email": email,
            "api_token": token,
            "enabled": "on",
        },
    )
    assert resp.status_code in (302, 303), resp.status_code

    svc = JiraConnectorService()

    with tenant_ctx(org_a.id):
        cfg = svc._get_config()
        assert cfg is not None, (
            "jira-connection: the Jira settings save must be the row JiraConnectorService reads."
        )
        assert cfg.connector_type == "jira"
        assert cfg.organization_id == org_a.id
        assert svc._instance_url(cfg) == instance_url

        stored = cfg.config or {}
        assert stored.get("email") == email
        assert stored.get("api_token_encrypted"), "the token must be stored encrypted"
        assert token not in repr(stored), "the stored config must never hold the plaintext token"
        assert "api_token" not in stored

    with tenant_ctx(org_b.id):
        assert svc._get_config() is None, (
            "TENANT LEAK: organisation B's Jira connection resolved to organisation A's row."
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known defect, out of scope for this tests-only change: jira_config_save stores the "
        "encrypted token as a str, JiraConnectorService._get_headers only decrypts bytes, so the "
        "Authorization header carries the ciphertext. Remove this marker when _get_headers decrypts."
    ),
)
def test_decrypted_header_carries_the_saved_token(
    db_session, org_a, admin_a, client, login_as, tenant_ctx
):
    from app.services.jira_connector_service import JiraConnectorService

    email = "jira-h@example.com"
    token = "tok-" + uuid.uuid4().hex
    login_as(client, admin_a)
    client.post(
        "/admin/connectors/jira",
        data={
            "instance_url": "https://canonical-h.atlassian.net",
            "email": email,
            "api_token": token,
            "enabled": "on",
        },
    )
    svc = JiraConnectorService()
    with tenant_ctx(org_a.id):
        cfg = svc._get_config()
        assert cfg is not None
        headers = svc._get_headers(cfg)
        decoded = base64.b64decode(headers["Authorization"].split(" ", 1)[1]).decode("utf-8")
        assert decoded == f"{email}:{token}"


# --------------------------------------------------------------------- #
# Test B: the ratchet
# --------------------------------------------------------------------- #

CREDENTIAL_PATTERN = re.compile(r"JIRA_(URL|USER|USERNAME|BASE_URL|API_TOKEN|PROJECT_KEY)")
SYSTEM_NAME_PATTERN = re.compile(r"system_name=.*jira|SYSTEM_NAME = \"jira\"")

# Files under app/ that read a Jira credential outside the canonical connection,
# as found on main. This list can only shrink; never add to it.
ALLOWED_OTHER_JIRA_CREDENTIAL_READERS = {
    "app/modules/admin/routes/admin_routes.py",
    "app/modules/admin/v2/routes/admin_routes.py",
    "app/modules/capabilities/routes/enterprise_api_routes.py",
    "app/modules/integrations/jira/jira_sync_service.py",
    "app/modules/solutions_strategic/v2/services/solution_ai_orchestrator.py",
    "app/services/jira_integration_service.py",
    "app/services/jira_push_service.py",
}

CANONICAL = "app/services/jira_connector_service.py"


def _scan_app_for_other_jira_credentials():
    found = set()
    for dirpath, dirnames, filenames in os.walk(APP):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "tests")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if rel == CANONICAL:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if CREDENTIAL_PATTERN.search(text) or SYSTEM_NAME_PATTERN.search(text):
                found.add(rel)
    return found


def test_no_new_jira_credential_reader_outside_the_canonical_connection():
    found = _scan_app_for_other_jira_credentials()
    new = sorted(found - ALLOWED_OTHER_JIRA_CREDENTIAL_READERS)
    assert not new, (
        "Register concept `jira-connection`: new Jira access goes through "
        "JiraConnectorService (ConnectorConfig connector_type 'jira', per organisation, "
        "token encrypted). These files read a Jira credential from elsewhere: "
        f"{new}"
    )


def test_jira_credential_allow_list_has_no_stale_entries():
    """When a listed file stops reading a Jira credential, delete it from the list."""
    found = _scan_app_for_other_jira_credentials()
    stale = sorted(ALLOWED_OTHER_JIRA_CREDENTIAL_READERS - found)
    assert not stale, (
        "These files no longer read a Jira credential outside the canonical connection; "
        f"remove them from ALLOWED_OTHER_JIRA_CREDENTIAL_READERS so it keeps shrinking: {stale}"
    )
