"""The import review tells the person exactly which rows arrived and which were
refused, each refusal naming the cell it is about and why -- and the count the
review promises is the count the import then creates.

A spreadsheet of twenty systems with three bad rows must come back as seventeen
accepted and three refused -- one with no name, and two that repeat a name
already in the file. No bad row is quietly imported, so the review's figures are
the figures the person can act on. Both halves are asserted: the review's own
counts, and that committing the same file creates exactly seventeen.
"""
from __future__ import annotations

import io
import json
import uuid

import pytest


def _admin(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    user = User(
        email=f"review-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Review",
        last_name="Importer",
        organization_id=org.id,
        role=Role.query.filter_by(name="User").first(),
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin")
    user.grant_org_admin()
    db_session.flush()
    return user


def _twenty_row_file(token):
    """Twenty rows: seventeen good, three refused -- one with no name (its other
    cell is filled so it is a real row), and two that repeat a name already in
    the file."""
    lines = ["Name,Reference"]
    for i in range(1, 18):
        lines.append(f"System {i} {token},ref-{i}")
    lines.append(",ref-19")                        # row 19: no name
    lines.append(f"System 1 {token},ref-20")       # row 20: repeats row 2
    lines.append(f"System 4 {token},ref-21")       # row 21: repeats row 5
    return "\n".join(lines)


def _analyze(client, login_as, user, text):
    login_as(client, user)
    mappings = {"Name": "name"}
    data = {
        "file": (io.BytesIO(text.encode()), "systems.csv"),
        "field_mappings": json.dumps(mappings),
        "date_format": "iso",
    }
    return client.post(
        "/applications/analyze-import-duplicates",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def _commit(client, login_as, user, text):
    login_as(client, user)
    mappings = {"Name": "name"}
    data = {
        "file": (io.BytesIO(text.encode()), "systems.csv"),
        "import_mode": "skip",
        "field_mappings": json.dumps(mappings),
    }
    return client.post(
        "/applications/import",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=False,
    )


@pytest.mark.journey
def test_twenty_rows_three_bad_gives_seventeen_accepted_three_refused(
    client, db_session, make_org, login_as
):
    org = make_org("review")
    user = _admin(db_session, org)
    token = uuid.uuid4().hex[:8]
    text = _twenty_row_file(token)

    resp = _analyze(client, login_as, user, text)
    assert resp.status_code == 200, resp.data[:400]
    body = resp.get_json()
    assert body["success"] is True

    assert body["total_rows"] == 20, body
    assert body["accepted_count"] == 17, body
    assert body["refused_count"] == 3, body
    assert len(body["refused"]) == 3

    # Every refusal names the cell it is about and gives a plain reason.
    assert all(r.get("cell") for r in body["refused"]), body["refused"]
    assert all(r.get("reason") for r in body["refused"]), body["refused"]
    reasons = " ".join(r["reason"] for r in body["refused"])
    cells = [r["cell"] for r in body["refused"]]
    assert any("Name" in c for c in cells), cells
    assert "No application name" in reasons, reasons
    assert reasons.count("already on row") >= 2, reasons

    # No bad row is also reported as accepted: the two sets do not overlap.
    accepted_rows = {a["row"] for a in body["accepted"]}
    refused_rows = {r["row"] for r in body["refused"]}
    assert accepted_rows.isdisjoint(refused_rows), (accepted_rows, refused_rows)


@pytest.mark.journey
def test_the_import_creates_exactly_what_the_review_promised(
    client, db_session, make_org, login_as
):
    """The seventeen the review accepted are the seventeen the commit creates."""
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("commit")
    user = _admin(db_session, org)
    token = uuid.uuid4().hex[:8]
    text = _twenty_row_file(token)

    review = _analyze(client, login_as, user, text).get_json()
    assert review["accepted_count"] == 17

    before = ApplicationComponent.query.filter_by(organization_id=org.id).count()
    resp = _commit(client, login_as, user, text)
    assert resp.status_code in (200, 302), resp.data[:400]
    db_session.expire_all()
    after = ApplicationComponent.query.filter_by(organization_id=org.id).count()

    created = after - before
    assert created == review["accepted_count"] == 17, (
        "the review promised %s rows would arrive; the import created %s"
        % (review["accepted_count"], created)
    )
    # The refused rows never became applications.
    assert ApplicationComponent.query.filter_by(
        organization_id=org.id
    ).filter(ApplicationComponent.name.is_(None)).count() == 0


@pytest.mark.journey
def test_importing_right_after_reviewing_the_same_file_still_creates_the_rows(
    client, db_session, make_org, login_as
):
    """Reviewing a file must not block importing it moments later.

    The review writes an audit row for the file it analysed. A too-broad
    idempotency check then matched that row by filename and refused the real
    import of the same file, silently creating nothing. The person reviews,
    then imports, and the rows must still arrive.
    """
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("review-then-import")
    user = _admin(db_session, org)
    token = uuid.uuid4().hex[:8]
    text = _twenty_row_file(token)

    # 1. Review the file (this writes the analysis audit row).
    review = _analyze(client, login_as, user, text).get_json()
    assert review["accepted_count"] == 17

    # 2. Import the same file, with an import_token, right after.
    before = ApplicationComponent.query.filter_by(organization_id=org.id).count()
    data = {
        "file": (io.BytesIO(text.encode()), "systems.csv"),
        "import_mode": "update",
        "field_mappings": json.dumps({"Name": "name"}),
        "import_token": uuid.uuid4().hex,
    }
    login_as(client, user)
    resp = client.post(
        "/applications/import", data=data,
        content_type="multipart/form-data", follow_redirects=False,
    )
    assert resp.status_code in (200, 302), resp.data[:400]
    db_session.expire_all()
    after = ApplicationComponent.query.filter_by(organization_id=org.id).count()
    assert after - before == 17, (
        "reviewing the file first blocked its import; created %s, expected 17"
        % (after - before)
    )
