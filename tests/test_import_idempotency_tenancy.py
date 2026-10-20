"""The import duplicate check is per organisation.

The same file imported into a second organisation inside the duplicate window is a
new import, not a repeat of the first organisation's.
"""
from __future__ import annotations

import pytest

CONTENT = b"name,owner\nPayroll,HR\n"


@pytest.fixture(autouse=True)
def _clean():
    from app.modules.import_batch.services import import_orchestrator as mod

    mod._import_idempotency_cache.clear()
    yield
    mod._import_idempotency_cache.clear()


def test_same_file_into_another_organisation_is_not_a_duplicate(tenant_ctx, make_org):
    from app.modules.import_batch.services.import_orchestrator import (
        check_import_idempotency,
        store_import_idempotency,
    )

    org_a, org_b = make_org("ida"), make_org("idb")
    with tenant_ctx(org_a.id):
        store_import_idempotency(CONTENT, 7, {"imported": 1})
        assert check_import_idempotency(CONTENT, 7) == {"imported": 1}
    with tenant_ctx(org_b.id):
        assert check_import_idempotency(CONTENT, 7) is None


def test_identifiers_cannot_be_shifted_into_the_file_content(tenant_ctx, make_org):
    from app.modules.import_batch.services.import_orchestrator import _make_idempotency_key

    org = make_org("idc")
    with tenant_ctx(org.id):
        assert _make_idempotency_key(b"1" + CONTENT, 2) != _make_idempotency_key(CONTENT, 21)
