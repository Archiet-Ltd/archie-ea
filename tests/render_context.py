"""What the app's context processors and Flask-Login give every template, for tests that render a real
template in a bare Jinja environment (no database, no request).

One place, so a template that starts using current_user or link_requires_satisfied does not break each
harness one at a time. The user is a signed-in administrator without the general permission, so the
chat page carries no approvals panel and its queue poll, which a bare loopback server cannot answer.
"""
from types import SimpleNamespace


def app_template_context():
    from app.utils.role_access import link_requires_satisfied

    user = SimpleNamespace(
        is_admin=lambda: True,
        can=lambda permission: False,
        is_authenticated=True,
        is_org_admin=True,
    )
    return {"current_user": user, "link_requires_satisfied": link_requires_satisfied}


def basecoat_globals():
    """The component helpers the app registers on its Jinja environment (``init_basecoat``). A bare
    environment that renders a template using a component macro needs them, or the macro fails with
    "'slot' is undefined"."""
    from types import SimpleNamespace

    from app.basecoat_kit import init_basecoat

    registered = {}
    init_basecoat(SimpleNamespace(jinja_env=SimpleNamespace(globals=registered)))
    return registered
