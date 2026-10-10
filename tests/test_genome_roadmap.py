"""Implementation-domain genome slice: the deterministic Plateau -> WorkPackage
transformation roadmap slice + gantt emitter (ADR 0010, 3rd domain).

Asserts the properties the slice must prove, mirroring the proven coverage test:
  (a) real plateau and work-package names from the tenant render;
  (b) provenance resolves — structural for anchored rows, honestly synthetic
      (never a fabricated id) for a legitimately-unanchored row, and fail-closed
      for a dangling anchor;
  (c) DETERMINISM — building the slice twice yields byte-identical output
      (same spec_hash) and the emitted HTML is byte-identical.

Uses the SHARED fixtures in tests/conftest.py (db_session, make_org, tenant_ctx).
Seeds a small org with 2 plateaus, 3 work packages, and their ArchiMate elements.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.modules.genome.emit.roadmap_gantt import emit_roadmap_gantt_html
from app.modules.genome.services.roadmap_slice import (
    SliceProvenanceError,
    build_roadmap_slice,
)


def _seed(db_session, org_id):
    """Seed 2 plateaus + 3 work packages (2 anchored, 1 gets auto-created element).

    Returns the names and element ids for assertions.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Plateau, WorkPackage

    # ArchiMate elements (the provenance anchors).
    p_elem_1 = ArchiMateElement(name="Current State", type="Plateau", layer="implementation", organization_id=org_id)
    p_elem_2 = ArchiMateElement(name="Target State", type="Plateau", layer="implementation", organization_id=org_id)
    wp_elem_1 = ArchiMateElement(name="Migrate CRM", type="WorkPackage", layer="implementation", organization_id=org_id)
    wp_elem_2 = ArchiMateElement(name="Decommission Legacy ERP", type="WorkPackage", layer="implementation", organization_id=org_id)
    db_session.add_all([p_elem_1, p_elem_2, wp_elem_1, wp_elem_2])
    db_session.flush()

    p1 = Plateau(
        name="Current State", organization_id=org_id, sequence_order=1,
        target_date=date(2026, 1, 1), archimate_element_id=p_elem_1.id,
    )
    p2 = Plateau(
        name="Target State", organization_id=org_id, sequence_order=2,
        target_date=date(2027, 1, 1), archimate_element_id=p_elem_2.id,
    )
    db_session.add_all([p1, p2])
    db_session.flush()

    wp1 = WorkPackage(
        name="Migrate CRM", organization_id=org_id, plateau_id=p2.id,
        status="in_progress", priority="high", percent_complete=40,
        start_date=date(2026, 2, 1), target_date=date(2026, 8, 1),
        sequence_order=1, archimate_element_id=wp_elem_1.id,
    )
    wp2 = WorkPackage(
        name="Decommission Legacy ERP", organization_id=org_id, plateau_id=p2.id,
        status="planned", priority="medium", percent_complete=0,
        start_date=date(2026, 6, 1), target_date=date(2027, 1, 1),
        sequence_order=2, archimate_element_id=wp_elem_2.id,
    )
    # Third work package is created without an explicit archimate_element_id;
    # the backbone's before_flush listener creates one automatically.  It is
    # unassigned to a plateau — exercises the Unassigned path.
    wp3 = WorkPackage(
        name="Backlog Grooming", organization_id=org_id,
        status="planned", priority="low", percent_complete=0,
        sequence_order=3,
    )
    db_session.add_all([wp1, wp2, wp3])
    db_session.flush()

    # Collect all element ids including the auto-created one for wp3.
    all_element_ids = {p_elem_1.id, p_elem_2.id, wp_elem_1.id, wp_elem_2.id}
    if wp3.archimate_element_id:
        all_element_ids.add(wp3.archimate_element_id)

    return {
        "plateau_names": {"Current State", "Target State"},
        "wp_names": {"Migrate CRM", "Decommission Legacy ERP", "Backlog Grooming"},
        "element_ids": all_element_ids,
    }


