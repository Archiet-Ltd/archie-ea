#!/usr/bin/env python
"""Fails when any organisation that has members has no Administrator.

An organisation without an Administrator cannot open its own settings or members
pages, and nobody inside it can fix that. This reads the database named by
TEST_DATABASE_URL (or DATABASE_URL) and lists every such organisation, using the
same rule the repair migration and the team pages use
(app/services/administrator_guard.py).

    python scripts/check_org_administrators.py          # list, exit 1 if any
    python scripts/check_org_administrators.py --count  # print the number only

Proven-against: a database holding an organisation whose only members are
Architects -- the check names that organisation and exits 1; after the repair
migration (20261027_admin_every_org) it exits 0. Pinned red-and-green by
tests/test_administrator_every_org.py.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def find_organisations(connection) -> list[dict]:
    from app.services.administrator_guard import organisations_without_administrator

    return organisations_without_administrator(connection)


def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", action="store_true")
    args = parser.parse_args(argv)

    url = database_url()
    if not url:
        print("neither TEST_DATABASE_URL nor DATABASE_URL is set", file=sys.stderr)
        return 2
    from sqlalchemy import create_engine

    engine = create_engine(url)
    with engine.begin() as connection:
        found = find_organisations(connection)
    if args.count:
        print(len(found))
        return 1 if found else 0
    for org in found:
        print("organisation %s (%s) has members and no Administrator" % (org["id"], org["name"]))
    if found:
        print(
            "\n%d organisation(s) without an Administrator. Run the migration "
            "20261027_admin_every_org (flask --app manage db upgrade)." % len(found)
        )
        return 1
    print("every organisation with members has an Administrator")
    return 0


if __name__ == "__main__":
    sys.exit(main())
