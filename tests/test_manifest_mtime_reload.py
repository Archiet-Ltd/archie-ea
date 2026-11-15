"""Tests for mtime-based manifest reload outside production.

The manifest (app/static/manifest.json) maps logical filenames to
content-hashed filenames.  Outside production the cached manifest is
reloaded when its mtime changes, checked at most once a second, so a
stylesheet rebuild never leaves a running preview linking a file that
no longer exists.  In production the manifest is read once at start-up.
"""

import json
import os
import time

import pytest
from flask import Flask

from app._bootstrap.assets import (
    _load_manifest,
    _manifest_cache,
    _manifest_last_check,
    _manifest_mtime,
    _maybe_reload_manifest,
    init_assets,
)


@pytest.fixture(autouse=True)
def _reset_manifest_state():
    """Reset module-level manifest state before each test so rate-limit
    timestamps and cached mtimes from one test do not affect the next."""
    import app._bootstrap.assets as assets_mod

    assets_mod._manifest_cache = {}
    assets_mod._manifest_mtime = 0.0
    assets_mod._manifest_last_check = 0.0


@pytest.fixture
def app(tmp_path):
    static_dir = tmp_path / "static" / "css"
    static_dir.mkdir(parents=True)
    (static_dir / "tailwind-output.css").write_text("body{color:red}")

    application = Flask(__name__, static_folder=str(tmp_path / "static"))
    init_assets(application)
    return application


@pytest.fixture
def testing_app(tmp_path):
    static_dir = tmp_path / "static" / "css"
    static_dir.mkdir(parents=True)
    (static_dir / "tailwind-output.css").write_text("body{color:red}")

    application = Flask(__name__, static_folder=str(tmp_path / "static"))
    application.config["ASSET_MANIFEST_RELOAD"] = True
    init_assets(application)
    return application


def _asset_url(application, filename):
    with application.test_request_context():
        return application.jinja_env.filters["asset_url"](filename)


def _write_manifest(app, mapping):
    """Write a manifest.json and bump its mtime so the reload detects it."""
    manifest_path = os.path.join(app.static_folder, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f)
    # Bump mtime deterministically past the rate-limit window.
    # Use time.time() to ensure the mtime actually changes even if
    # two writes happen within the same filesystem timestamp granularity.
    os.utime(manifest_path, (time.time(), time.time() + 10))


def _force_reload(app):
    """Call _maybe_reload_manifest after clearing the rate-limit guard.

    Resets the module-level ``_manifest_last_check`` to 0.0 so the
    once-per-second rate limit does not block the reload, then calls
    ``_maybe_reload_manifest``.  This mirrors what the before_request
    handler does in a running process.
    """
    import app._bootstrap.assets as assets_mod

    assets_mod._manifest_last_check = 0.0
    _maybe_reload_manifest(app)


# ── testing config: manifest reloads when mtime changes ────────────────────


def test_testing_manifest_reloads_when_new_manifest_is_written(testing_app):
    """Writing a new manifest changes the asset URL on the next request."""
    # Start with no manifest — asset_url falls back to mtime version.
    before = _asset_url(testing_app, "css/tailwind-output.css")
    assert "?v=" in before

    # Write a manifest that maps to a hashed filename.
    _write_manifest(
        testing_app,
        {"css/tailwind-output.css": "css/tailwind-output.b257857d.css"},
    )

    # Simulate the before_request handler firing.
    _force_reload(testing_app)

    after = _asset_url(testing_app, "css/tailwind-output.css")
    assert after == "/static/css/tailwind-output.b257857d.css"
    assert after != before


def test_testing_manifest_reloads_when_manifest_changes(testing_app):
    """Changing an existing manifest updates the asset URL."""
    # Seed with an initial manifest.
    _write_manifest(
        testing_app,
        {"css/tailwind-output.css": "css/tailwind-output.aaaaaaaa.css"},
    )
    # Force a load so the cache has the initial entry.
    _load_manifest(testing_app)
    before = _asset_url(testing_app, "css/tailwind-output.css")
    assert before == "/static/css/tailwind-output.aaaaaaaa.css"

    # Write a new manifest with a different hash.
    _write_manifest(
        testing_app,
        {"css/tailwind-output.css": "css/tailwind-output.bbbbbbbb.css"},
    )

    # Simulate the before_request handler firing.
    _force_reload(testing_app)

    after = _asset_url(testing_app, "css/tailwind-output.css")
    assert after == "/static/css/tailwind-output.bbbbbbbb.css"
    assert after != before


