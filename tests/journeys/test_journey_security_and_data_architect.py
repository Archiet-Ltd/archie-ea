"""Journeys for the two personas promoted from charter-only on 31 Aug 2026.

`security_architect` and `data_architect` existed as AI charters that no user
could ever be. They were promoted because their absence created an
inconsistency, not because more roles seemed better:

* the solution blueprint scores a **Security Viewpoint** as one of its fifteen
  sections, and no persona owned it -- the same shape as an ARB decision with
  no visible decider, which this codebase spent the week fixing;
* **data architecture, lineage and stewardship** all ship, and ARCH-123 folded
  them into enterprise_architect with the explicit note "no dedicated role for
  either yet".

The other five charters (application_architect, integration_architect,
systems_architect, business_analyst, product_analyst) stay charter-only: they
own no surface the product grades, and two are analyst rather than architect
roles. That reasoning lives in ASPIRATIONAL in
scripts/check_persona_vocabularies.py so the gap stays visible.

A promoted persona has to be able to do its job, which is what these assert:
sign in, reach the surfaces the role exists for, and perform its write.
"""

import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def test_a_security_architect_can_record_and_see_a_risk(app, client):
    """The persona's write: the register is the security architect's ledger."""
    from app import db
    from app.models.risk import Risk

    with app.app_context():
        org_id = make_org(db, "SecArch")
        architect_id = make_user(
            db, org_id, "sec", enterprise_role="security_architect",
            role_name="Architect",
        )

    login(client, architect_id)
    title = "Unencrypted PII at rest %s" % uuid.uuid4().hex[:8]

    response = client.post(
        "/api/risks",
        json={
            "title": title,
            "description": "Customer records stored without disk encryption.",
            "likelihood": 4,
            "impact": 5,
            "owner": "Security guild",
        },
    )
    assert response.status_code == 201, response.data[:300]

    with app.app_context():
        db.session.expunge_all()
        row = db.session.execute(db.select(Risk).filter_by(title=title)).scalar_one()
        assert row.organization_id == org_id

    page = client.get("/risks/")
    assert page.status_code == 200
    assert title in page.get_data(as_text=True)


def test_the_security_architect_sidebar_reaches_their_own_surfaces(app, client):
    """A persona whose pages are only reachable by URL is not a persona.

    This is the discoverability rule the Level 10 walkthrough enforces, applied
    at the point a role is created rather than discovered later in a browser.
    """
    from app import db
    from app.utils.role_access import get_sidebar_zones
    from app.models.user import User

    with app.app_context():
        org_id = make_org(db, "SecNav")
        architect_id = make_user(
            db, org_id, "secnav", enterprise_role="security_architect",
            role_name="Architect",
        )
        user = db.session.get(User, architect_id)
        labels = {
            link["label"]
            for zone in get_sidebar_zones(user)
            for link in zone["links"]
        }

    # The surfaces that justified promoting the role.
    for expected in ("Policy Monitoring", "Risk Register", "Governance Gates"):
        assert expected in labels, (
            "%r is missing from the security architect's sidebar: %s"
            % (expected, sorted(labels))
        )


def test_security_architect_can_inspect_but_not_change_governance_gates(app, client):
    """Gate policy is part of security review, while mutation stays admin-only."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "SecGateRead")
        architect_id = make_user(
            db,
            org_id,
            "secgateread",
            enterprise_role="security_architect",
            role_name="Architect",
        )

    login(client, architect_id)

    page = client.get("/admin/governance-gates")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Governance Gates" in body
    assert 'data-governance-mode="read-only"' in body
    assert '@click="openCreate()"' not in body
    assert '@click="openEdit(gate)"' not in body

    listing = client.get("/admin/api/governance-gates")
    assert listing.status_code == 200
    assert listing.get_json()["success"] is True

    forbidden = client.post(
        "/admin/api/governance-gates",
        json={"gate_name": "security_architect_must_not_create"},
    )
    assert forbidden.status_code == 403


def test_a_data_architect_can_reach_the_data_layer_they_steward(app, client):
    """Data architecture, lineage and stewardship are this persona's remit.

    Neither page renders in any sidebar today (no segment's day-to-day work
    asks their question yet), so what still applies is that both stay
    reachable by their own URL for this persona rather than 403ing a role
    nobody added to the guards."""
    from app import db

    with app.app_context():
        org_id = make_org(db, "DataArch")
        architect_id = make_user(
            db, org_id, "data", enterprise_role="data_architect",
            role_name="Architect",
        )

    login(client, architect_id)
    for path in ("/architecture/data-architecture", "/architecture/data-lineage"):
        response = client.get(path)
        assert response.status_code == 200, (
            "%s returned %s for a data architect" % (path, response.status_code)
        )


def test_a_data_architect_records_a_capability_the_estate_can_use(app, client):
    """The persona's write, through the endpoint the UI calls."""
    from app import db
    from app.models.business_capabilities import BusinessCapability

    with app.app_context():
        org_id = make_org(db, "DataWrite")
        architect_id = make_user(
            db, org_id, "datawrite", enterprise_role="data_architect",
            role_name="Architect",
        )

    login(client, architect_id)
    name = "Master Data Management %s" % uuid.uuid4().hex[:8]

    response = client.post(
        "/enterprise/capabilities",
        json={"name": name, "type": "operational",
              "description": "Golden record for customer entities.", "level": 1},
    )
    assert response.status_code == 201, response.data[:300]

    with app.app_context():
        db.session.expunge_all()
        row = db.session.execute(
            db.select(BusinessCapability).filter_by(name=name)
        ).scalar_one()
        assert row.organization_id == org_id


def test_both_new_personas_get_their_own_governed_ai_charter(app):
    """A promoted persona must not fall back to a generalist prompt.

    The whole argument for promoting security_architect is that the Security
    Viewpoint needs an owner; handing it the enterprise_architect charter would
    concede that argument. And every charter must carry the no-fabrication
    rules -- a persona that can invent a control is worse than none.
    """
    with app.app_context():
        from app.modules.ai_chat.services.architect_persona_charters import (
            build_architect_prompt,
            get_default_chat_persona,
        )

        assert get_default_chat_persona("security_architect") == "security_architect"
        assert get_default_chat_persona("data_architect") == "data_architect"

        for persona in ("security_architect", "data_architect"):
            prompt = build_architect_prompt(persona)
            assert prompt, "%s has no charter" % persona
            assert "NO FABRICATION" in prompt, (
                "%s's charter does not carry the evidence rules" % persona
            )
