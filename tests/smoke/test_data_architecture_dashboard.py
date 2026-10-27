
import os
import uuid

from bs4 import BeautifulSoup
from tests.test_team_invite_acceptance import _make_user
import pytest

pytestmark = [pytest.mark.smoke]

# The plain explanation each empty card gives: what that kind of model is, in a
# person's words, with no status label and no record id. Kept beside the test so a
# change to the wording on the page is a change here too.
EMPTY_EXPLANATION = {
    "conceptual": "A conceptual model names the main things your organisation cares about",
    "logical": "A logical model works those things into entities, their attributes and keys",
    "physical": "A physical model maps the logical design onto the tables, columns and indexes",
}


@pytest.fixture
def make_user_fixture(db_session, make_org):
    def _make_user_for_org(org, **kwargs):
        return _make_user(db_session, org, **kwargs)
    return _make_user_for_org


def _card(soup, kind):
    return soup.find(attrs={'data-testid': '%s-models-card' % kind})


def test_data_architecture_dashboard_empty_state(client, login_as, make_org, make_user_fixture):
    """Given an organization with no models, when the data architecture dashboard is viewed,
    then each card shows 0 with a plain explanation of what that kind of model is and the one
    action that creates the first one -- never a bare zero with no next step."""
    org = make_org()
    user = make_user_fixture(org)
    login_as(client, user)

    response = client.get('/architecture/data-architecture')
    assert response.status_code == 200

    soup = BeautifulSoup(response.get_data(as_text=True), 'html.parser')

    for kind, title in (('conceptual', 'Conceptual Models'),
                        ('logical', 'Logical Models'),
                        ('physical', 'Physical Models')):
        card = _card(soup, kind)
        assert card is not None, kind
        assert title in card.text
        assert '0' in card.find(attrs={'data-slot': 'card-title'}).text
        # the plain explanation of what this kind of model is
        assert EMPTY_EXPLANATION[kind] in card.text, (kind, card.text)
        # the one create action, and it goes to the page that manages the models
        create = card.find('a')
        assert create is not None, 'empty %s card offers no create action' % kind
        assert 'Add the first one' in create.text
        assert '/enterprise/data/models' in create['href']
        # no status label dressed up as a value, and no bare dash
        assert '—' not in card.text


def test_data_architecture_dashboard_with_models(client, app, login_as, make_org, make_user_fixture, db_session):
    """Given an organisation with one model of each kind, when the data architecture dashboard
    is viewed, then each card shows 1 and links to that model's page, which has content."""
    org = make_org()
    user = make_user_fixture(org)
    login_as(client, user)

    with app.app_context():
        from app.models import ConceptualDataModel, LogicalDataModel, PhysicalDataModel

        concept_model = ConceptualDataModel(name='Test Conceptual Model', description='A test model', organization_id=org.id)
        logical_model = LogicalDataModel(name='Test Logical Model', description='A test model', organization_id=org.id)
        physical_model = PhysicalDataModel(name='Test Physical Model', description='A test model', organization_id=org.id)
        db_session.add_all([concept_model, logical_model, physical_model])
        db_session.commit()

        response = client.get('/architecture/data-architecture')
        assert response.status_code == 200

        soup = BeautifulSoup(response.get_data(as_text=True), 'html.parser')

        for kind, title in (('conceptual', 'Conceptual Models'),
                            ('logical', 'Logical Models'),
                            ('physical', 'Physical Models')):
            card = _card(soup, kind)
            assert card is not None, kind
            assert title in card.text
            assert '1' in card.find(attrs={'data-slot': 'card-title'}).text
            link = card.find('a')
            assert link is not None, kind
            assert 'model_type=%s' % kind in link['href']
            # the empty-state explanation is gone once there is a real count
            assert EMPTY_EXPLANATION[kind] not in card.text
            # the page the card links to has content, not an empty shell
            target = client.get(link['href'])
            assert target.status_code == 200
            target_soup = BeautifulSoup(target.get_data(as_text=True), 'html.parser')
            assert target_soup.find('main') is not None
            assert len(target_soup.find('main').get_text(strip=True)) > 50


