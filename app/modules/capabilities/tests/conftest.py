"""Reuse the shared fixtures from tests/conftest.py for this module's tests.

pytest's conftest.py discovery walks a test file's own directory ancestry, and
``app/modules/capabilities/tests/`` is not a descendant of ``tests/`` — so the
shared ``app`` / ``db_session`` / ``make_org`` / ``tenant_ctx`` fixtures
CLAUDE.md's testing conventions point new tests at are not automatically
visible here. ``pytest_plugins`` is restricted to the rootdir conftest.py
(there is none in this repo), so the supported way to reuse them without
duplicating the fixture bodies is a plain import: pytest discovers a fixture
by the name bound in a conftest.py's namespace, whether or not it is defined
there. Mirrors ``app/modules/intelligence/tests/conftest.py``, the one
existing instance of this bridge.
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
