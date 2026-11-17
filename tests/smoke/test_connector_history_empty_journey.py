"""A platform admin who has just connected GitHub opens its history and is told what happens next.

The goal: from the connectors page, open a connector that has never synced and read that it has not
synced yet and how a sync starts; follow that step to a page that lets them start one. A connector
that has synced still lists its runs. Inside the dialog, keyboard focus stays put until it is closed.

A second organisation owns a connector with history that must not appear in this one.
"""

import os
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.test_connector_dialog_journey import _card, _open, github_server  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CONNECTOR_SHOTS_DIR") or os.path.join("test-results", "connector-history")


def _seed(org_id):
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig, SyncLog
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    app = create_app("testing")
    with app.app_context():
        other = Organization(name="Other Org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()

        def make(owner, label):
            row = ConnectorConfig(
                organization_id=owner,
                connector_type="github",
                name="GitHub %s %s" % (label, suffix),
                config={"organization": "octocat", "account_type": "User"},
                status="active",
            )
            db.session.add(row)
            db.session.flush()
            return row

        never, ran, theirs = make(org_id, "never"), make(org_id, "ran"), make(other.id, "foreign")
        jira = ConnectorConfig(
            organization_id=org_id,
            connector_type="jira",
            name="Jira never %s" % suffix,
            config={"base_url": "https://example.atlassian.net"},
            status="active",
        )
        db.session.add(jira)
        db.session.flush()
        started = datetime(2026, 10, 12, 9, 30, 0)
        logs = [
            SyncLog(connector_id=ran.id, sync_type="batch", status="success", records_processed=12,
                    records_created=5, records_updated=7, records_deleted=0,
                    started_at=started, completed_at=started + timedelta(seconds=4)),
            SyncLog(connector_id=ran.id, sync_type="manual", status="error", records_processed=0,
                    records_created=0, records_updated=0, records_deleted=0,
                    error_message="GitHub rejected the token (HTTP 401)",
                    started_at=started - timedelta(days=2), completed_at=started - timedelta(days=2)),
            SyncLog(connector_id=theirs.id, sync_type="batch", status="success", records_processed=999,
                    started_at=started),
        ]
        db.session.add_all(logs)
        db.session.commit()
        return {
            "jira": jira.name, "never": never.name, "ran": ran.name, "foreign": theirs.name,
            "connector_ids": [never.id, ran.id, theirs.id, jira.id], "other": other.id,
        }


def _cleanup(data):
    from sqlalchemy import text

    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        ids = data["connector_ids"]
        db.session.execute(text("DELETE FROM sync_logs WHERE connector_id = ANY(:ids)"), {"ids": ids})
        db.session.execute(text("DELETE FROM connector_configs WHERE id = ANY(:ids)"), {"ids": ids})
        db.session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": data["other"]})
        db.session.commit()


@pytest.mark.parametrize(
    "size,scheme",
    [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")],
)
def test_admin_sees_what_to_do_for_a_connector_that_never_synced(
    browser, github_server, seeded, size, scheme
):
    data = _seed(seeded["ids"]["org"])
    base = str(github_server)
    context, page, errors = _open(browser, base, seeded, size, scheme)
    os.makedirs(SHOTS, exist_ok=True)
    tag = "%d-%s" % (size["width"], scheme)
    try:
        assert data["foreign"] not in page.content()

        # A connector that has never synced: say so, and say how a sync starts.
        card = _card(page, data["never"])
        card.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        empty = dialog.get_by_test_id("connector-history-empty")
        expect(empty).to_be_visible()
        expect(empty).to_contain_text("has not synced yet")
        assert dialog.get_by_test_id("connector-history-run").count() == 0
        page.screenshot(path=os.path.join(SHOTS, "empty-%s.png" % tag))

        # Keyboard focus moves into the dialog and Tab cannot leave it.
        assert dialog.evaluate("el => el.contains(document.activeElement)"), "focus stayed on the page behind"
        for _ in range(12):
            page.keyboard.press("Tab")
            assert dialog.evaluate("el => el.contains(document.activeElement)"), "Tab left the dialog"
        for _ in range(3):
            page.keyboard.press("Shift+Tab")
            assert dialog.evaluate("el => el.contains(document.activeElement)"), "Shift+Tab left the dialog"

        # The next step lands on a page that can start a sync.
        step = empty.get_by_role("link", name="Open the GitHub connection")
        expect(step).to_be_visible()
        step.click()
        page.wait_for_url("**/admin/connectors/github", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("button", name="Save and read again")).to_be_visible()

        # A connector that is not GitHub: the step is the Connectors page, where its row has Sync.
        page.goto(base + "/integrations/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _card(page, data["jira"]).click()
        dialog = page.get_by_role("dialog")
        empty = dialog.get_by_test_id("connector-history-empty")
        expect(empty).to_contain_text("has not synced yet")
        expect(empty).to_contain_text("choose \u201cSync\u201d")
        expect(empty).not_to_contain_text("cannot be started from this page")
        page.screenshot(path=os.path.join(SHOTS, "jira-empty-%s.png" % tag))
        empty.get_by_role("link", name="Open the Connectors page").click()
        page.wait_for_url("**/admin/connectors", timeout=PAGE_TIMEOUT)
        row = page.locator("tr", has_text=data["jira"])
        expect(row).to_be_visible()
        expect(row.get_by_role("button", name="Sync")).to_be_visible()
        assert data["foreign"] not in page.content()

        # A connector that has synced still lists its runs, newest first, and has no empty state.
        page.goto(base + "/integrations/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _card(page, data["ran"]).click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        runs = dialog.get_by_test_id("connector-history-run")
        expect(runs).to_have_count(2)
        expect(runs.nth(0)).to_contain_text("Processed: 12")
        expect(runs.nth(1)).to_contain_text("GitHub rejected the token (HTTP 401)")
        expect(dialog.get_by_test_id("connector-history-empty")).to_be_hidden()
        assert "999" not in dialog.inner_text()
        page.screenshot(path=os.path.join(SHOTS, "filled-%s.png" % tag))
        assert not [e for e in errors if "favicon" not in e], errors
    finally:
        context.close()
        _cleanup(data)
