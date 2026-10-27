
from bs4 import BeautifulSoup
from tests.test_team_invite_acceptance import _make_user
import pytest

@pytest.fixture
def make_user_fixture(db_session, make_org):
    def _make_user_for_org(org, **kwargs):
        return _make_user(db_session, org, **kwargs)
    return _make_user_for_org

def test_data_architecture_dashboard_empty_state(client, login_as, make_org, make_user_fixture):
    """Given an organization with no models, when the data architecture dashboard is viewed,
    then the page shows the explained empty state for each model type."""
    org = make_org()
    user = make_user_fixture(org)
    login_as(client, user)

    response = client.get('/architecture/data-architecture')
    assert response.status_code == 200

    soup = BeautifulSoup(response.get_data(as_text=True), 'html.parser')

    conceptual_card = soup.find(attrs={'data-testid': 'conceptual-models-card'})
    assert 'Conceptual Models' in conceptual_card.text
    assert '0' in conceptual_card.find(attrs={'data-slot': 'card-title'}).text
    assert 'No conceptual models yet. Add one to start.' in conceptual_card.text
    assert not conceptual_card.find('a')

    logical_card = soup.find(attrs={'data-testid': 'logical-models-card'})
    assert 'Logical Models' in logical_card.text
    assert '0' in logical_card.find(attrs={'data-slot': 'card-title'}).text
    assert 'No logical models yet. Add one to start.' in logical_card.text
    assert not logical_card.find('a')

    physical_card = soup.find(attrs={'data-testid': 'physical-models-card'})
    assert 'Physical Models' in physical_card.text
    assert '0' in physical_card.find(attrs={'data-slot': 'card-title'}).text
    assert 'No physical models yet. Add one to start.' in physical_card.text
    assert not physical_card.find('a')


def test_data_architecture_dashboard_with_models(client, app, login_as, make_org, make_user_fixture, db_session):
    """Given an organization with models, when the data architecture dashboard is viewed,
    then the page shows the correct count for each model type and a link to the models page."""
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

        conceptual_card = soup.find(attrs={'data-testid': 'conceptual-models-card'})
        assert 'Conceptual Models' in conceptual_card.text
        assert '1' in conceptual_card.find(attrs={'data-slot': 'card-title'}).text
        assert conceptual_card.find('a')

        logical_card = soup.find(attrs={'data-testid': 'logical-models-card'})
        assert 'Logical Models' in logical_card.text
        assert '1' in logical_card.find(attrs={'data-slot': 'card-title'}).text
        assert logical_card.find('a')

        physical_card = soup.find(attrs={'data-testid': 'physical-models-card'})
        assert 'Physical Models' in physical_card.text
        assert '1' in physical_card.find(attrs={'data-slot': 'card-title'}).text
        assert physical_card.find('a')
