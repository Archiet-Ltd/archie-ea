"""Reuse the shared fixtures from tests/conftest.py for every module's tests.

pytest's conftest.py discovery walks a test file's own directory ancestry, and
``app/modules/<module>/tests/`` is not a descendant of ``tests/``, so the
shared ``app`` / ``db_session`` / ``make_org`` / ``tenant_ctx`` / ``login_as``
fixtures are not automatically visible there. ``pytest_plugins`` is restricted
to the rootdir conftest.py (there is none in this repo), so the supported way
to reuse them without duplicating the fixture bodies is a plain import: pytest
discovers a fixture by the name bound in a conftest.py's namespace, whether or
not it is defined there.

One bridge for every module: pytest.ini collects each ``app/modules/*/tests``
directory, and a module's tests find these fixtures without a copy of this
file of their own.
"""

from __future__ import annotations

from tests.conftest import (  # noqa: F401  (imported for pytest fixture discovery)
    _schema,
    app,
    client,
    db_session,
    login_as,
    make_org,
    tenant_ctx,
)
