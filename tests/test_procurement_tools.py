"""Procurement persona AI-chat tools (register gap G5).

Two service-backed capabilities that existed but were unreachable from the
conversational agent are now wrapped as tools:

* ``create_vendor`` — WRITE, mutates=True, tier 'approve'. Wraps
  ``AIDataInteractionService.create_vendor`` (creates a ``VendorOrganization``).
* ``extract_contract_from_document`` — EXTRACT (calls external LLM service),
  mutates=True, tier 'approve'. Wraps ``contract_extraction_service.extract_contract_terms``
  (LLM text->JSON). Classified as external_action and requires confirmation.

DEVIATION pinned here as fact, not oversight: ``VendorOrganization`` is
DELIBERATELY not tenant-scoped (ADR-0003 — shared reference data with a globally
unique ``name``; see app/models/vendor/vendor_organization.py and
tests/test_vendor_tenancy_policy.py). The brief asked for a per-org-invisible
vendor; that is architecturally impossible for this model and would break the
unique-name constraint. So the isolation assertion below pins the REAL policy:
a vendor created in one org's context carries no organization_id and is visible
globally, and the write cannot leak *because there is no tenant column to leak*.
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.ai_chat.tools.executor import ToolExecutor
from app.modules.ai_chat.tools.registry import TOOL_SCHEMA_BY_NAME

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org_id):
    from app.models.user import User

    user = User(email=f"proc-{uuid.uuid4().hex[:8]}@example.com", organization_id=org_id)
    db_session.add(user)
    db_session.flush()
    return user


# --------------------------------------------------------------- registration


def test_both_tools_registered_with_correct_flags():
    create = TOOL_SCHEMA_BY_NAME.get("create_vendor")
    extract = TOOL_SCHEMA_BY_NAME.get("extract_contract_from_document")

    assert create is not None, "create_vendor is not registered"
    assert create["mutates"] is True
    assert create["tier"] == "approve"

    assert extract is not None, "extract_contract_from_document is not registered"
    # The tool calls an external service, so it needs confirmation.
    assert extract["mutates"] is True
    assert extract["tier"] == "approve"


def test_tools_dispatch_via_getattr():
    """The execute() dispatcher resolves a tool by getattr(self, '_tool_'+name)."""
    ex = ToolExecutor(user_id=1)
    assert callable(getattr(ex, "_tool_create_vendor", None))
    assert callable(getattr(ex, "_tool_extract_contract_from_document", None))


# --------------------------------------------------------------- create_vendor


def test_create_vendor_creates_a_vendor_row(db_session, make_org, tenant_ctx):
    from app.models.vendor.vendor_organization import VendorOrganization

    org = make_org("proc")
    user = _make_user(db_session, org.id)
    name = f"ACME Data {uuid.uuid4().hex[:8]}"

    with tenant_ctx(org.id):
        ex = ToolExecutor(user_id=user.id)
        result = ex._tool_create_vendor(
            {"name": name, "vendor_type": "software_vendor", "website": "https://acme.example"}
        )

    assert result["success"] is True, result
    vendor_id = result["result"]["id"]
    assert vendor_id is not None

    vendor = VendorOrganization.query.filter_by(id=vendor_id).first()
    assert vendor is not None
    assert vendor.name == name
    assert vendor.website == "https://acme.example"


def test_create_vendor_is_shared_reference_data_not_tenant_scoped(
    db_session, make_org, tenant_ctx
):
    """Pins the ADR-0003 policy: the vendor has no organization_id and is global.

    This is the honest form of the brief's isolation requirement. The write
    cannot cross orgs because VendorOrganization carries no tenant column, and
    the row created in org A's context is visible from org B's context.
    """
    from app.models.vendor.vendor_organization import VendorOrganization

    org_a, org_b = make_org("a"), make_org("b")
    user_a = _make_user(db_session, org_a.id)
    name = f"Shared Vendor {uuid.uuid4().hex[:8]}"

    with tenant_ctx(org_a.id):
        ex = ToolExecutor(user_id=user_a.id)
        result = ex._tool_create_vendor({"name": name})
    assert result["success"] is True, result
    vendor_id = result["result"]["id"]

    # No tenant column exists on the model at all — the write cannot be org-scoped.
    assert not hasattr(VendorOrganization, "organization_id")

    # Visible from a different org's context — shared reference data by design.
    with tenant_ctx(org_b.id):
        seen = VendorOrganization.query.filter_by(id=vendor_id).first()
    assert seen is not None
    assert seen.name == name


def test_create_vendor_requires_a_name(db_session, make_org, tenant_ctx):
    org = make_org("proc")
    user = _make_user(db_session, org.id)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user_id=user.id)
        result = ex._tool_create_vendor({"name": "   "})
    assert result["success"] is False
    assert "name is required" in result["error"].lower()


# ------------------------------------------------- extract_contract_from_document


def test_extract_contract_returns_structured_fields(db_session, make_org, tenant_ctx, monkeypatch):
    """Happy path with the LLM mocked — no paid call. Fields the text states are
    populated; fields it does not are None (never guessed)."""
    from app.modules.procurement import contract_extraction_service as svc

    fake_json = (
        '{"contract_name": "Master Services Agreement", "vendor_name": "ACME Corp", '
        '"notice_period_days": 90, "auto_renewal": true, "contract_value": 120000, '
        '"currency": "USD", "start_date": "2026-01-01", "end_date": "2027-01-01"}'
    )
    monkeypatch.setattr(
        svc.LLMService, "generate_from_prompt", staticmethod(lambda *a, **k: fake_json)
    )

    org = make_org("proc")
    user = _make_user(db_session, org.id)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user_id=user.id)
        result = ex._tool_extract_contract_from_document(
            {"contract_text": "This MSA between ACME Corp ... 90 days notice ..."}
        )

    assert result["success"] is True, result
    fields = result["result"]
    assert fields["vendor_name"] == "ACME Corp"
    assert fields["notice_period_days"] == 90
    assert fields["auto_renewal"] is True
    assert fields["contract_value"] == 120000.0
    # A field the text does not state comes back None, not fabricated.
    assert fields["liability_cap"] is None


def test_extract_contract_surfaces_llm_failure_honestly(
    db_session, make_org, tenant_ctx, monkeypatch
):
    """If the LLM backend is absent/failing, the tool fails honestly — it does
    NOT return a fabricated extraction."""
    from app.modules.procurement import contract_extraction_service as svc

    def _boom(*a, **k):
        raise RuntimeError("no API key configured")

    monkeypatch.setattr(svc.LLMService, "generate_from_prompt", staticmethod(_boom))

    org = make_org("proc")
    user = _make_user(db_session, org.id)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user_id=user.id)
        result = ex._tool_extract_contract_from_document(
            {"contract_text": "Some contract text here."}
        )

    assert result["success"] is False
    assert "result" not in result
    assert "no API key configured" in result["error"]


def test_extract_contract_requires_text(db_session, make_org, tenant_ctx):
    org = make_org("proc")
    user = _make_user(db_session, org.id)
    with tenant_ctx(org.id):
        ex = ToolExecutor(user_id=user.id)
        result = ex._tool_extract_contract_from_document({"contract_text": "  "})
    assert result["success"] is False
    assert "required" in result["error"].lower()
