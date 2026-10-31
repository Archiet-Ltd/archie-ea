"""What a signed-in person sees while a release wave is switched on.

A signed-in address that opens in a later release answers inside the signed-in app with the person's
email filled in; the Overview and the chat never lead to a waiting list.
"""
import re

import pytest

from tests.test_sample_company_working_data import _architect


@pytest.fixture
def wave1(app, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    app.extensions.pop("release_waves_link_cache", None)
    yield app
    app.extensions.pop("release_waves_link_cache", None)


def _early_access_links(html):
    return re.findall(r'href="(/early-access[^"]*)"', html)


def test_early_access_page_inside_the_app_for_a_signed_in_person(wave1, client, login_as, make_org):
    user = _architect(make_org(), "ea-signed-in@example.com")
    login_as(client, user)
    page = client.get("/early-access/pg-ans").get_data(as_text=True)
    assert 'data-testid="early-access-page"' in page
    assert 'value="ea-signed-in@example.com"' in page
    assert 'data-testid="early-access-back"' in page
    assert 'data-testid="admin-sidebar"' in page or "sidebar" in page
    assert "public-navbar" not in page and "Coming soon" not in page


def test_early_access_page_stays_public_when_signed_out(wave1, client):
    page = client.get("/early-access/pg-ans").get_data(as_text=True)
    assert 'data-testid="early-access-page"' in page
    assert 'data-testid="early-access-back"' not in page
    assert 'value="' not in re.search(r'<input[^>]*id="early-access-email"[^>]*>', page).group(0)


@pytest.mark.parametrize("path", ["/dashboard/overview", "/ai-chat"])
def test_the_home_and_the_chat_lead_to_no_waiting_list(wave1, client, login_as, make_org, path):
    user = _architect(make_org(), "ea-home@example.com")
    login_as(client, user)
    response = client.get(path)
    assert response.status_code == 200
    assert _early_access_links(response.get_data(as_text=True)) == []
