"""The flask-basecoat component helpers the Overview dashboard is built from.

Vendored from flask-basecoat, branch feat/shadcn-100-compliance, commit
b327186c2e2f745310005b18e92a990a9b77f240. The component macros live in
``app/templates/basecoat/``; the script that draws charts and runs the tabs and
selects is ``app/static/vendor/basecoat/flask-basecoat.js``.

Local change to the vendored copy: in ``data/recipes.json`` the attachment-media image
variant is written ``[img]:*:aspect-square``, ``[img]:*:w-full`` and ``[img]:*:object-cover``
where the library wrote ``*:[img]:...``. Tailwind 3 applies a class's variants from the last
to the first, so only the first order matches an image among the children; the library's
order matches the children of an image and never matches. A re-vendor must keep the local
order (``tests/test_basecoat_kit_vendoring.py`` fails if it does not).
"""
from .icons import ICONS as _ICONS, svg as _lucide_svg
from .slots import slot as _slot
from .twmerge import merge as _merge


def tw(*classes):
    """Merge class strings so that later classes win."""
    return _merge(classes)


def lucide(name, class_name='', **attrs):
    """Render a Lucide icon."""
    return _lucide_svg(name, class_name, **attrs)


def has_lucide(name):
    """True when lucide() can draw this icon on the server."""
    return name in _ICONS


def register_basecoat_helpers(env):
    """Register the four basecoat helpers on a Jinja environment.

    This is the single place that knows which helpers the basecoat component
    macros require. Both the Flask app (via init_basecoat) and test harnesses
    call this function so the helpers are available in every environment that
    renders basecoat templates.
    """
    env.globals.setdefault('slot', _slot)
    env.globals.setdefault('tw', tw)
    env.globals.setdefault('has_lucide', has_lucide)
    if 'lucide' not in env.globals:
        env.globals['lucide'] = lucide


def init_basecoat(app):
    """Expose the helpers the component macros call."""
    register_basecoat_helpers(app.jinja_env)
