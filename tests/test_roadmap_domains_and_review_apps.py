"""The roadmap's Domains card explains itself when no domain is listed, and the applications under review are the
organisation's own under-review and sunset ones, found in the database."""
import uuid

from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.user import Role, User


def _user(db_session, org, label):
    Role.insert_roles()
    user = User(
        email="%s.%s@example.com" % (label, uuid.uuid4().hex[:8]), first_name="A", last_name="B",
        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
        role=Role.query.filter_by(name="Viewer").first())
    user.password = "Passw0rd!x"
    db_session.add(user)
    db_session.flush()
    return user


def test_domains_card_says_why_when_no_domain_is_listed(db_session, make_org, client, login_as):
    empty, unassessed = make_org("domains-empty"), make_org("domains-unassessed")
    db_session.add(BusinessCapability(name="Unscored capability", level=1, business_domain="Finance",
                                      organization_id=unassessed.id))
    db_session.flush()
    for org, words in ((empty, "No capabilities are recorded yet"),
                       (unassessed, "No capability has a health score yet")):
        login_as(client, _user(db_session, org, "dom"))
        page = client.get("/capability-roadmap")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'data-testid="roadmap-domains-basis"' in html and words in html, words


def test_applications_under_review_are_this_organisations_under_review_and_sunset_ones(
        db_session, make_org, client, login_as):
    ours, theirs = make_org("review-ours"), make_org("review-theirs")
    for org, name, stage in (
        (ours, "Zeta Sunset App", "3. sunset"), (ours, "Alpha Review App", "under_review"),
        (ours, "Plain Active App", "2.1 strategic"), (ours, "Presunset App", "pre-sunset"),
        (theirs, "Other Sunset App", "3. sunset"),
    ):
        db_session.add(ApplicationComponent(name=name, lifecycle_status=stage, organization_id=org.id))
    db_session.flush()

    login_as(client, _user(db_session, ours, "rev"))
    page = client.get("/enterprise/analysis/ai-architecture")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Zeta Sunset App" in html and "Alpha Review App" in html
    assert "Plain Active App" not in html and "Presunset App" not in html and "Other Sunset App" not in html
    assert html.index("Alpha Review App") < html.index("Zeta Sunset App")
