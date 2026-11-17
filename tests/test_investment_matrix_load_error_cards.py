"""A scorer failure is an error with a status code, and its cards claim nothing about the organisation."""
import pytest

from tests.test_admin_change_account_type_preselect import _get_role, _make_org, _make_user

PAGES = ("/architecture/investment-priorities", "/strategic/investment-matrix")


@pytest.mark.parametrize("path", PAGES)
def test_the_failed_page_answers_503_and_its_cards_read_unavailable(
        app, db_session, login_as, client, monkeypatch, path):
    org = _make_org(db_session, "cardsfail")
    user = _make_user(db_session, org, _get_role(db_session, "Administrator"), is_platform_admin=True)
    db_session.commit()

    def broken(*args, **kwargs):
        raise RuntimeError("scorer down")

    import app.modules.architecture.routes.architecture_routes as architecture_routes
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    monkeypatch.setattr(architecture_routes, "_assemble_investment_priorities_context", broken)
    monkeypatch.setattr(InvestmentPrioritizationService, "analyze_investment_priorities", broken)
    with app.app_context():
        login_as(client, user)
        response = client.get(path)

    html = response.get_data(as_text=True)
    assert response.status_code == 503, response.status_code
    assert 'data-testid="investment-load-error"' in html
    cards = html.split('data-testid="total-capabilities"', 1)[1].split("AI Priority Suggestions", 1)[0]
    for claim in ("Not estimated", "Not scored", "No capability has linked work",
                  "No capability has an importance rating"):
        assert claim not in cards, claim
    assert cards.count("Unavailable") >= 4
