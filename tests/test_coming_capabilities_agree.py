"""The early-access hub, the features page's Coming soon list and the sitemap read one list of coming capabilities."""

from __future__ import annotations

import re

import pytest


@pytest.fixture(params=["off", "1"])
def switch(request, app, _schema, monkeypatch):
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "" if request.param == "off" else request.param)
    return request.param


def _rows(html, start_marker, end_marker):
    section = html.split(start_marker, 1)[1].split(end_marker, 1)[0]
    rows = []
    for item in re.findall(r"<li[^>]*>(.*?)</li>", section, flags=re.S):
        link = re.search(r'<a href="([^"]+)">(.*?)</a>', item, flags=re.S)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", item)).strip()
        name = re.sub(r"<[^>]+>", "", link.group(2)).strip() if link else text.split(":")[0].strip()
        rows.append({"name": name, "href": link.group(1) if link else None, "text": text,
                     "sentence": text[len(name):].lstrip(": ").strip()})
    return rows


def _features(client):
    return _rows(client.get("/features").get_data(as_text=True), 'id="coming-soon"', "</ul>")


def _hub(client):
    html = client.get("/early-access").get_data(as_text=True)
    return html, _rows(html, 'data-testid="early-access-hub"', "</article>")


def _sitemap(client):
    return re.findall(r"<loc>https://entelim\.org([^<]*)</loc>", client.get("/sitemap.xml").get_data(as_text=True))


def test_the_hub_lists_exactly_the_coming_capabilities_that_have_a_page_to_open(app, switch):
    client = app.test_client()
    coming = _features(client)
    _html, hub = _hub(client)
    held_page = {r["href"] for r in coming if r["href"] and not client.get(r["href"]).get_data(as_text=True).count(
        'data-testid="early-access-page"') and r["href"] not in _sitemap(client)}
    openable = {r["name"] for r in coming if r["href"] and r["href"] not in held_page}
    assert openable, "no coming capability has a page to open"
    assert {r["name"] for r in hub} == openable, (
        "only on the hub: %s; only on the features page: %s" % (
            sorted({r["name"] for r in hub} - openable), sorted(openable - {r["name"] for r in hub})))


def test_the_hub_opens_with_an_early_access_page_and_lists_one_alphabetical_list(app, switch):
    client = app.test_client()
    _html, hub = _hub(client)
    names = [r["name"] for r in hub]
    assert names == sorted(names, key=str.lower)
    assert hub[0]["href"].startswith("/early-access/"), "the first row opens %s" % hub[0]["href"]


def test_with_the_switch_off_the_hub_does_not_say_nothing_is_waiting_while_the_features_page_lists_coming(app, switch):
    client = app.test_client()
    html, hub = _hub(client)
    assert _features(client)
    assert "Nothing is waiting" not in html
    assert hub, "the hub lists no capability while the features page lists some with a waiting list"


def test_the_sitemap_carries_the_hub_when_it_lists_a_row_and_every_early_access_page_the_hub_lists(app, switch):
    client = app.test_client()
    _html, hub = _hub(client)
    paths = set(_sitemap(client))
    assert ("/early-access" in paths) is bool(hub)
    for row in hub:
        if row["href"].startswith("/early-access/"):
            assert row["href"] in paths, "%s is on the hub but not in the sitemap" % row["href"]
    for path in paths:
        if path.startswith("/early-access/"):
            assert path in {r["href"] for r in hub}, "%s is in the sitemap but not on the hub" % path


def test_no_name_is_listed_twice_and_one_capability_has_one_name(app, switch):
    client = app.test_client()
    names = [r["name"].lower() for r in _features(client)]
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)
    assert not ("scenarios" in names and "simulation scenarios" in names)
    assert "risk management" in names
    assert "risk register and risk views" not in names, "the risk register is listed under two names"
    if switch == "1":
        assert "business model canvas" in names and "strategy management" in names


def test_a_hub_row_carries_the_name_the_features_list_gives_it(app, switch):
    client = app.test_client()
    coming = {r["href"]: r for r in _features(client) if r["href"]}
    _html, hub = _hub(client)
    assert hub
    for row in hub:
        assert coming[row["href"]]["name"] == row["name"], row["href"]
        assert coming[row["href"]]["sentence"] == row["sentence"], row["href"]


def test_no_list_entry_repeats_its_own_label_and_every_sentence_is_written_out(app, switch):
    client = app.test_client()
    _html, hub = _hub(client)
    for row in _features(client) + hub:
        assert row["sentence"], "%s has no sentence" % row["name"]
        assert not row["sentence"].lower().startswith(row["name"].lower() + ":"), row["text"]
        assert "coming soon:" not in row["sentence"].lower(), row["text"]
        assert row["sentence"][0].isupper(), "%s: a sentence that starts in lower case: %s" % (row["name"], row["sentence"][:40])
        assert row["sentence"].rstrip().endswith((".", "!", "?")), "%s: not a finished sentence: %s" % (row["name"], row["sentence"][-30:])


def test_a_held_capability_is_introduced_once_in_its_section(app, switch):
    html = app.test_client().get("/features").get_data(as_text=True)
    body = html.split('id="coming-soon"', 1)[0]
    assert "Coming soon</a>: Coming soon" not in body
    for section in re.split(r'<h3 id="', body)[1:]:
        assert len(re.findall(r"Coming soon</a>:", section)) <= 1, "a capability says Coming soon twice: %s" % section[:60]
    for row in _features(app.test_client()):
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", row["sentence"]) if x.strip()]
        assert len(sentences) == len(set(sentences)), "%s says a sentence twice" % row["name"]
    for paragraph in re.findall(r"<p>(.*?)</p>", body, flags=re.S):
        assert len(re.findall(r"Coming soon</a>:", paragraph)) <= 1, paragraph[:80]
        for match in re.finditer(r"Coming soon</a>:\s*([a-z])", paragraph):
            pytest.fail("a held description starts in lower case: %s" % paragraph[:100])
