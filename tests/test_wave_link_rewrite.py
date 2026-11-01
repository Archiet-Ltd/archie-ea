"""The link rewriter of the wave switch: queries merge cleanly and addresses that move into each other end."""

from __future__ import annotations

import logging

import pytest

from app.release_waves import REDIRECT, hook


@pytest.fixture
def wave1(app, _schema, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    saved = app.extensions.pop("release_waves_link_cache", None)
    yield app
    app.extensions.pop("release_waves_link_cache", None)
    if saved is not None:
        app.extensions["release_waves_link_cache"] = saved


@pytest.mark.parametrize("target, query, expected", [
    ("/frameworks/", "?tab=a", "/frameworks/?tab=a"),
    ("/frameworks/?view=map", "?tab=a", "/frameworks/?view=map&tab=a"),
    # the link's own value wins over the destination's, so a search term the person typed is never lost
    ("/frameworks/?x=b", "?x=a&page=2", "/frameworks/?x=a&page=2"),
    ("/capability-maturity/search?q=old&view=list", "?q=Pay", "/capability-maturity/search?view=list&q=Pay"),
    # the destination's routing keys (lens, view, tab) stand against the link's; q and page are the link's
    ("/frameworks/?tab=b", "?tab=a&page=2", "/frameworks/?tab=b&page=2"),
    ("/capability-map/?lens=health", "?lens=x&q=Pay", "/capability-map/?lens=health&q=Pay"),
    ("/capability-map/?view=list", "?q=Pay&view=tree", "/capability-map/?view=list&q=Pay"),
    ("/frameworks/", "", "/frameworks/"),
    ("/frameworks/?view=map", "?view=list#top", "/frameworks/?view=map#top"),
    ("/frameworks/", "#top", "/frameworks/#top"),
])
def test_a_links_own_query_joins_the_destinations_without_repeating_a_mark_or_a_key(target, query, expected):
    merged = hook.merge_query(target, query)
    assert merged == expected
    assert merged.count("?") <= 1


def test_a_link_to_a_moved_address_with_a_query_keeps_one_question_mark(wave1, monkeypatch):
    monkeypatch.setattr(hook, "state_for", lambda entry, wave: REDIRECT if entry[0] == "from" else "open")
    monkeypatch.setattr(hook, "entry_for", lambda rule, endpoint=None: (
        ["from", "x", "page", "/dashboard/health?view=map", "W1"] if rule == "/applications/" else ["W1", "x", "page", None, None]))
    monkeypatch.setattr(hook, "redirect_target", lambda entry, args: entry[3])
    response = wave1.response_class('<a href="/applications/?tab=a&view=list">x</a>', mimetype="text/html")
    with wave1.test_request_context("/"):
        out = hook.point_closed_links_at_early_access(response).get_data(as_text=True)
    # the destination's view=map stands against the link's view=list; the link's tab=a is kept
    assert 'href="/dashboard/health?view=map&tab=a"' in out


def test_addresses_that_move_into_each_other_end_on_a_page_that_opens_and_are_logged_once(wave1, monkeypatch, caplog):
    where = {"/applications/": "/dashboard/health", "/dashboard/health": "/applications/"}
    monkeypatch.setattr(hook, "state_for", lambda entry, wave: REDIRECT)
    monkeypatch.setattr(hook, "entry_for", lambda rule, endpoint=None: [rule, "PG-BC", "page", None, None])
    monkeypatch.setattr(hook, "redirect_target", lambda entry, args: where[entry[0]])
    with wave1.test_request_context("/"), caplog.at_level(logging.WARNING):
        first = hook.early_access_target("/applications/")
        again = hook.early_access_target("/applications/")
    # ends on the early-access page of the last address reached, which opens, not on an address that moves on
    assert first == again == "/early-access/business-cases"
    # the address it names is a real group: the page opens
    monkeypatch.undo()
    monkeypatch.setitem(wave1.config, "RELEASE_WAVE", "1")
    assert wave1.test_client().get(first).status_code == 200
    assert len([r for r in caplog.records if "lead back" in r.getMessage()]) == 1


def _rewrite_one_link(wave1, monkeypatch, href):
    monkeypatch.setattr(hook, "state_for", lambda entry, wave: REDIRECT if entry[0] == "from" else "open")
    monkeypatch.setattr(hook, "entry_for", lambda rule, endpoint=None: (
        ["from", "x", "page", "/capability-map/?view=list&q=", "W1"] if rule == "/capability-maturity/search" else ["W1", "x", "page", None, None]))
    monkeypatch.setattr(hook, "redirect_target", lambda entry, args: entry[3])
    response = wave1.response_class(f'<a href="{href}">x</a>', mimetype="text/html")
    with wave1.test_request_context("/"):
        return hook.point_closed_links_at_early_access(response).get_data(as_text=True)


def test_a_link_written_with_an_encoded_ampersand_keeps_the_search_term_once(wave1, monkeypatch):
    out = _rewrite_one_link(wave1, monkeypatch, "/capability-maturity/search?view=tree&amp;q=Pay")
    assert out == '<a href="/capability-map/?view=list&amp;q=Pay">x</a>'
    assert out.count("q=") == 1 and "amp;q=" in out and "q=&" not in out


@pytest.mark.parametrize("target, query, expected", [
    ("/capability-map/?view=list", "?view=tree&amp;q=Pay", "/capability-map/?view=list&amp;q=Pay"),
    ("/capability-map/?q=old", "?q=a&amp;q=b", "/capability-map/?q=a&amp;q=b"),
    ("/capability-map/?q=", "?q=Pay", "/capability-map/?q=Pay"),
    ("/capability-map/?view=list&q=", "?q=Pay", "/capability-map/?view=list&q=Pay"),
])
def test_encoded_queries_are_decoded_once_and_repeated_keys_stay(target, query, expected):
    # a link written with "&amp;" is answered with "&amp;" (encoded once); a plain one stays plain
    merged = hook.merge_query(target, query, in_html=True)
    assert merged == expected


def test_an_empty_destination_value_never_overrides_the_links_value():
    assert hook.merge_query("/capability-map/?q=", "?q=Pay") == "/capability-map/?q=Pay"
    assert hook.merge_query("/capability-map/?q=Pay", "?q=") == "/capability-map/?q="
