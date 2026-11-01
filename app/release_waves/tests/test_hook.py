"""Tests for the release wave hook."""

from unittest.mock import patch

import pytest
from flask import Flask

from app.release_waves import CLOSED
from app.release_waves.hook import early_access_target


def _app():
    app = Flask(__name__)
    app.config["SERVER_NAME"] = "localhost"
    return app


def test_cache_size_limit():
    """The link-rewriting cache is bounded: 10,000 distinct crafted paths never
    grow it past the cap (``lru_cache(maxsize=4096)`` in ``early_access_target``)."""
    app = _app()
    app.testing = True
    app.add_url_rule("/path/<int:n>", endpoint="test.endpoint", view_func=lambda n: "ok")

    with patch("app.release_waves.hook.configured_wave", return_value=1), patch(
        "app.release_waves.hook.entry_for",
        return_value=["W5", "group1", "page", None, None],
    ), patch("app.release_waves.hook.state_for", return_value=CLOSED), patch(
        "app.release_waves.hook.group_for",
        return_value=("group1", {"title": "Group 1"}),
    ):
        with app.app_context():
            for i in range(10000):
                early_access_target(f"/path/{i}")

            cache = app.extensions["release_waves_link_cache"]
            info = cache.cache_info()
            assert info.maxsize == 4096
            assert info.currsize <= 4096, (
                f"the cache grew to {info.currsize} entries over 10,000 crafted paths; "
                "it must stay at or under its declared cap"
            )


def test_unmatched_path_is_left_alone():
    """A path the router cannot match at all (not a wrong mapping, just not ours) comes back unchanged, never raises."""
    app = _app()
    app.testing = True

    with patch("app.release_waves.hook.configured_wave", return_value=1):
        with app.app_context():
            assert early_access_target("/this/matches/no/rule/at/all") is None


def test_wrong_mapping_raises_in_testing():
    """A rule the router matched but the wave map cannot read (a malformed entry,
    not an absent one) must fail loudly in testing rather than pass silently as
    'leave the link as written'."""
    app = _app()
    app.testing = True
    app.add_url_rule("/broken", endpoint="test.broken", view_func=lambda: "ok")

    with patch("app.release_waves.hook.configured_wave", return_value=1), patch(
        "app.release_waves.hook.entry_for", side_effect=KeyError("malformed wave map entry"),
    ):
        with app.app_context():
            with pytest.raises(KeyError):
                early_access_target("/broken")


def test_wrong_mapping_degrades_outside_testing():
    """The same wrong mapping, outside testing, logs and leaves the link as
    written rather than taking the whole page down over one bad link."""
    app = _app()
    app.testing = False
    app.add_url_rule("/broken", endpoint="test.broken", view_func=lambda: "ok")

    with patch("app.release_waves.hook.configured_wave", return_value=1), patch(
        "app.release_waves.hook.entry_for", side_effect=KeyError("malformed wave map entry"),
    ):
        with app.app_context():
            assert early_access_target("/broken") is None


def _moves_app(moves):
    """An app whose addresses move into each other: moves maps an address to where it moves."""
    app = _app()
    app.testing = True
    for source in moves:
        app.add_url_rule(source, endpoint=f"test.{source.strip('/')}", view_func=lambda: "ok")
    app.add_url_rule("/open-page", endpoint="test.open_page", view_func=lambda: "ok")
    app.add_url_rule("/early-access/<slug>", endpoint="early_access.page", view_func=lambda slug: "page" if slug == "pg-rt" else ("", 404))
    app.add_url_rule("/early-access/<slug>/join", endpoint="early_access.join", view_func=lambda slug: "ok", methods=["POST"])

    def entry(rule, endpoint=None):
        if rule in moves:
            return ["RT", "PG-RT", "page", moves[rule], "W1"]
        return ["W1", "PG-X", "page", None, None]

    return app, entry


def _patched(entry):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("app.release_waves.hook.configured_wave", return_value=1))
    stack.enter_context(patch("app.release_waves.hook.entry_for", side_effect=entry))
    stack.enter_context(patch("app.release_waves.hook.group_for", side_effect=lambda e: (e[1], {"title": "T"})))
    return stack


def test_link_to_a_moved_address_keeps_the_persons_search_term():
    """/capability-maturity/search?q=Pay: the link's q wins over the destination's, and its other keys stay."""
    from app.release_waves.hook import merge_query

    assert merge_query("/catalogues/?q=old&view=list", "?q=Pay") == "/catalogues/?view=list&q=Pay"
    assert merge_query("/catalogues/", "?q=Pay") == "/catalogues/?q=Pay"
    # a routing key on the destination survives a link carrying lens=x; the search term survives too
    assert merge_query("/capability-map/?lens=health", "?lens=x&q=Pay") == "/capability-map/?lens=health&q=Pay"
    assert merge_query("/catalogues/?view=list", "") == "/catalogues/?view=list"


def test_request_to_a_moved_address_keeps_the_query():
    """The request-time redirect carries ?q=Pay to the page the address moves to."""
    app, entry = _moves_app({"/capability-maturity/search": "/open-page"})
    with _patched(entry), app.test_request_context("/capability-maturity/search?q=Pay"):
        from app.release_waves.hook import enforce

        reply = enforce()
        assert reply.status_code == 302
        assert reply.headers["Location"].endswith("/open-page?q=Pay")


def test_moves_that_lead_back_end_on_a_page_that_opens_in_the_link_rewrite_and_the_request():
    """A moves to B and B moves to A: the link and the request both end on a page, never loop."""
    from app.release_waves.hook import early_access_target, enforce

    app, entry = _moves_app({"/a": "/b", "/b": "/a"})
    with _patched(entry), patch("app.release_waves.hook.state_for", side_effect=lambda e, w: "redirect" if e[0] == "RT" else "open"):
        with app.app_context():
            assert early_access_target("/a") == "/early-access/pg-rt"
        # the group is registered: the address the link points at answers 200
        assert app.test_client().get("/early-access/pg-rt").status_code == 200
        with app.test_request_context("/a"), patch("app.release_waves.hook.render_template", return_value="page"):
            reply = enforce()
            assert reply.status_code == 200 and reply.get_data(as_text=True) == "page"
