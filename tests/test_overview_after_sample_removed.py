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
