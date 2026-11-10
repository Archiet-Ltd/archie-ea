"""The features page's Coming soon list is every capability whose page answers a visitor with a waiting list."""

from __future__ import annotations

import re

import pytest


@pytest.fixture(params=["off", "1"])
def switch(request, app, _schema, monkeypatch):
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "" if request.param == "off" else request.param)
    return request.param


def _listed(html):
    section = html.split('id="coming-soon"', 1)[1]
    section = section.split("</ul>", 1)[0]
    return re.findall(r'<li><a href="([^"]+)">', section)


def _waiting_list_address(client, path):
    """The waiting list a page answers with: its own address, or the early-access group it names; None if open."""
    html = client.get(path).get_data(as_text=True)
    if 'data-testid="feature-interest"' in html:
        return path
    if 'data-testid="early-access-form"' in html:
        return re.search(r'action="(/early-access/[a-z0-9-]+)', html).group(1)
    return None


def test_the_list_holds_every_waiting_list_page_the_hub_and_the_module_pages_answer(app, switch):
    from app.services.public_pages import MERGED_PAGES, load_all_pages

    client = app.test_client()
    listed = _listed(client.get("/features").get_data(as_text=True))
    assert listed, "Coming soon lists nothing"
    assert len(listed) == len(set(listed)), "an item is listed twice"

    hub = re.findall(r'href="(/early-access/[a-z0-9-]+)"', client.get("/early-access").get_data(as_text=True))
    assert hub
    assert not set(hub) - set(listed), "waiting lists the hub links to but Coming soon omits: %s" % sorted(set(hub) - set(listed))

    missing = []
    for page in load_all_pages():
        if page.family != "module" or page.url in MERGED_PAGES or page.front_matter.get("state") == "not_planned":
            continue
        answer = _waiting_list_address(client, page.url)
        if answer is not None and answer not in listed and page.url not in listed:
            missing.append((page.url, answer))
    assert not missing, "module pages answering a waiting list that Coming soon omits: %s" % missing


def test_every_listed_item_lands_on_a_waiting_list_with_a_heading(app, switch):
    client = app.test_client()
    for path in _listed(client.get("/features").get_data(as_text=True)):
        response = client.get(path)
        html = response.get_data(as_text=True)
        assert response.status_code == 200, path
        assert re.search(r"<h1[^>]*>\s*\S", html), path
        assert _waiting_list_address(client, path) is not None, "%s has no waiting list" % path
