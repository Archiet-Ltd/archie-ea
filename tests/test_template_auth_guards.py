"""A Jinja guard that calls a User *method* without parentheses always passes.

`User.is_admin` is a method, not a property (app/models/user.py). In Jinja,
`{% if current_user.is_admin %}` therefore evaluates the bound method object,
which is truthy for every user including anonymous ones. The guard reads exactly
like a working authorisation check and enforces nothing.

Two live instances were found and fixed:

    components/admin_sidebar_northstar_phase2.html  the Administration nav block -
        Users, API keys, Seed Management, Platform Settings - rendered for every
        authenticated user of every role, on all 293 templates extending
        layouts/admin_base.html
    errors/custom_error.html                        an "Admin Dashboard" link
        offered to everyone who hits an error page

Both were single missing parens. Nothing in the tree catches that class, so this
test derives the risky names by introspection rather than hardcoding them: any
plain method on User (or AnonymousUser) is a name that must never appear bare in
a template guard. A method added later is covered without touching this file.

Properties are deliberately excluded - `current_user.is_authenticated` is a
Flask-Login property and correct without parentheses.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


def _callable_method_names():
    """Names on User/AnonymousUser that are plain methods, not properties."""
    from app.models.user import AnonymousUser, User

    names = set()
    for cls in (User, AnonymousUser):
        for attr in dir(cls):
            if attr.startswith("__"):
                continue
            # A property must be read from the class, not an instance, or it
            # would execute against a detached model.
            if isinstance(getattr(cls, attr, None), property):
                continue
            if callable(getattr(cls, attr, None)):
                names.add(attr)
    # Only guard-shaped predicates matter; a template calling e.g.
    # current_user.query is a different (and already broken) mistake.
    return {n for n in names if n.startswith(("is_", "can", "has_"))}


@pytest.fixture(scope="module")
def app():
    """Importing the models needs the package importable, not a database."""
    import os

    os.environ.setdefault("FLASK_CONFIG", "testing")
    os.environ.setdefault("SECRET_KEY", "test-only-not-secret")
    from app import create_app

    return create_app("testing")


def test_no_template_guards_on_an_uncalled_user_method(app):
    with app.app_context():
        risky = _callable_method_names()

    assert "is_admin" in risky, (
        "introspection did not find User.is_admin as a method - if it became a "
        "property this test needs rewriting, not deleting"
    )

    # `current_user.name` NOT followed by an opening paren.
    pattern = re.compile(
        r"current_user\.(" + "|".join(sorted(re.escape(n) for n in risky)) + r")\s*(?!\()"
    )

    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in pattern.finditer(line):
                # Only a guard position matters: inside {% if %} / {{ }}.
                if "{%" in line or "{{" in line:
                    offenders.append(
                        "%s:%d  current_user.%s"
                        % (path.relative_to(TEMPLATES.parents[1]), lineno, match.group(1))
                    )

    assert not offenders, (
        "%d template guard(s) reference a User method without calling it. A bound "
        "method is always truthy, so each of these authorises everyone:\n  %s\n\n"
        "Add the parentheses: {%% if current_user.is_admin() %%}."
        % (len(offenders), "\n  ".join(offenders))
    )


def test_second_admin_required_implementation_is_gone():
    """R2-4 (PR 428 round 3): D-5 consolidated every route onto the one
    canonical ``app.decorators.admin_required`` (``app._decorators_base``),
    but left a second, unrelated ``admin_required`` defined in
    ``app/utils/decorators.py`` -- "admin anywhere" logic (a bare
    ``is_admin``/``is_superuser``/``role == "admin"`` check) with no
    active-org check, reachable by anyone who typed
    ``from app.utils.decorators import admin_required``. Nothing imported it
    (confirmed before deleting -- its last route caller,
    ``adm_kanban_view.init_phases``, already used the canonical
    ``app.decorators.admin_required``), so it was deleted outright rather
    than fixed in place: one implementation, not two.

    This replaces the previous version of this test, which exercised that
    module's decorator directly with a duck-typed user. Coverage for "does
    the canonical admin_required actually deny a non-admin in the active
    organisation" already lives in
    test_admin_rbac_active_org_enforcement.py's url_map-wide sweep
    (test_a_viewer_of_the_active_org_is_refused_by_every_admin_required_route)
    and its anonymous-request test -- both exercise the one real decorator
    rather than a stand-in, so nothing is lost by not re-deriving it here.
    """
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.utils.decorators")
