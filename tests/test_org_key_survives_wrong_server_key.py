"""Saving a connection while the server's key is wrong must not make other saved secrets unreadable for good."""
import pytest
from cryptography.fernet import Fernet

from app.modules.codegen.services.credential_encryption import CredentialUnreadable

pytestmark = pytest.mark.usefixtures("db_session")


def _opens(vault, org_id, connector):
    try:
        return vault.retrieve(org_id, connector, "api_token")
    except CredentialUnreadable:
        return "unreadable"


def test_other_secrets_open_again_when_the_right_server_key_is_back(db_session, make_org, tenant_ctx, app, monkeypatch):
    from app.connectors import github as gh
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    org_a = make_org("keyloss-a")
    org_b = make_org("keyloss-b")
    good = app.config["ORG_ENCRYPTION_MASTER_KEY"]
    vault = OrgCredentialVault()
    for org in (org_a, org_b):
        with tenant_ctx(org.id):
            vault.store(org_id=org.id, connector_type="github", credential_type="api_token", value="gh-old-" + str(org.id))
            vault.store(org_id=org.id, connector_type="jira", credential_type="api_token", value="jira-" + str(org.id))
    db_session.flush()
    with tenant_ctx(org_a.id):
        assert _opens(vault, org_a.id, "jira") == f"jira-{org_a.id}"

    monkeypatch.setitem(app.config, "ORG_ENCRYPTION_MASTER_KEY", Fernet.generate_key().decode())
    with tenant_ctx(org_a.id):
        gh._store_token(org_a.id, "gh-new-1")  # wrong key: must not raise
        db_session.flush()
        assert _opens(vault, org_a.id, "github") == "gh-new-1"
        gh._store_token(org_a.id, "gh-new-2")  # a second save under the wrong key
        db_session.flush()
        assert _opens(vault, org_a.id, "github") == "gh-new-2"
    with tenant_ctx(org_b.id):
        assert _opens(vault, org_b.id, "jira") == "unreadable"  # untouched, not opened by the wrong key

    monkeypatch.setitem(app.config, "ORG_ENCRYPTION_MASTER_KEY", good)
    with tenant_ctx(org_a.id):
        assert _opens(vault, org_a.id, "jira") == f"jira-{org_a.id}"
        assert _opens(vault, org_a.id, "github") in ("gh-new-2", "unreadable")
    with tenant_ctx(org_b.id):
        assert _opens(vault, org_b.id, "jira") == f"jira-{org_b.id}"
        assert _opens(vault, org_b.id, "github") == f"gh-old-{org_b.id}"

    # the person connects again with the right key: Jira still opens, the new token opens
    with tenant_ctx(org_a.id):
        gh._store_token(org_a.id, "gh-new-3")
        db_session.flush()
        assert _opens(vault, org_a.id, "github") == "gh-new-3"
        assert _opens(vault, org_a.id, "jira") == f"jira-{org_a.id}"
