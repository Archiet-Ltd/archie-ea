"""Loading the sample company: who may do it, what it creates, and that it stays in one organisation."""
from app import db
from app.models.user import Role, User


def _user(org, email, role_name, enterprise_role):
    Role.insert_roles()
    user = User(
        email=email, first_name="Test", last_name=role_name, organization_id=org.id,
        enterprise_role=enterprise_role, confirmed=True,
    )
    user.role = Role.query.filter_by(name=role_name).one()
    user.password = "password"
    db.session.add(user)
    db.session.commit()
    return user


def _element_count(org_id):
    return db.session.execute(
        db.text("SELECT count(*) FROM archimate_elements WHERE organization_id = :o"), {"o": org_id}
    ).scalar()


def test_load_sample_company_requires_permission(client, login_as, make_org):
    """A viewer cannot load the sample, and nothing is created for them."""
    org = make_org()
    viewer = _user(org, "viewer@example.com", "Viewer", "viewer")
    login_as(client, viewer)
    response = client.post("/architecture/sample-company/load")
    assert response.status_code in (403, 302), f"Got {response.status_code}"
    assert _element_count(org.id) == 0


def test_load_sample_company_creates_elements(client, login_as, make_org):
    """An architect loading the sample gets elements, including the Payments service."""
    org = make_org()
    architect = _user(org, "architect@example.com", "Architect", "enterprise_architect")
    before = _element_count(org.id)
    login_as(client, architect)
    response = client.post("/architecture/sample-company/load", follow_redirects=False)
    assert response.status_code == 302, f"Got {response.status_code}"
    assert "/dashboard/overview" in response.location
    assert _element_count(org.id) > before
    payments = db.session.execute(
        db.text(
            "SELECT count(*) FROM archimate_elements WHERE organization_id = :o "
            "AND name = 'Payments service' AND type = 'ApplicationComponent'"
        ),
        {"o": org.id},
    ).scalar()
    assert payments == 1


def test_load_sample_twice_is_idempotent(client, login_as, make_org):
    """Loading the sample twice does not duplicate elements."""
    org = make_org()
    architect = _user(org, "architect@example.com", "Architect", "enterprise_architect")
    login_as(client, architect)
    assert client.post("/architecture/sample-company/load").status_code == 302
    first = _element_count(org.id)
    assert first > 0
    login_as(client, architect)
    assert client.post("/architecture/sample-company/load").status_code == 302
    assert _element_count(org.id) == first


def test_sample_does_not_leak_across_organisations(client, login_as, make_org):
    """Loading the sample in one organisation leaves the other with no elements."""
    org1, org2 = make_org(), make_org()
    architect1 = _user(org1, "architect1@example.com", "Architect", "enterprise_architect")
    _user(org2, "architect2@example.com", "Architect", "enterprise_architect")
    login_as(client, architect1)
    assert client.post("/architecture/sample-company/load").status_code == 302
    assert _element_count(org1.id) > 0
    assert _element_count(org2.id) == 0
