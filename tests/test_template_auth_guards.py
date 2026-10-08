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


def test_admin_required_actually_denies_a_non_admin(app):
    """The same bug in decorator form: getattr on a method is always truthy.

    app/utils/decorators.py resolved `is_admin` with a bare getattr, so the
    decorator never rejected anyone. It no longer guards any route in this
    codebase (adm_kanban_view.init_phases, its last caller, was moved onto
    the canonical app.decorators.admin_required in the admin-rbac systemic
    fix, since this module's own decorator never checked the session's
    active organisation either) -- this test exercises the decorator
    function directly to prove it still behaves correctly in isolation,
    regardless of whether anything currently imports it.
    """
    from werkzeug.exceptions import Forbidden

    from app.utils.decorators import admin_required

    @admin_required
    def protected():
        return "reached"

    class NotAnAdmin:
        is_authenticated = True

        def is_admin(self):
            return False

    class AnAdmin:
        is_authenticated = True

        def is_admin(self):
            return True

    import app.utils.decorators as decorators_module

    original = decorators_module.current_user
    try:
        decorators_module.current_user = NotAnAdmin()
        with pytest.raises(Forbidden):
            protected()

        decorators_module.current_user = AnAdmin()
        assert protected() == "reached", "a real admin must still get through"
    finally:
        decorators_module.current_user = original