def test_testing_manifest_reloads_when_manifest_is_deleted(testing_app):
    """Deleting the manifest falls back to the mtime version."""
    # Seed with a manifest.
    _write_manifest(
        testing_app,
        {"css/tailwind-output.css": "css/tailwind-output.aaaaaaaa.css"},
    )
    _load_manifest(testing_app)
    before = _asset_url(testing_app, "css/tailwind-output.css")
    assert "aaaaaaaa" in before

    # Delete the manifest and bump its former mtime so the reload triggers.
    manifest_path = os.path.join(testing_app.static_folder, "manifest.json")
    os.remove(manifest_path)

    # Simulate the before_request handler firing.
    _force_reload(testing_app)

    after = _asset_url(testing_app, "css/tailwind-output.css")
    # Should fall back to the mtime-based ?v= URL.
    assert "?v=" in after
    assert after != before


# ── production mode: manifest does NOT reload ──────────────────────────────


def test_production_manifest_does_not_reload_when_new_manifest_is_written(app):
    """In production a new manifest is ignored — the startup copy stays."""
    app.config["ASSET_MANIFEST_RELOAD"] = False
    before = _asset_url(app, "css/tailwind-output.css")
    # Production uses the build-id version, not per-file mtime.
    assert "?v=" in before

    _write_manifest(
        app,
        {"css/tailwind-output.css": "css/tailwind-output.b257857d.css"},
    )

    after = _asset_url(app, "css/tailwind-output.css")
    # Still the build-id version, not the hashed filename.
    assert after == before
    assert "b257857d" not in after


def test_production_manifest_does_not_reload_when_manifest_changes(app):
    """In production a changed manifest is ignored."""
    app.config["ASSET_MANIFEST_RELOAD"] = False
    # Seed with an initial manifest loaded at startup.
    _write_manifest(
        app,
        {"css/tailwind-output.css": "css/tailwind-output.aaaaaaaa.css"},
    )
    _load_manifest(app)
    before = _asset_url(app, "css/tailwind-output.css")
    assert before == "/static/css/tailwind-output.aaaaaaaa.css"

    # Write a new manifest.
    _write_manifest(
        app,
        {"css/tailwind-output.css": "css/tailwind-output.bbbbbbbb.css"},
    )

    after = _asset_url(app, "css/tailwind-output.css")
    # Still the old hash — production never reloads.
    assert after == before
    assert after == "/static/css/tailwind-output.aaaaaaaa.css"


# ── rate limit: mtime checked at most once per second ──────────────────────


def test_manifest_reload_is_rate_limited(testing_app, monkeypatch):
    """Two rapid requests do not re-stat the manifest file."""
    # Seed with a manifest.
    _write_manifest(
        testing_app,
        {"css/tailwind-output.css": "css/tailwind-output.aaaaaaaa.css"},
    )
    _load_manifest(testing_app)

    call_count = 0
    _real_getmtime = os.path.getmtime

    def _counting_getmtime(path):
        nonlocal call_count
        call_count += 1
        return _real_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", _counting_getmtime)

    # First call goes through.
    _maybe_reload_manifest(testing_app)
    first_count = call_count

    # Second call within the same second is rate-limited.
    _maybe_reload_manifest(testing_app)
    # call_count should not have increased (the rate-limit guard returns early).
    assert call_count == first_count


def test_manifest_reload_fires_after_rate_limit_window(testing_app, monkeypatch):
    """After the 1-second window the mtime is checked again."""
    _write_manifest(
        testing_app,
        {"css/tailwind-output.css": "css/tailwind-output.aaaaaaaa.css"},
    )
    _load_manifest(testing_app)

    # Force the last-check timestamp into the past.
    import app._bootstrap.assets as assets_mod

    monkeypatch.setattr(assets_mod, "_manifest_last_check", time.time() - 2.0)

    call_count = 0
    _real_getmtime = os.path.getmtime

    def _counting_getmtime(path):
        nonlocal call_count
        call_count += 1
        return _real_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", _counting_getmtime)

    _maybe_reload_manifest(testing_app)
    # The rate-limit window has passed, so getmtime should have been called.
    assert call_count >= 1