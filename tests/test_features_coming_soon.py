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
    if switch == "1":
        assert hub
    else:
        assert not hub, "with the switch off every capability opens, so the hub lists none: %s" % hub
    assert not set(hub) - set(listed), "waiting lists the hub links to but Coming soon omits: %s" % sorted(set(hub) - set(listed))

    missing = []
    for page in load_all_pages():
        if page.family != "module" or page.url in MERGED_PAGES or page.front_matter.get("state") == "not_planned":
            continue
        answer = _waiting_list_address(client, page.url)
        # a page waiting for a screen is covered by the entry of the capability that screen belongs to
        from app.services.public_pages import _parse_front_matter

        source_matter, _body = _parse_front_matter(page.source_path.read_text(encoding="utf-8"))
        screen = (source_matter.get("released_variant") or {}).get("screen") if answer is not None else None
        if screen:
            from app.release_waves import code_slug, entry_for, group_for

            if "/early-access/" + code_slug(group_for(entry_for(str(screen)))[0]) in listed:
                continue
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


# ── what the features page calls delivered, and where every Coming soon link lands ───────────────────────────────


def _sections(html):
    """{capability id: its html} for each capability heading on the page."""
    body = html.split('id="coming-soon"', 1)[0]
    parts = re.split(r'<h3 id="([a-z0-9-]+)">', body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _delivered_links(section):
    """The page's links under "Delivered today by"."""
    out = []
    for para in re.findall(r"<p>Delivered today by:(.*?)</p>", section, flags=re.S):
        out += re.findall(r'href="([^"]+)"', para)
    return out


def test_nothing_is_delivered_today_that_answers_a_waiting_list(app, switch):
    client = app.test_client()
    html = client.get("/features").get_data(as_text=True)
    links = [(sid, link) for sid, section in _sections(html).items() for link in _delivered_links(section)]
    assert links, "no link under Delivered today"
    for sid, link in links:
        page = client.get(link)
        assert page.status_code == 200, link
        assert _waiting_list_address(client, link) is None, "%s (%s) is delivered today but answers a waiting list" % (link, sid)


def test_a_capability_none_of_whose_pages_open_is_coming_soon_not_delivered(app, switch):
    from pathlib import Path

    raw = (Path(app.root_path).parent / "content/pages/site/features.md").read_text(encoding="utf-8")
    client = app.test_client()
    html = client.get("/features").get_data(as_text=True)
    sections = _sections(html)
    listed = html.split('id="coming-soon"', 1)[1].split("</ul>", 1)[0]
    held = []
    for sid, chunk in re.findall(r'<h3 id="([a-z0-9-]+)">(.*?)(?=<h3 id="|\n## |\n---\n)', raw, flags=re.S):
        links = [m for para in re.findall(r"Delivered today by:(.*?)\n\n", chunk, flags=re.S) for m in re.findall(r"\]\((/[^)]+)\)", para)]
        if links and all(_waiting_list_address(client, link) is not None for link in links):
            held.append(sid)
            assert "Delivered today" not in sections[sid], sid
            assert "<strong>Today:</strong>" not in sections[sid], sid
            assert listed.count('id="coming-%s"' % sid) == 1, "%s is closed but not listed once" % sid
    if switch == "1":
        assert held, "no capability is closed at the first release"
    else:
        assert not held, "with the switch off nothing is closed: %s" % held


def test_every_coming_soon_link_on_the_page_lands_on_an_item_that_names_it(app, switch):
    html = app.test_client().get("/features").get_data(as_text=True)
    listed = html.split('id="coming-soon"', 1)[1].split("</ul>", 1)[0]
    pairs = re.findall(r'<h3 id="([a-z0-9-]+)">(.*?)</h3>(.*?)(?=<h3 id="|<h2 )', html.split('id="coming-soon"', 1)[0], flags=re.S)
    seen = 0
    for sid, title, chunk in pairs:
        for anchor in re.findall(r'<a href="#(coming-(?!soon")[a-z0-9-]+)">Coming soon</a>', chunk):
            seen += 1
            assert anchor == "coming-" + sid
            item = re.findall(r'<li id="%s">(.*?)</li>' % anchor, listed, flags=re.S)
            assert len(item) == 1, "%s has %d list items" % (anchor, len(item))
            assert item[0].startswith(title), (title, item[0][:60])
            assert len(item[0]) > len(title) + 40, "%s lists no description" % anchor
    assert seen >= 10
    assert 'href="#coming-soon">Coming soon</a> below' in html


def test_one_capability_is_not_in_both_the_delivered_lines_and_the_list(app, switch):
    """Each address in the list answers a waiting list, and none of them is also a delivered link."""
    client = app.test_client()
    html = client.get("/features").get_data(as_text=True)
    delivered = {link for section in _sections(html).values() for link in _delivered_links(section)}
    assert not delivered & set(_listed(html))


def test_no_capability_is_listed_twice_by_name_or_address(app, switch):
    html = app.test_client().get("/features").get_data(as_text=True)
    section = html.split('id="coming-soon"', 1)[1].split("</ul>", 1)[0]
    names = [re.sub(r"<[^>]+>", "", n).split(":")[0].strip().lower() for n in re.findall(r"<li[^>]*>(.*?)</li>", section, flags=re.S)]
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)
    assert "scenarios" not in names or "simulation scenarios" not in names


def test_the_list_names_no_date_quarter_wave_or_plan(app, switch):
    html = app.test_client().get("/features").get_data(as_text=True)
    text = re.sub(r"<[^>]+>", " ", html).lower()
    for word in ("wave", "roadmap for", "planned", "next release", "to follow", "next quarter", "this quarter", "next year"):
        assert word not in text, word
    assert not re.search(r"\bq[1-4]\b|\b20[2-3]\d\b", re.sub(r"(?s)<footer.*", "", html).lower())
    assert not re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b \d", text)


def test_with_the_switch_off_the_list_does_not_hold_a_capability_the_page_delivers(app, switch):
    html = app.test_client().get("/features").get_data(as_text=True)
    addresses = _listed(html)
    if switch == "off":
        assert not [a for a in addresses if a.startswith("/early-access/")], "open capabilities listed as not available: %s" % addresses
        delivered = {link for section in _sections(html).values() for link in _delivered_links(section)}
        assert {"/modules/business-case", "/modules/vendors", "/modules/org-chart"} <= delivered
    else:
        assert "/early-access/business-cases" in addresses
