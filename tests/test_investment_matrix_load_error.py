"""When the scores cannot be loaded the matrix says so; it never tells an organisation to add capabilities."""
import pytest

from tests.test_admin_change_account_type_preselect import _get_role, _make_org, _make_user

PAGES = ("/architecture/investment-priorities", "/strategic/investment-matrix")


@pytest.mark.parametrize("path", PAGES)
def test_a_scorer_failure_is_an_error_with_a_retry_not_an_empty_organisation(
        app, db_session, login_as, client, monkeypatch, path):
    org = _make_org(db_session, "scorefail")
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
    assert response.status_code == 200, response.status_code
    assert "could not be loaded" in html
    assert 'data-testid="investment-load-error"' in html
    assert f'href="{path}"' in html and "Try again" in html
    assert "No capabilities yet" not in html
    assert "Add capabilities" not in html
