"""Owners entered by import count as owners in the Ask catalogue and coverage (DG-015)."""
from __future__ import annotations

import uuid

import pytest

from tests.test_application_owner_one_store import _person

pytestmark = pytest.mark.usefixtures("db_session")


def _import(client, rows):
    response = client.post("/applications/import-manual", json={"applications": rows, "duplicate_mode": "skip"})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["created"] == len(rows)


def test_imported_owners_count_in_the_ask_catalogue_and_coverage(db_session, make_org, client, login_as):
    from app.modules.intelligence.services.query_catalogue import _applications_without_owner

    org, other = make_org("imp-own-a"), make_org("imp-own-b")
    importer = _person(db_session, org, "importer")
    colleague = _person(db_session, org, "colleague")  # a name that matches a user
    sfx = uuid.uuid4().hex[:6]
    login_as(client, importer)
    _import(client, [
        {"name": f"ImpOne {sfx}", "business_owner": f"{colleague.first_name} {colleague.last_name}"},
        {"name": f"ImpTwo {sfx}", "business_owner": "Olivia Outsider"},  # matches no user
        {"name": f"ImpNone {sfx}"},
    ])
    answer = _applications_without_owner(org.id)
    unowned = {r["name"] for g in answer["groups"] for r in g["rows"]}
    assert unowned == {f"ImpNone {sfx}"}, unowned
    assert answer["total"] == 1
    # the other organisation is untouched
    assert _applications_without_owner(other.id)["total"] == 0
