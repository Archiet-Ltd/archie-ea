"""The Settings backup panel must only describe capabilities Archie provides."""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def admin_client(app, db_session, make_org, login_as):
    """Return a real admin session for the global Settings page."""
    from app.models import Permission, Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()

    org = make_org("settings-backup")
    user = User(
        email=f"settings-backup-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Settings",
        last_name="Admin",
        organization_id=org.id,
        role=role,
        confirmed=True,
        # /settings edits the global system_settings table: platform admin
        # (flag AND Administrator role), not merely an organisation admin.
        is_platform_admin=True,
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)
    return client


def test_settings_backup_panel_does_not_advertise_unavailable_archives(admin_client):
    """No archive exists until a repository-backed backup capability is shipped.

    This fails if a future template reintroduces sample backup files or presents
    download, restore, or deletion as actions despite no corresponding route.
    """
    response = admin_client.get("/settings")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    panel = html[html.index('id="tab-backup"'):html.index("<!-- Danger Zone -->")]

    assert 'data-testid="backup-capability-unavailable"' in panel
    # Backups are a real, running capability (deploy/archie-backup.sh: verified
    # 6-hourly pg_dump) - just not one this page can trigger, because the
    # application's own database role is deliberately least-privilege. The
    # panel says that, rather than the earlier "not available in this build",
    # which read as a missing feature rather than a deliberate boundary.
    assert "Backup and restore are not available from this page." in panel
    for unavailable_content in (
        "backup_2024_02_20_1000.zip",
        "backup_2024_02_13_1000.zip",
        'data-testid="btn-download-backup"',
        'data-testid="btn-restore-1"',
        'data-testid="btn-delete-backup-1"',
    ):
        assert unavailable_content not in panel
