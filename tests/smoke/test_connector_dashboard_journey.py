"""A platform admin checks that a GitHub connector synced.

The goal: open the connectors page, see the connector's name, status and
the date it last synced, open its sync history and read the date of each run,
with nothing in the console. The page used to stay on "Loading connectors..."
because the date helper it called was not reachable from the card.

Run in an organisation that owns a seeded connector with sync history, plus a
second organisation whose connector must not appear.
"""

import datetime as dt
import uuid

import pytest

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LAST_SYNC = dt.datetime(2026, 10, 14, 9, 30, 0)
OLDER_SYNC = dt.datetime(2026, 10, 7, 8, 15, 0)


def _seed(seeded):
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig
    from app.models.organization import Organization
    from app.services.connector_framework import SyncLog

    suffix = uuid.uuid4().hex[:8]
    app = create_app("testing")
    with app.app_context():
        other = Organization(name="Other Org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()
        mine = ConnectorConfig(
            organization_id=seeded["ids"]["org"],
            connector_type="github",
            name="GitHub %s" % suffix,
            config={"owner": "acme"},
            status="active",
        )
        theirs = ConnectorConfig(
            organization_id=other.id,
            connector_type="github",
            name="Foreign GitHub %s" % suffix,
            config={"owner": "other"},
            status="active",
        )
        db.session.add_all([mine, theirs])
        db.session.flush()
        for started, kind in ((LAST_SYNC, "batch"), (OLDER_SYNC, "manual")):
            db.session.add(
                SyncLog(
                    connector_id=mine.id,
                    sync_type=kind,
                    status="success",
                    records_processed=42,
                    records_created=5,
                    records_updated=30,
                    records_deleted=1,
                    started_at=started,
                    completed_at=started + dt.timedelta(seconds=20),
                )
            )
        db.session.commit()
        return {
            "name": mine.name,
            "foreign": theirs.name,
            "ids": [mine.id, theirs.id, other.id],
        }


def _cleanup(ids):
    from app import create_app, db
    from app.models.connector_config import ConnectorConfig
    from app.models.organization import Organization
    from app.services.connector_framework import SyncLog

    app = create_app("testing")
    with app.app_context():
        mine, theirs, other = ids
        SyncLog.query.filter(SyncLog.connector_id.in_([mine, theirs])).delete(
            synchronize_session=False
        )
        db.session.query(ConnectorConfig).filter(
            ConnectorConfig.id.in_([mine, theirs])
        ).delete(synchronize_session=False)
        db.session.query(Organization).filter(Organization.id == other).delete(
            synchronize_session=False
        )
        db.session.commit()


@pytest.mark.parametrize(
    "size,scheme",
    [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")],
)
def test_admin_reads_connector_last_sync_and_history(browser, live_server, seeded, size, scheme):
    data = _seed(seeded)
    context = browser.new_context(
        ignore_https_errors=True, viewport=size, color_scheme=scheme
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on(
        "console",
        lambda m: errors.append(m.text) if m.type == "error" else None,
    )
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", seeded["emails"]["platform_admin"])
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/integrations/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        card = page.locator("h3", has_text=data["name"]).locator(
            "xpath=ancestor::div[contains(@class,'cursor-pointer')][1]"
        )
        card.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert page.get_by_text("Loading connectors...").is_hidden()
        text = card.inner_text()
        assert "active" in text
        # The card's last-sync date is the newest run, shown as a real date.
        assert "2026" in text and "Never" not in text
        assert data["foreign"] not in page.content()

        page.screenshot(
            path="/home/ubuntu/verify/seats/connfmt02/walk/connectors-card-%d-%s.png"
            % (size["width"], scheme)
        )
        card.click()
        history = page.get_by_text("Recent Sync History").locator("xpath=..")
        page.get_by_text("Processed: 42").first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert page.get_by_text("Processed: 42").count() == 2
        history_text = history.inner_text()
        assert history_text.count("2026") == 2, history_text
        assert "Invalid Date" not in history_text

        page.screenshot(
            path="/home/ubuntu/verify/seats/connfmt02/walk/connectors-%d-%s.png"
            % (size["width"], scheme)
        )
        assert errors == [], errors
    finally:
        context.close()
        _cleanup(data["ids"])