def test_roadmap_slice_real_names_and_provenance(db_session, make_org, tenant_ctx):
    org = make_org("genome-roadmap")
    seed = _seed(db_session, org.id)

    with tenant_ctx(org.id):
        s = build_roadmap_slice(org.id)

    # (a) real names render into the slice.
    plateau_names = {p["name"] for p in s["plateaus"]}
    wp_names = {w["name"] for w in s["work_packages"]}
    assert seed["plateau_names"] <= plateau_names
    assert seed["wp_names"] <= wp_names

    assert s["domain"] == "implementation"
    assert len(s["plateaus"]) == 2
    assert len(s["work_packages"]) == 3

    # (b) plateaus are structurally anchored to real elements.
    for p in s["plateaus"]:
        prov = p["provenance"]
        assert prov["origin"] == "structural"
        assert prov["archimate_element_id"] in seed["element_ids"]
        assert prov["archimate_type"] == "Plateau"

    # Anchored work packages are structural; the third one has an auto-created
    # element from the backbone listener so it is also structural.
    by_name = {w["name"]: w for w in s["work_packages"]}
    for name in ("Migrate CRM", "Decommission Legacy ERP", "Backlog Grooming"):
        prov = by_name[name]["provenance"]
        assert prov["origin"] == "structural"
        assert prov["archimate_element_id"] in seed["element_ids"]

    # Plateau assignment and dates thread through.
    assert by_name["Migrate CRM"]["plateau_id"] is not None
    assert by_name["Migrate CRM"]["start_date"] == "2026-02-01"
    assert by_name["Migrate CRM"]["percent_complete"] == 40


def test_roadmap_slice_determinism(db_session, make_org, tenant_ctx):
    org = make_org("genome-roadmap")
    _seed(db_session, org.id)

    with tenant_ctx(org.id):
        s1 = build_roadmap_slice(org.id)
        s2 = build_roadmap_slice(org.id)

    # (c) determinism: identical spec_hash and byte-identical emitted HTML.
    assert s1["spec_hash"] == s2["spec_hash"]
    assert s1["spec_hash"].startswith("sha256:")
    html1 = emit_roadmap_gantt_html(s1)
    html2 = emit_roadmap_gantt_html(s2)
    assert html1 == html2


def test_emitted_html_renders_real_names_and_provenance(db_session, make_org, tenant_ctx):
    org = make_org("genome-roadmap")
    _seed(db_session, org.id)

    with tenant_ctx(org.id):
        s = build_roadmap_slice(org.id)
    html = emit_roadmap_gantt_html(s)

    # Real names appear in the rendered gantt.
    assert "Current State" in html
    assert "Migrate CRM" in html

    # Every anchored row carries its provenance element id as a data-* attribute.
    for p in s["plateaus"]:
        assert f'data-element-id="{p["provenance"]["archimate_element_id"]}"' in html
    # All work packages now have auto-created elements, so all are structural.
    assert 'data-provenance-origin="structural"' in html
    assert 'data-domain="implementation"' in html


def test_roadmap_page_renders_for_tenant(db_session, make_org, client, login_as):
    """End-to-end: the presenter's click renders the roadmap with real names."""
    import uuid

    from app.models.user import User

    org = make_org("genome-roadmap")
    _seed(db_session, org.id)
    user = User(
        email=f"genome-rm-{uuid.uuid4().hex[:10]}@example.test",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()

    login_as(client, user)
    resp = client.post("/genome/roadmap/generate")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Migrate CRM" in body
    assert "Current State" in body
    assert 'data-domain="implementation"' in body
    assert "data-element-id=" in body


def test_dangling_anchor_fails_closed():
    """An id that is SET but resolves to no real element must FAIL, not silently gap.

    On the live schema a dangling anchor is unreachable through the ORM: the
    ``work_packages.archimate_element_id`` FK rejects a non-existent id, and its
    ``ON DELETE SET NULL`` turns a deleted anchor into a legitimately-NULL one
    (honest synthetic provenance), never a dangle. The fail-closed guard is still
    the contract for a corrupt/legacy row, so it is pinned directly against the
    resolver rather than by fighting the FK — distinct from the NULL case, which
    the slice records as synthetic and is covered above.
    """
    from app.modules.genome.services.roadmap_slice import _provenance

    element_types = {5: "WorkPackage"}  # id 999 is deliberately absent

    # A NULL anchor is honestly synthetic, not an error.
    prov = _provenance(None, "WorkPackage", element_types, "WorkPackage 1")
    assert prov["origin"] == "synthetic"
    assert prov["archimate_element_id"] is None

    # A SET-but-unresolvable anchor fails closed.
    with pytest.raises(SliceProvenanceError):
        _provenance(999, "WorkPackage", element_types, "WorkPackage 2")

    # A SET-and-resolvable anchor is structural with the real type.
    prov = _provenance(5, "WorkPackage", element_types, "WorkPackage 3")
    assert prov["origin"] == "structural"
    assert prov["archimate_element_id"] == 5
    assert prov["archimate_type"] == "WorkPackage"
