"""Regression tests for CM-01, O-02, O-03 (fix/qa-register-100 wave).

CM-01: the capability domain (BusinessCapability, APQCProcess) was empty
because the reference taxonomy was never loaded, and the AI system prompt
hardcoded fabricated counts ("516 business capabilities", "720 ArchiMate
elements") instead of querying the database.

O-02: OEF export omitted <views>, <organizations> and
<propertyDefinitions>/<properties> entirely.

O-03: OEF import silently accepted a malformed/missing-file POST and
returned an HTTP 200 HTML page indistinguishable from success.

Uses the shared fixtures in tests/conftest.py (db_session rolls back, so
these cannot leave residue in the shared test database).
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# CM-01: reference taxonomy loading
# ---------------------------------------------------------------------------


def _clean_apqc_process(db_session):
    """The APQCProcess table has no TenantMixin scoping and seed_apqc_processes()
    commits directly, so — unlike TenantMixin-scoped rows — leftover rows from a
    prior run of this same module can be visible to a later test in the shared
    test database. Defensive cleanup, not a workaround for a real product bug."""
    from app.models.apqc_process import APQCProcess

    APQCProcess.query.delete()
    db_session.commit()


def test_apqc_process_table_is_empty_before_seeding(db_session):
    """Pin the defect: without seeding, APQCProcess — what /api/apqc/tree
    actually reads from (app.services.apqc_hierarchy_service.APQCHierarchyService)
    — has no rows. This is the "reference taxonomy never loaded" defect."""
    from app.models.apqc_process import APQCProcess

    _clean_apqc_process(db_session)
    assert APQCProcess.query.count() == 0


def test_seed_apqc_processes_loads_the_reference_taxonomy(db_session):
    """flask seed-capabilities apqc (app/commands/seed_capabilities.py::seed_apqc_processes)
    populates APQCProcess — the model /api/apqc/tree actually queries — fixing
    the "reference taxonomy never loaded" half of CM-01."""
    from app.commands.seed_capabilities import seed_apqc_processes, APQC_PROCESS_SEED_DATA
    from app.models.apqc_process import APQCProcess

    _clean_apqc_process(db_session)
    result = seed_apqc_processes()

    assert result["created"] == len(APQC_PROCESS_SEED_DATA)
    assert APQCProcess.query.count() == len(APQC_PROCESS_SEED_DATA)

    # L1 categories have no parent; L2/L3 are wired to their parent.
    root = APQCProcess.query.filter_by(process_code="1.0").first()
    assert root is not None
    assert root.parent_process_id is None

    child = APQCProcess.query.filter_by(process_code="1.1").first()
    assert child is not None
    assert child.parent_process_id == root.id


def test_seed_apqc_processes_is_idempotent(db_session):
    from app.commands.seed_capabilities import seed_apqc_processes, APQC_PROCESS_SEED_DATA

    _clean_apqc_process(db_session)
    first = seed_apqc_processes()
    second = seed_apqc_processes()

    assert first["created"] == len(APQC_PROCESS_SEED_DATA)
    assert second["created"] == 0
    assert second["skipped"] == len(APQC_PROCESS_SEED_DATA)


def test_capability_architect_prompt_has_no_hardcoded_fabricated_counts(app):
    """CM-01 / ARCH-015: the system prompt used to hardcode "516 business
    capabilities", "720 ArchiMate elements", "881 applications" as fixed
    prose that drifted from the real database the moment it changed, and
    never said whether a number came from customer data or a seeded
    reference framework. build_capability_architect_prompt() must query
    live counts instead of stating a memorized number."""
    from app.modules.ai_chat.services.capability_architect_prompts import (
        build_capability_architect_prompt,
        CAPABILITY_ARCHITECT_SYSTEM_PROMPT,
    )

    # The raw template must not contain the old fabricated literals.
    assert "516" not in CAPABILITY_ARCHITECT_SYSTEM_PROMPT
    assert "720 ArchiMate" not in CAPABILITY_ARCHITECT_SYSTEM_PROMPT
    assert "881 apps" not in CAPABILITY_ARCHITECT_SYSTEM_PROMPT

    with app.app_context():
        prompt = build_capability_architect_prompt()

    assert "{platform_data_block}" not in prompt  # placeholder was substituted
    assert "Platform Data Available" in prompt
    assert "never state a memorized or approximate count" in prompt


def test_capability_prompt_labels_seeded_reference_data_when_empty(app, db_session):
    """With no BusinessCapability rows and no APQCProcess rows, the block must
    say the taxonomy has not been loaded rather than inventing a plausible
    number (never-invent-data rule in CLAUDE.md)."""
    from app.modules.ai_chat.services.capability_architect_prompts import (
        build_capability_architect_prompt,
    )

    _clean_apqc_process(db_session)
    prompt = build_capability_architect_prompt()
    assert "reference taxonomy has not been loaded yet" in prompt


def test_apqc_context_helper_reports_real_count_not_fabricated_1000(app, db_session):
    """context_aware_ai_helper._load_apqc_context used to hardcode
    total_processes=1000 with a comment admitting "# Approximate" — exactly
    the fabricated-literal pattern CLAUDE.md's never-invent-data rule bans.
    It must report None (not a fake number) when nothing is loaded, and the
    real count once seeded."""
    from app.modules.ai_chat.services.context_aware_ai_helper import ContextAwareAIHelper
    from app.commands.seed_capabilities import seed_apqc_processes, APQC_PROCESS_SEED_DATA

    _clean_apqc_process(db_session)
    helper = ContextAwareAIHelper(user_id=1)
    ctx = helper._load_apqc_context()
    assert ctx["total_processes"] != 1000  # not the old fabricated constant
    assert ctx["total_processes"] == 0

    seed_apqc_processes()
    ctx_after = helper._load_apqc_context()
    assert ctx_after["total_processes"] == len(APQC_PROCESS_SEED_DATA)


# ---------------------------------------------------------------------------
# O-02: OEF export omitted <views>, <organizations>, <propertyDefinitions>
# ---------------------------------------------------------------------------


def test_oef_export_includes_organizations_folder_structure(app, db_session, make_org, tenant_ctx):
    from app.services.archimate_oef_service import ArchiMateOEFService
    from app.models.archimate_core import ArchiMateElement

    org = make_org("oef-export")
    with tenant_ctx(org.id):
        el = ArchiMateElement(name="Test App", type="ApplicationComponent", layer="Application")
        db_session.add(el)
        db_session.flush()

        xml_str, _errors = ArchiMateOEFService().export_model_validated()

    assert "<organizations>" in xml_str
    assert f'identifierRef="id-{el.id}"' in xml_str


def test_oef_export_includes_property_definitions_and_values(app, db_session, make_org, tenant_ctx):
    from app.services.archimate_oef_service import ArchiMateOEFService
    from app.models.archimate_core import ArchiMateElement

    org = make_org("oef-export-props")
    with tenant_ctx(org.id):
        el = ArchiMateElement(
            name="Priced App",
            type="ApplicationComponent",
            layer="Application",
            properties=json.dumps({"annual_cost": "50000", "owner": "Finance"}),
        )
        db_session.add(el)
        db_session.flush()

        xml_str, _errors = ArchiMateOEFService().export_model_validated()

    assert "<propertyDefinitions>" in xml_str
    assert "annual_cost" in xml_str
    assert "50000" in xml_str


def test_oef_export_includes_views_with_node_geometry(app, db_session, make_org, tenant_ctx):
    """A SavedDiagram (Composer save) referencing an exported element must
    produce a <views> section carrying real x/y geometry, not be silently
    dropped as before."""
    from app.services.archimate_oef_service import ArchiMateOEFService
    from app.models.archimate_core import ArchiMateElement, SavedDiagram, SavedDiagramElement

    org = make_org("oef-export-views")
    with tenant_ctx(org.id):
        el = ArchiMateElement(name="Positioned App", type="ApplicationComponent", layer="Application")
        db_session.add(el)
        db_session.flush()

        diagram = SavedDiagram(name="My View")
        db_session.add(diagram)
        db_session.flush()

        pos = SavedDiagramElement(diagram_id=diagram.id, element_id=el.id, position_x=120, position_y=340)
        db_session.add(pos)
        db_session.flush()

        xml_str, _errors = ArchiMateOEFService().export_model_validated()

    assert "<views>" in xml_str
    assert f'elementRef="id-{el.id}"' in xml_str
    assert 'x="120' in xml_str
    assert 'y="340' in xml_str


def test_oef_export_direction_validation_untouched(app, db_session, make_org, tenant_ctx):
    """Guardrail: O-01's relationship-direction validation (commit 6aaf0b5)
    must keep working after the O-02 additions — it is owned by another
    agent's work and this wave must not regress it.

    Seeds its own tenant's relationships rather than relying on rows other
    tests left in the shared database: one valid control and one stored
    backwards, which must be emitted reversed and reported."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.services.archimate_oef_service import ArchiMateOEFService

    org = make_org("oef-export-direction")
    with tenant_ctx(org.id):
        comp = ArchiMateElement(name="Direction Component", type="ApplicationComponent", layer="Application")
        svc = ArchiMateElement(name="Direction Service", type="ApplicationService", layer="Application")
        goal = ArchiMateElement(name="Direction Goal", type="Goal", layer="Motivation")
        requirement = ArchiMateElement(name="Direction Requirement", type="Requirement", layer="Motivation")
        db_session.add_all([comp, svc, goal, requirement])
        db_session.flush()

        valid = ArchiMateRelationship(type="realization", source_id=comp.id, target_id=svc.id)
        # A Requirement realises a Goal, never the reverse: stored backwards here.
        backwards = ArchiMateRelationship(type="realization", source_id=goal.id, target_id=requirement.id)
        db_session.add_all([valid, backwards])
        db_session.flush()

        # export_model_validated must still return the (xml, errors) tuple shape
        xml_str, errors = ArchiMateOEFService().export_model_validated()

    assert isinstance(xml_str, str)
    assert isinstance(errors, list)
    assert "<elements>" in xml_str
    assert "<relationships>" in xml_str
    assert (
        f'identifier="id-rel-{valid.id}" xsi:type="Realization" '
        f'source="id-{comp.id}" target="id-{svc.id}"'
    ) in xml_str
    assert (
        f'identifier="id-rel-{backwards.id}" xsi:type="Realization" '
        f'source="id-{requirement.id}" target="id-{goal.id}"'
    ) in xml_str
    assert any(f"id-rel-{backwards.id}" in e and "emitted reversed" in e for e in errors)
    assert not any(f"id-rel-{valid.id}" in e for e in errors)


