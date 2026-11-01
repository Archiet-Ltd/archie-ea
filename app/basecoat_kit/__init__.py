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


def init_basecoat(app):
    """Expose the helpers the component macros call."""
    app.jinja_env.globals.setdefault('slot', _slot)
    app.jinja_env.globals.setdefault('tw', tw)
    app.jinja_env.globals.setdefault('has_lucide', has_lucide)
    if 'lucide' not in app.jinja_env.globals:
        app.jinja_env.globals['lucide'] = lucide
