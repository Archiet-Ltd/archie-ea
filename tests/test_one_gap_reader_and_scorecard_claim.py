"""One reader answers "how many gaps", and the CTO scorecard's claim about
disagreement is computed from every reader, screens included.

Before: the Enterprise Dashboard and the Strategic Planning page fell back to
counting ArchiMate Gap elements when the gap register was empty, so they showed
3 beside a Gap Analysis and a roadmap showing 0, while the CTO scorecard said
"No disagreement found" because it only ever asked the stores, never a screen.

Two organisations: A holds three ArchiMate Gap elements and no register gap; B
holds two register gaps. Neither sees the other's figure.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import insert

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, enterprise_role):
    from app.models.user import User

    user = User(
        email=f"gap-reader-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Gap",
        last_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _gap_elements(db_session, org, count):
    from app.models.archimate_core import ArchiMateElement

    for i in range(count):
        db_session.execute(
            insert(ArchiMateElement.__table__).values(
                name=f"Gap element {i} {uuid.uuid4().hex[:6]}",
                type="Gap",
                layer="Implementation",
                organization_id=org.id,
            )
        )
    db_session.flush()


def _register_gaps(db_session, org, count):
    from app.models.implementation_migration import Gap

    for i in range(count):
        db_session.add(
            Gap(
                name=f"Register gap {i} {uuid.uuid4().hex[:6]}",
                gap_kind="capability_shortfall",
                organization_id=org.id,
            )
        )
    db_session.flush()


def _tile(html):
    match = re.search(r'data-testid="gap-count"[^>]*>\s*([\d,]+)\s*<', html)
    assert match, "no gap figure on the page"
    return int(match.group(1).replace(",", ""))


@pytest.fixture
def two_orgs(db_session, make_org):
    org_a, org_b = make_org("gap-reader-a"), make_org("gap-reader-b")
    _gap_elements(db_session, org_a, 3)
    _register_gaps(db_session, org_b, 2)
    return {
        "A": (org_a, _user(db_session, org_a, "a", "cto")),
        "B": (org_b, _user(db_session, org_b, "b", "cto")),
    }


def test_every_gap_screen_reads_the_one_register(client, login_as, two_orgs):
    for label, expected in (("A", 0), ("B", 2)):
        _org, user = two_orgs[label]
        for path in (
            "/enterprise/",
            "/enterprise/strategic",
            "/enterprise/implementation/gap-analysis",
        ):
            login_as(client, user)
            response = client.get(path)
            assert response.status_code == 200, (label, path)
            assert _tile(response.get_data(as_text=True)) == expected, (label, path)


def test_scorecard_names_a_disagreement_the_screens_show(
    client, login_as, two_orgs, monkeypatch
):
    """The register says 2, a screen says 99: the scorecard must say so. It
    used to ask only the stores and answer "No disagreement found"."""
    from app.services import gap_register_service

    real = gap_register_service.count_gaps
    monkeypatch.setattr(
        gap_register_service,
        "count_gaps",
        lambda org_id, *a, **k: 99 if (not a and not k) else real(org_id, *a, **k),
    )
    _org, user = two_orgs["B"]
    login_as(client, user)

    html = client.get("/architecture/cto-scorecard/").get_data(as_text=True)

    assert "No disagreement found" not in html
    assert "Enterprise Dashboard" in html and "99" in html


def test_scorecard_all_clear_states_what_it_compared(client, login_as, two_orgs):
    from scripts.check_store_agreement import CONCEPTS

    _org, user = two_orgs["B"]
    login_as(client, user)

    html = client.get("/architecture/cto-scorecard/").get_data(as_text=True)

    assert "No disagreement found" in html
    match = re.search(r"Compared (\d+) of (\d+) questions", html)
    assert match, "the all-clear does not say what it compared"
    compared, questions = int(match.group(1)), int(match.group(2))
    assert questions == len(CONCEPTS)
    # B holds gaps and almost nothing else, so most questions have no data to
    # compare; the claim must not be wider than the questions that did.
    assert 1 <= compared < questions


def test_scorecard_makes_no_claim_when_nothing_could_be_compared(
    db_session, make_org, client, login_as
):
    org = make_org("gap-reader-empty")
    user = _user(db_session, org, "empty", "cto")
    login_as(client, user)

    html = client.get("/architecture/cto-scorecard/").get_data(as_text=True)

    assert "No disagreement found" not in html
    assert "Nothing could be compared yet" in html