# ---------------------------------------------------------------------------
# O-03: OEF import silently failing with HTTP 200 HTML
# ---------------------------------------------------------------------------


def _login_user(db_session, make_org):
    import uuid

    from app.models.user import User

    org = make_org("oef")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"qa100-oef-{suffix}@example.com",
        first_name="QA100",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_oef_import_missing_file_returns_4xx_json_not_html_200(app, db_session, login_as, make_org):
    """O-03: posting to /architecture/import/oef with no file used to
    flash() + redirect to the GET form — an HTTP 200 HTML page a JSON API
    caller cannot distinguish from success. A JSON-preferring caller must
    now get a 4xx with a JSON error body."""
    user = _login_user(db_session, make_org)
    client = app.test_client()
    login_as(client, user)

    resp = client.post(
        "/architecture/import/oef",
        data={},
        headers={"Accept": "application/json"},
    )

    assert resp.status_code == 400
    assert resp.content_type.startswith("application/json")
    body = resp.get_json()
    assert body["success"] is False
    assert body["errors"]


def test_oef_import_reports_success_false_with_json_and_4xx_on_bad_xml(app, db_session, login_as, make_org):
    """A syntactically-invalid XML upload must not silently report success —
    it must come back as JSON with success: False and a non-2xx status."""
    import io

    user = _login_user(db_session, make_org)
    client = app.test_client()
    login_as(client, user)

    resp = client.post(
        "/architecture/import/oef",
        data={"oef_file": (io.BytesIO(b"not xml at all <<<"), "bad.xml")},
        headers={"Accept": "application/json"},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    assert body["errors"]


def test_oef_import_success_reports_counts_as_json(app, db_session, login_as, make_org):
    """A well-formed OEF file must come back with real created/skipped
    counts, not a silent no-op — the original O-03 symptom (145 elements
    before and after, delta 0, HTTP 200)."""
    import io

    user = _login_user(db_session, make_org)
    client = app.test_client()
    login_as(client, user)

    xml_content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-test-model">'
        '<name xml:lang="en">QA100 Import Test</name>'
        '<elements>'
        '<element identifier="id-qa100-1" xsi:type="ApplicationComponent">'
        '<name xml:lang="en">QA100 Test App</name>'
        "</element>"
        "</elements>"
        "<relationships/>"
        "</model>"
    )

    resp = client.post(
        "/architecture/import/oef",
        data={"oef_file": (io.BytesIO(xml_content.encode("utf-8")), "good.xml")},
        headers={"Accept": "application/json"},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["elements_created"] == 1
    assert "errors" in body
