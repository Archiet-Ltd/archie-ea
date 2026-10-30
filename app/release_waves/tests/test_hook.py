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