def test_data_architecture_counts_and_links_are_scoped_to_the_signed_in_organisation(
        client, app, login_as, make_org, make_user_fixture, db_session):
    """Two real organisations: organisation A's counts and model links never include
    organisation B's models, and B never sees A's."""
    org_a = make_org('A')
    org_b = make_org('B')
    user_a = make_user_fixture(org_a)
    user_b = make_user_fixture(org_b)

    with app.app_context():
        from app.models import ConceptualDataModel, LogicalDataModel, PhysicalDataModel

        # A has one of each kind; B has two conceptual and nothing else.
        db_session.add_all([
            ConceptualDataModel(name='A Conceptual', description='x', organization_id=org_a.id),
            LogicalDataModel(name='A Logical', description='x', organization_id=org_a.id),
            PhysicalDataModel(name='A Physical', description='x', organization_id=org_a.id),
            ConceptualDataModel(name='B Conceptual One', description='x', organization_id=org_b.id),
            ConceptualDataModel(name='B Conceptual Two', description='x', organization_id=org_b.id),
        ])
        db_session.commit()

    # Organisation A sees exactly its own one-of-each counts, and every card links.
    login_as(client, user_a)
    soup_a = BeautifulSoup(client.get('/architecture/data-architecture').get_data(as_text=True), 'html.parser')
    for kind in ('conceptual', 'logical', 'physical'):
        card = _card(soup_a, kind)
        assert '1' in card.find(attrs={'data-slot': 'card-title'}).text, kind
        assert card.find('a') is not None, kind

    # Organisation B sees its own counts: two conceptual, no logical, no physical.
    login_as(client, user_b)
    soup_b = BeautifulSoup(client.get('/architecture/data-architecture').get_data(as_text=True), 'html.parser')
    assert '2' in _card(soup_b, 'conceptual').find(attrs={'data-slot': 'card-title'}).text
    for kind in ('logical', 'physical'):
        card = _card(soup_b, kind)
        assert '0' in card.find(attrs={'data-slot': 'card-title'}).text, kind
        # a zero card offers a create action, not a link to a populated page
        assert 'Add the first one' in card.find('a').text, kind

    # B never sees A's models named on the page, and the reverse.
    page_b = client.get('/architecture/data-architecture').get_data(as_text=True)
    for name in ('A Conceptual', 'A Logical', 'A Physical'):
        assert name not in page_b, name
    login_as(client, user_a)
    page_a = client.get('/architecture/data-architecture').get_data(as_text=True)
    for name in ('B Conceptual One', 'B Conceptual Two'):
        assert name not in page_a, name


# ---------------------------------------------------------------------------
# Browser journey: the accessibility sweep and no horizontal overflow, at both
# viewports and both themes, with one model of each kind seeded for the org.
# ---------------------------------------------------------------------------

from .conftest import PAGE_TIMEOUT, PASSWORD  # noqa: E402
from .test_accessibility_audit import TAGS  # noqa: E402

VIEWPORTS = [(1440, "1440"), (390, "390")]
BLOCKING = {"serious", "critical"}


def _seed_org_with_one_of_each_model():
    """A fresh organisation, one signed-in person, and one model of each kind.

    Created through the ORM because the models have no create screen of their
    own; the dashboard under test only reads them back."""
    from app import create_app, db
    from app.models import ConceptualDataModel, LogicalDataModel, PhysicalDataModel
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        architect = Role.query.filter_by(name="Architect").one()
        org = Organization(name="DataArch Org %s" % suffix, slug="dataarch-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        email = "dataarch.%s@example.com" % suffix
        user = User(email=email, first_name="Data", last_name="Arch",
                    organization_id=org.id, enterprise_role="data_architect",
                    confirmed=True, role=architect)
        user.password = PASSWORD
        db.session.add(user)
        db.session.add_all([
            ConceptualDataModel(name="Customer domain %s" % suffix, description="seed", organization_id=org.id),
            LogicalDataModel(name="Customer logical %s" % suffix, description="seed", organization_id=org.id),
            PhysicalDataModel(name="Customer physical %s" % suffix, description="seed", organization_id=org.id),
        ])
        db.session.commit()
        return email


@pytest.fixture(scope="module")
def dataarch_email():
    return _seed_org_with_one_of_each_model()


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


@pytest.mark.journey
@pytest.mark.parametrize("width,label", VIEWPORTS, ids=[v[1] for v in VIEWPORTS])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_dashboard_is_accessible_and_does_not_overflow(browser, live_server, dataarch_email, width, label, theme):
    """axe at 1440 and 390, light and dark: no serious or critical finding; and at 390
    the page never scrolls sideways."""
    from axe_playwright_python.sync_playwright import Axe

    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _sign_in(page, live_server, dataarch_email)
        page.goto(live_server + "/architecture/data-architecture",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        if theme == "dark":
            page.evaluate("() => document.documentElement.classList.add('dark')")
            page.wait_for_timeout(400)

        report = Axe().run(page, options={"runOnly": {"type": "tag", "values": TAGS}})
        data = report.response if hasattr(report, "response") else report
        blocking = [v for v in data.get("violations", []) if v.get("impact") in BLOCKING]
        assert not blocking, "serious/critical axe violations at %s %s: %s" % (
            label, theme,
            ", ".join("%s (%s)" % (v["id"], v.get("impact")) for v in blocking))

        if width == 390:
            assert page.evaluate("document.documentElement.scrollWidth <= 390"), (
                "the page scrolls sideways at 390px (scrollWidth %s)"
                % page.evaluate("document.documentElement.scrollWidth"))
    finally:
        context.close()
