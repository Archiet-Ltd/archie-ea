"""Reuse the shared fixtures from tests/conftest.py for this module's tests.

pytest's conftest.py discovery walks a test file's own directory ancestry, and
``app/modules/reference_packs/tests/`` is not a descendant of ``tests/`` — so
the shared ``app``, ``db_session``, ``make_org``, ``tenant_ctx`` fixtures are
not automatically visible here.  The supported way to reuse them without
duplicating the fixture bodies is a plain import: pytest discovers a fixture
by the name bound in a conftest.py's namespace, whether or not it is defined
there.
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