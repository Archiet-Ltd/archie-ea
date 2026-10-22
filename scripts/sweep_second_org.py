#!/usr/bin/env python
"""A second organisation for the sweep's isolation pass: one person, one record of its own.

    set -a; . ~/verify/walk-<name>.env; . ~/verify/preview-demo.env; set +a
    python scripts/sweep_second_org.py

The live sweep signs in as this person and opens every address it opened for the demo company's people. Nothing
belonging to the demo company may appear. Idempotent. Throwaway databases only; never prints a password.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ORG_NAME, ORG_SLUG = "Isolation Co", "isolation-co"
EMAIL = "isolation.architect@isolation-co.example.test"
SOLUTION = "Isolation Co private design"


def main():
    from werkzeug.security import generate_password_hash

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.solution_models import Solution
    from app.models.user import PASSWORD_HASH_METHOD, Role, User

    app = create_app(os.environ.get("FLASK_CONFIG", "testing"))
    with app.app_context():
        org = Organization.query.filter_by(slug=ORG_SLUG).first()
        if org is None:
            org = Organization(name=ORG_NAME, slug=ORG_SLUG)
            db.session.add(org)
            db.session.flush()
        Role.insert_roles()
        user = User.query.filter_by(email=EMAIL).first()
        if user is None:
            user = User(email=EMAIL, first_name="Isolation", last_name="Architect", organization_id=org.id, confirmed=True,
                        role=Role.query.filter_by(name="Viewer").first(), enterprise_role="enterprise_architect",
                        password_hash=generate_password_hash(os.environ["DEMO_USER_PASSWORD"], method=PASSWORD_HASH_METHOD))
            db.session.add(user)
            db.session.flush()
        if Solution.query.filter_by(organization_id=org.id, name=SOLUTION).first() is None:
            db.session.add(Solution(organization_id=org.id, name=SOLUTION, status="in_progress", created_by_id=user.id,
                                    description="A design that belongs to the second organisation only."))
        db.session.commit()
        print(f"second organisation ready: {ORG_NAME}, person {EMAIL}")


if __name__ == "__main__":
    main()
