"""Portfolio empty state: an organisation with no initiatives sees the
canonical empty state with one working action; an organisation with
initiatives renders its list unchanged."""

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
    empty_state: one icon, one headline, one body, one action whose target
    returns 200. No mention of an administrator or an import."""
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

    # Exactly one action button/link
    ctas = soup.find_all("a", string=lambda text: text and "Talk to us about loading yours" in text.strip())
    assert len(ctas) == 1, "expected exactly one CTA, found %d" % len(ctas)

    # The CTA target returns 200
    target = ctas[0].get("href")
    assert target is not None
    target_response = client.get(target)
    assert target_response.status_code == 200, (
        "CTA target %s returned %s, expected 200" % (target, target_response.status_code)
    )

    # No mention of administrator or import in visible page text
    for tag in soup(["script", "style"]):
        tag.decompose()
    visible_text = soup.get_text().lower()
    assert "administrator" not in visible_text
    assert "import" not in visible_text


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
