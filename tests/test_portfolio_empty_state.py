"""Portfolio empty state: an organisation with no initiatives sees the
canonical empty state with one working action "Import your portfolio"
opening the model importer; a quiet text link "Talk to us about loading
yours" pointing at the contact page remains below it. An organisation
with initiatives renders its list unchanged."""

from bs4 import BeautifulSoup

import pytest


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email="portfolio-empty-%s@example.com" % org_id,
        first_name="Portfolio",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="portfolio_manager",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_initiative(db_session, org_id, name="Test Initiative"):
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    initiative = EnterpriseInitiative(
        name=name,
        organization_id=org_id,
        status="active",
        current_phase="planning",
        health_status="green",
    )
    db_session.add(initiative)
    db_session.flush()
    return initiative


def test_portfolio_empty_state_renders_canonical_empty_state(
    app, db_session, make_org, client, login_as
):
    """An organisation with no EnterpriseInitiative sees the canonical
    empty_state: one icon, one headline, one body, one primary action
    "Import your portfolio" whose target returns 200, and a quiet
    text link "Talk to us about loading yours" below it pointing at
    the contact page."""
    org = make_org("portfolio-empty")
    user = _user(db_session, org.id)
    login_as(client, user)
    response = client.get("/portfolio/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    soup = BeautifulSoup(html, "html.parser")

    # The canonical empty_state headline and body are present
    assert "No initiatives yet" in html
    assert "Initiatives are loaded with your portfolio data." in html

    # Primary: "Import your portfolio" link opens the model importer
    import_ctas = soup.find_all(
        "a", string=lambda text: text and "Import your portfolio" in text.strip()
    )
    assert len(import_ctas) == 1, (
        "expected exactly one Import CTA, found %d" % len(import_ctas)
    )
    import_target = import_ctas[0].get("href")
    assert import_target is not None
    import_target_response = client.get(import_target)
    assert import_target_response.status_code == 200, (
        "Import CTA target %s returned %s, expected 200"
        % (import_target, import_target_response.status_code)
    )

    # Quiet: "Talk to us about loading yours" link still present below
    talk_ctas = soup.find_all(
        "a", string=lambda text: text and "Talk to us about loading yours" in text.strip()
    )
    assert len(talk_ctas) == 1, (
        "expected exactly one Talk CTA, found %d" % len(talk_ctas)
    )
    talk_target = talk_ctas[0].get("href")
    assert talk_target is not None
    talk_target_response = client.get(talk_target)
    assert talk_target_response.status_code == 200, (
        "Talk CTA target %s returned %s, expected 200"
        % (talk_target, talk_target_response.status_code)
    )

    # The quiet "Talk to us about loading yours" CTA target is the contact page
    talk_target = talk_ctas[0].get("href")
    assert "contact" in talk_target.lower(), (
        "expected Talk CTA target to contain 'contact', got %s" % talk_target
    )

    # No mention of administrator in visible page text
    for tag in soup(["script", "style"]):
        tag.decompose()
    visible_text = soup.get_text().lower()
    assert "administrator" not in visible_text


def test_portfolio_with_initiatives_renders_list_unchanged(
    app, db_session, make_org, client, login_as
):
    """An organisation with initiatives renders its list unchanged."""
    org = make_org("portfolio-with-initiatives")
    _make_initiative(db_session, org.id, "SAP Migration")
    user = _user(db_session, org.id)
    login_as(client, user)
    response = client.get("/portfolio/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "SAP Migration" in html
    # The empty state must not appear when there are initiatives
    assert "No initiatives yet" not in html
