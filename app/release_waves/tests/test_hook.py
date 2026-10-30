"""Tests for the release wave hook."""

from unittest.mock import MagicMock, patch

from flask import Flask

from app.release_waves.hook import early_access_target, point_closed_links_at_early_access


def test_cache_size_limit():
    """The link-rewriting cache is bounded."""
    app = Flask(__name__)
    app.config["SERVER_NAME"] = "localhost"
    app.url_map.add(MagicMock(endpoint="test.endpoint", rule="/<path:path>"))

    with patch("app.release_waves.hook.configured_wave", return_value=1), patch(
        "app.release_waves.hook.entry_for",
        return_value=["/fake", "fake.endpoint", 1, None],
    ), patch("app.release_waves.hook.state_for", return_value="CLOSED"), patch(
        "app.release_waves.hook.group_for",
        return_value=("group1", {"title": "Group 1"}),
    ):
        with app.app_context():
            for i in range(10000):
                # The hook is called after each request
                early_access_target(f"/path/{i}")

            cache = app.extensions.get("release_waves_link_targets", {})
            # This will fail initially as the cache is unbounded
            assert len(cache) < 5000
