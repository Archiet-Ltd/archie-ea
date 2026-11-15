"""The roadmap counts its figures once per request that needs them.

An architect opens the capability roadmap: the page counts the figures once and the three first reads
(gaps, plateaus, work packages) do not count them again. A read after a save carries fresh figures,
once. A work-package row carries its evidence line only when the page asks for it.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

FIRST_READS = (
    "/capability-map/api/roadmap/gaps?figures=0",
    "/capability-map/api/roadmap/plateaus?figures=0",
    "/capability-map/api/roadmap/work-packages?figures=0&footer=1",
)


def _user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    role = Role.query.filter_by(name="User").first()
    user = User(
        email=f"caproad-cost-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(), last_name="Test", organization_id=org.id, confirmed=True,
        enterprise_role="enterprise_architect", role_id=role.id if role else None)
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session, org, packages):
    from app.models.implementation_migration import Plateau
    from app.services import work_package_service

    for i in range(packages):
        work_package_service.create_work_package(organization_id=org.id, name=f"Package {org.id}-{i}")
    db_session.add(Plateau(organization_id=org.id, name=f"Plateau {org.id}", sequence_order=1))
    db_session.flush()


@pytest.fixture
def counted(monkeypatch):
    from app.services import result_footer_service

    real = result_footer_service.roadmap_figures
    calls = []

    def counting(organization_id):
        calls.append(organization_id)
        return real(organization_id)

    monkeypatch.setattr(result_footer_service, "roadmap_figures", counting)
    return calls


def test_a_page_load_and_its_first_reads_count_the_figures_once(db_session, make_org, client, login_as, counted):
    org_a, org_b = make_org("cost-a"), make_org("cost-b")
    user_a = _user(db_session, org_a, "a")
    _seed(db_session, org_a, packages=3)
    _seed(db_session, org_b, packages=1)
    login_as(client, user_a)

    html = client.get("/capability-roadmap").get_data(as_text=True)
    for url in FIRST_READS:
        body = client.get(url).get_json()
        assert body["success"] is True and body["figures"] is None, url
    assert "Work packages: 3" in html
    assert counted == [org_a.id], counted


def test_a_read_after_a_save_counts_the_figures_once_and_returns_them(db_session, make_org, client, login_as, counted):
    org_a, org_b = make_org("cost-c"), make_org("cost-d")
    user_a = _user(db_session, org_a, "a")
    _seed(db_session, org_a, packages=3)
    _seed(db_session, org_b, packages=1)
    login_as(client, user_a)

    for url in ("/capability-map/api/roadmap/gaps", "/capability-map/api/roadmap/plateaus",
                "/capability-map/api/roadmap/work-packages"):
        del counted[:]
        figures = client.get(url).get_json()["figures"]
        assert counted == [org_a.id], (url, counted)
        assert figures["work_package_count"] == 3 and "Work packages: 3" in figures["footer_html"], url


def test_a_work_package_row_carries_its_evidence_line_only_when_asked(db_session, make_org, client, login_as):
    org_a, org_b = make_org("cost-e"), make_org("cost-f")
    user_a = _user(db_session, org_a, "a")
    _seed(db_session, org_a, packages=2)
    _seed(db_session, org_b, packages=1)
    login_as(client, user_a)

    plain = client.get("/capability-map/api/roadmap/work-packages").get_json()["work_packages"]
    asked = client.get("/capability-map/api/roadmap/work-packages?footer=1").get_json()["work_packages"]
    assert len(plain) == len(asked) == 2
    assert all("footer_html" not in row for row in plain)
    assert all(row.get("footer_html") for row in asked)


def test_the_package_total_is_the_organisations_whatever_gap_is_asked_for(db_session, make_org, client, login_as):
    org_a, org_b = make_org("cost-g"), make_org("cost-h")
    user_a = _user(db_session, org_a, "a")
    _seed(db_session, org_a, packages=2)
    _seed(db_session, org_b, packages=4)
    login_as(client, user_a)

    body = client.get("/capability-map/api/roadmap/work-packages?gap_id=999999999").get_json()
    assert body["work_packages"] == [] and body["all_count"] == 2, body
