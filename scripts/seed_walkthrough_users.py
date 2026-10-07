#!/usr/bin/env python
"""Seed the tenant the archetype walkthrough drives.

One organisation, one NON-ADMIN user per persona, and the minimum real data the
journeys need. Deliberately not admin: an admin session satisfies every
require_roles guard on the way through and hides exactly the authorisation
defects Level 10 exists to find (TESTING_STANDARD.md rule 4).

Emails use `.example.com`, never `.test` -- email_validator rejects reserved
and special-use TLDs outright, so a `.test` address makes the login form answer
"Invalid email address." and the walkthrough reports a broken sign-in that is
really bad seed data.

    python scripts/seed_walkthrough_users.py
"""
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSWORD = os.environ.get("WALKTHROUGH_PASSWORD", "Walk!2026")
PERSONAS = {
    "cto": "cto@walkthrough.example.com",
    "enterprise_architect": "ea@walkthrough.example.com",
    "portfolio_manager": "pm@walkthrough.example.com",
    "arb_member": "arb@walkthrough.example.com",
    "solution_architect": "solution@walkthrough.example.com",
}


def main() -> int:
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import ApplicationRationalizationScore
    from app.models.archimate_core import ArchiMateElement
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app()
    with app.app_context():
        Role.insert_roles()
        org = Organization.query.filter_by(slug="walkthrough").first()
        if org is None:
            org = Organization(name="Walkthrough Org", slug="walkthrough")
            db.session.add(org)
            db.session.flush()
        # One person per persona is more than Community admits.
        from app.services.billing_plans import set_contract_plan

        set_contract_plan(org, "enterprise", None)
        db.session.commit()

        role = Role.query.filter_by(name="Architect").first()
        users = {}
        for persona, email in PERSONAS.items():
            user = User.query.filter_by(email=email).first()
            if user is None:
                user = User(
                    email=email, first_name=persona.split("_")[0].title(),
                    last_name="Walkthrough", organization_id=org.id,
                    confirmed=True, enterprise_role=persona,
                )
                user.password = PASSWORD
                user.role = role
                db.session.add(user)
                db.session.commit()
            users[persona] = user

        if not ArchiMateElement.query.filter_by(name="Kafka 3.x").first():
            for name, note in (("Kafka 3.x", "Event backbone."),
                               ("Oracle WebLogic 12c", "Legacy app server.")):
                db.session.add(ArchiMateElement(
                    name=name, type="Node", layer="Technology",
                    organization_id=org.id, description=note))
            db.session.commit()

        component = ApplicationComponent.query.filter_by(name="Legacy Invoicing").first()
        if component is None:
            component = ApplicationComponent(name="Legacy Invoicing", organization_id=org.id)
            db.session.add(component)
            db.session.flush()
            db.session.add(ApplicationRationalizationScore(
                application_component_id=component.id, organization_id=org.id,
                review_status="reviewed", overall_health_score=41.0,
                rationalization_action="ELIMINATE"))
            db.session.commit()

        if not ARBReviewItem.query.filter_by(title="Retire Legacy Invoicing").first():
            db.session.add(ARBReviewItem(
                organization_id=org.id,
                review_number="REV-WALK-%s" % uuid.uuid4().hex[:6].upper(),
                title="Retire Legacy Invoicing", description="Decommission proposal.",
                review_type="solution_design", status="submitted",
                submitter_id=users["solution_architect"].id,
                submitted_at=datetime.utcnow()))
            db.session.commit()

        print("seeded org %s with %d personas" % (org.id, len(users)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
