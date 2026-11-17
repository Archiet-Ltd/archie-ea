"""The one place every model module is loaded.

A relationship names its target class by string ("RoadmapResource"), and SQLAlchemy resolves the name when
the mappers are first configured, which any query triggers. A process that queries before the model modules
are all imported (the background job worker begins while the application is still being assembled) fails
there, and a failed configuration stays failed. Calling :func:`import_all_models` before such a query makes
the set complete whatever order the rest of the application loads in. It is idempotent.
"""

from __future__ import annotations

import importlib
import pkgutil

import app.models as _models

# A module that defines tables another module already defines; nothing imports it.
_NOT_MODELS = frozenset({"app.models.tables"})


def import_all_models() -> None:
    for module in pkgutil.walk_packages(_models.__path__, _models.__name__ + "."):
        if module.name not in _NOT_MODELS:
            importlib.import_module(module.name)
