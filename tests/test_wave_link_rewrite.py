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
    ("/frameworks/?tab=b", "?tab=a&page=2", "/frameworks/?tab=a&page=2"),
    ("/capability-maturity/search?q=old&view=list", "?q=Pay", "/capability-maturity/search?view=list&q=Pay"),
    ("/frameworks/", "", "/frameworks/"),
    ("/frameworks/?view=map", "?view=list#top", "/frameworks/?view=list#top"),
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
    # the link's view=list wins over the destination's view=map; its tab=a is kept
    assert 'href="/dashboard/health?tab=a&view=list"' in out


def test_addresses_that_move_into_each_other_end_on_a_page_that_opens_and_are_logged_once(wave1, monkeypatch, caplog):
    where = {"/applications/": "/dashboard/health", "/dashboard/health": "/applications/"}
    monkeypatch.setattr(hook, "state_for", lambda entry, wave: REDIRECT)
    monkeypatch.setattr(hook, "entry_for", lambda rule, endpoint=None: [rule, "pg-x", "page", None, None])
    monkeypatch.setattr(hook, "redirect_target", lambda entry, args: where[entry[0]])
    with wave1.test_request_context("/"), caplog.at_level(logging.WARNING):
        first = hook.early_access_target("/applications/")
        again = hook.early_access_target("/applications/")
    # ends on the early-access page of the last address reached, which opens, not on an address that moves on
    assert first == again == "/early-access/pg-x"
    assert len([r for r in caplog.records if "lead back" in r.getMessage()]) == 1
