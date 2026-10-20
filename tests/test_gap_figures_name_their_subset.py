"""DG-030: the gap figure on three screens counts three different things, so
each screen's label says which one, and each figure equals its own subset.

  /enterprise/implementation/gap-analysis  gap records in the register
  /capability-roadmap                      business capabilities with a gap signal
  /architecture/traceability               Gap elements modelled in ArchiMate

Two organisations are seeded with different numbers so the counts are proven
tenant-scoped.
"""

from __future__ import annotations

import html as html_lib
import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_user(db_session, org, label):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    role = Role.query.filter_by(name="Administrator").first()
    if role is not None:
        user.role = role
    db_session.commit()
    return user


def _seed(db_session, org, registered, uncovered, modelled):
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability
    from app.services.gap_register_service import create_gap

    tag = uuid.uuid4().hex[:6]
    for i in range(registered):
        create_gap(org.id, f"Reg {tag} {i}", gap_type="coverage")
    for i in range(uncovered):
        db_session.add(BusinessCapability(
            name=f"Cap {tag} {i}", code=f"C-{tag}-{i}", organization_id=org.id, level=1))
    for i in range(modelled):
        db_session.add(ArchiMateElement(
            name=f"Model gap {tag} {i}", type="Gap", layer="Implementation",
            organization_id=org.id))
    db_session.commit()


def test_each_gap_figure_names_its_subset_and_matches_it(
    app, db_session, make_org, login_as, client
):
    org_a = make_org("dg030-a")
    org_b = make_org("dg030-b")
    _seed(db_session, org_a, registered=4, uncovered=2, modelled=3)
    _seed(db_session, org_b, registered=7, uncovered=5, modelled=6)
    login_as(client, _make_user(db_session, org_a, "dg030-user"))

    # Gap analysis: the register count, labelled as records in the register.
    page = client.get("/enterprise/implementation/gap-analysis")
    assert page.status_code == 200
    text = page.data.decode("utf-8")
    card = re.search(
        r"Recorded Gaps</p>\s*<div data-slot=\"card-title\"[^>]*>\s*([0-9]+)\s*</div>", text)
    assert card and int(card.group(1)) == 4
    assert "Gap records in the register, all types" in text

    # Roadmap: capabilities with a gap signal, not the register.
    api = client.get("/capability-map/api/roadmap/gaps").get_json()
    assert api["statistics"]["total_gaps"] == 2
    page = client.get("/capability-roadmap")
    assert page.status_code == 200
    text = page.data.decode("utf-8")
    assert "Capabilities With a Gap Signal" in text
    assert "Capability Gaps Detected" not in text
    assert "Business capabilities with a coverage, quality, retirement or modernization signal" in text

    # Traceability: Gap elements modelled in ArchiMate.
    page = client.get("/architecture/traceability")
    assert page.status_code == 200
    text = html_lib.unescape(page.data.decode("utf-8"))
    option = re.search(r"Gap \(([^)]*)\)", text)
    assert option is not None, "Gap pivot option missing"
    assert option.group(1) == "Projects and change, 3 gap elements modelled", option.group(1)
