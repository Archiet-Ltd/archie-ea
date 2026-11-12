"""Once the sample company is removed, the Overview's records-added figures agree with an empty model.

Two real organisations: A adds a record of its own; B loads the sample company and removes it. B's panels
must count nothing that is gone, the sample's records are never "from connected sources" while it is
loaded, and A's figures do not move at any point.
"""
import re

from app import db
from tests.test_sample_company_working_data import _architect


def _page(client):
    return client.get("/dashboard/overview").get_data(as_text=True)


def _figure(html, testid):
    match = re.search(r'data-testid="%s"[^>]*>\s*([^<]*?)\s*<' % testid, html)
    return match.group(1) if match else None


def _panel(html):
    """Every figure of the records-added, year and week panels, as a person reads them."""
    return {
        "total": _figure(html, "activity-total-90d"),
        "empty": "activity-empty" in html,
        "year_empty": 'data-testid="year-empty"' in html,
        "year": _figure(html, "year-value"),
        "week_empty": "week-empty" in html,
        "recent_empty": "recent-empty" in html,
        "connected": re.findall(r"From connected sources</span><span[^>]*>\s*(\d+)\s*<", html),
    }


def _own_record(org, user):
    from app.services.archimate_backbone import create_backbone_element
    from app.services.change_source import change_source

    with change_source("screen", user_id=user.id):
        create_backbone_element(
            element_type="BusinessActor", layer="Business", name="Treasury desk", organization_id=org.id
        )
    db.session.commit()


def test_removing_the_sample_leaves_the_panels_at_nothing_and_the_other_organisation_unmoved(
    client, login_as, make_org
):
    org_a, org_b = make_org(), make_org()
    user_a = _architect(org_a, "panel-a@example.com")
    user_b = _architect(org_b, "panel-b@example.com")
    _own_record(org_a, user_a)

    login_as(client, user_a)
    html = _page(client)
    a_before = _panel(html)
    assert a_before["total"] == "1" and a_before["connected"][:1] == ["0"], a_before

    login_as(client, user_b)
    assert client.post("/architecture/sample-company/load").status_code == 302
    loaded = _panel(_page(client))
    assert int(loaded["total"]) > 0, loaded
    # the sample is the sample while it is loaded, never a connected source
    assert set(loaded["connected"]) == {"0"}, loaded

    login_as(client, user_a)
    assert _panel(_page(client)) == a_before

    login_as(client, user_b)
    assert client.post("/architecture/sample-company/remove").status_code == 303
    removed = _panel(_page(client))
    assert removed["total"] is None and removed["empty"], removed
    assert removed["year_empty"] and removed["year"] is None, removed
    assert removed["week_empty"] and removed["recent_empty"], removed

    login_as(client, user_a)
    assert _panel(_page(client)) == a_before


def _history_rows(org_id, since=None):
    """The organisation's own model history as (table, action, record id), read straight from the table."""
    from app.models.audit_log import AuditLog
    from app.modules.intelligence.services.history_service import AUDITED_TABLES

    query = AuditLog.query.filter(AuditLog.organization_id == org_id, AuditLog.table_name.in_(list(AUDITED_TABLES)))
    if since is not None:
        query = query.filter(AuditLog.created_at >= since)
    return [(r.table_name, r.action, r.record_id) for r in query.order_by(AuditLog.id)]


def test_a_real_record_added_then_deleted_still_counts_once_added_and_once_deleted_after_the_sample_is_removed(
    client, login_as, make_org
):
    from datetime import datetime, timedelta

    from app.modules.dashboard.services import overview_figures as figures
    from app.services import sample_company_service
    from app.services.change_source import change_source

    org_a, org_b = make_org(), make_org()
    user_a = _architect(org_a, "gone-a@example.com")
    user_b = _architect(org_b, "gone-b@example.com")
    _own_record(org_a, user_a)

    login_as(client, user_a)
    a_before = _panel(_page(client))
    assert a_before["total"] == "1", a_before

    login_as(client, user_b)
    assert client.post("/architecture/sample-company/load").status_code == 302
    # one real record of the organisation's own: added, then deleted by a person
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_backbone import create_backbone_element

    with change_source("screen", user_id=user_b.id):
        mine = create_backbone_element(
            element_type="BusinessActor", layer="Business", name="Treasury desk", organization_id=org_b.id
        )
    db.session.commit()
    mine_id = mine.id
    with change_source("screen", user_id=user_b.id):
        db.session.delete(db.session.get(ArchiMateElement, mine_id))
        db.session.commit()
    assert client.post("/architecture/sample-company/remove").status_code == 303

    now = datetime.utcnow()
    sample = sample_company_service.written_by_the_sample(org_b.id)
    raw = _history_rows(org_b.id)
    assert ("archimate_elements", "create", mine_id) in raw and ("archimate_elements", "delete", mine_id) in raw, raw
    # the history table names an add "create"; the panels read it as an insert
    # the same period as the panel's "Last 3 months": from the start of the day 89 days ago
    period = _history_rows(org_b.id, since=datetime.combine(now.date() - timedelta(days=89), datetime.min.time()))
    own_inserts = [r for r in period if r[1] == "create" and (r[0], r[2]) not in sample]

    kept = figures._history(org_b.id, now)
    kept_rows = [(c.table_name, c.action, c.record_id) for c in kept]
    # the sample's rows are gone, and the person's own add and delete are both there
    assert sorted(kept_rows) == sorted([("archimate_elements", "insert", mine_id), ("archimate_elements", "delete", mine_id)]), kept_rows

    activity = figures._activity(kept, now, org_b.id)
    # the records-added figure is the number of non-sample inserts in the organisation's own history
    assert activity["ranges"]["90d"]["total"] == len(own_inserts) == 1, activity["ranges"]["90d"]
    assert activity["this_year"] == "1 added this year" or activity["this_year"] == 1, activity["this_year"]
    removed = _panel(_page(client))
    assert removed["total"] == "1" and not removed["empty"], removed
    assert not removed["week_empty"], removed

    login_as(client, user_a)
    assert _panel(_page(client)) == a_before
