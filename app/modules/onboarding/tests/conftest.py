"""Reuse the shared fixtures from tests/conftest.py for this module's tests.

See app/modules/intelligence/tests/conftest.py for why this import (not
pytest_plugins) is the supported way to make them visible here.
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
