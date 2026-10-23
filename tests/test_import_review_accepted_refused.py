"""The import review tells the person exactly which rows arrived and which were
refused, each refusal naming the cell it is about and why.

A spreadsheet of twenty systems with three bad rows must come back as seventeen
accepted and three refused -- a row with no name, a row repeating one already in
the file, and a row whose date cell cannot be read -- and no bad row may be
quietly imported with its bad cell dropped. The review's own figures are the
figures the person acts on, so they are asserted exactly.
"""
from __future__ import annotations

import io
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
    """Twenty rows: seventeen good, and three that must be refused -- one with no
    name, one repeating the first row's name, and one with an unreadable date."""
    lines = ["Name,Go Live Date"]
    # Seventeen unique, valid rows (rows 2..18 of the file).
    for i in range(1, 18):
        lines.append(f"System {i} {token},2020-01-15")
    # Row 19: no name at all.
    lines.append(",2020-01-15")
    # Row 20: repeats the first row's name.
    lines.append(f"System 1 {token},2020-01-15")
    # Row 21: a valid name but a date cell that cannot be read.
    lines.append(f"System Dated {token},not-a-date")
    return "\n".join(lines)


def _analyze(client, login_as, user, text):
    login_as(client, user)
    mappings = {"Name": "name", "Go Live Date": "go_live_date"}
    import json

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


@pytest.mark.journey
def test_twenty_rows_three_bad_gives_seventeen_accepted_three_refused(
    client, db_session, make_org, login_as
):
    org = make_org("review")
    user = _admin(db_session, org)
    token = uuid.uuid4().hex[:8]

    resp = _analyze(client, login_as, user, _twenty_row_file(token))
    assert resp.status_code == 200, resp.data[:400]
    body = resp.get_json()
    assert body["success"] is True

    assert body["total_rows"] == 20, body
    assert body["accepted_count"] == 17, body
    assert body["refused_count"] == 3, body
    assert len(body["refused"]) == 3

    # Every refusal names the cell it is about and gives a plain reason.
    reasons = " ".join(r["reason"] for r in body["refused"])
    cells = [r["cell"] for r in body["refused"]]
    assert all(r.get("cell") for r in body["refused"]), body["refused"]
    assert all(r.get("reason") for r in body["refused"]), body["refused"]
    # The no-name refusal points at the Name column.
    assert any("Name" in c for c in cells), cells
    assert "No application name" in reasons, reasons
    # The in-file duplicate names the earlier row.
    assert "already on row" in reasons, reasons
    # The bad-date refusal points at the Go Live Date column.
    assert any("Go Live Date" in c for c in cells), cells
    assert "date" in reasons.lower(), reasons

    # No bad row is also reported as accepted: the counts do not overlap.
    accepted_rows = {a["row"] for a in body["accepted"]}
    refused_rows = {r["row"] for r in body["refused"]}
    assert accepted_rows.isdisjoint(refused_rows), (accepted_rows, refused_rows)
