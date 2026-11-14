"""The roadmap tiles and the evidence line come from one count.

Two real organisations, each with its own packages and plateaus. Signed into
one, the page's server HTML carries a figure in each of the four tile spans
(no blank first paint), and every answer the page redraws from carries the same
figures and an evidence line built from them. The summary-footer route is gone.
"""

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    user = User(
        email=f"caproad-count-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(), last_name="Test", organization_id=org.id, confirmed=True,
        enterprise_role="enterprise_architect", role_id=role.id if role else None)
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session, org, packages, plateaus):
    from app.models.implementation_migration import Plateau
    from app.services import work_package_service

    for i in range(packages):
        work_package_service.create_work_package(organization_id=org.id, name=f"Package {org.id}-{i}")
    for i in range(plateaus):
        db_session.add(Plateau(organization_id=org.id, name=f"Plateau {org.id}-{i}", sequence_order=i))
    db_session.flush()


def _span(html, testid):
    m = re.search(r'data-testid="%s"[^>]*>([^<]*)<' % testid, html)
    assert m, testid
    return m.group(1).strip()


def test_first_paint_carries_each_tile_figure_and_answers_agree(db_session, make_org, client, login_as):
    org_a, org_b = make_org("count-a"), make_org("count-b")
    user_a, user_b = _user(db_session, org_a, "a"), _user(db_session, org_b, "b")
    _seed(db_session, org_a, packages=3, plateaus=2)
    _seed(db_session, org_b, packages=1, plateaus=1)

    login_as(client, user_a)
    html = client.get("/capability-roadmap").get_data(as_text=True)
    assert _span(html, "rdm-plateaus-tile") == "2", _span(html, "rdm-plateaus-tile")
    assert _span(html, "rdm-work-packages-tile") == "3"
    assert _span(html, "rdm-recorded-gaps") != ""
    assert _span(html, "rdm-ai-generated") != ""
    assert "Work packages: 3" in html and "Plateaus: 2" in html

    for url in ("/capability-map/api/roadmap/work-packages", "/capability-map/api/roadmap/plateaus",
                "/capability-map/api/roadmap/gaps"):
        body = client.get(url).get_json()
        figures = body["figures"]
        assert figures["work_package_count"] == 3 and figures["plateau_count"] == 2, (url, figures)
        assert "Work packages: 3" in figures["footer_html"] and "Plateaus: 2" in figures["footer_html"], url

    assert client.get("/capability-map/api/roadmap/summary-footer").status_code == 404

    login_as(client, user_b)
    other = client.get("/capability-map/api/roadmap/plateaus").get_json()["figures"]
    assert other["work_package_count"] == 1 and other["plateau_count"] == 1, other
