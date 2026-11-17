"""The Solutions Architecture dashboard counts the signed-in organisation's own records.

Two organisations, each with its own solutions, patterns and contracts. Whichever organisation is signed in, the
dashboard (from the sidebar and from the enterprise page) and /enterprise/api/solutions show only that
organisation's rows, and the contract figure is the number of contracts the Contracts register lists.
"""

import uuid
from datetime import date

import pytest
from flask import template_rendered


@pytest.fixture
def two_organisations(db_session, make_org):
    from app.models.application_portfolio import VendorContract
    from app.models.solution_models import Solution, SolutionPattern
    from app.models.user import User

    def estate(label, solutions, patterns, contracts, role="procurement"):
        org = make_org(label)
        person = User(
            email=f"dash-{label}-{uuid.uuid4().hex[:8]}@example.com",
            first_name=label.capitalize(),
            last_name="Tester",
            organization_id=org.id,
            confirmed=True,
            enterprise_role=role,
        )
        db_session.add(person)
        for n in range(solutions):
            db_session.add(Solution(name=f"{label} solution {n}", organization_id=org.id))
        for n in range(patterns):
            db_session.add(SolutionPattern(name=f"{label} pattern {n}", organization_id=org.id))
        for n in range(contracts):
            db_session.add(
                VendorContract(
                    contract_name=f"{label} contract {n}",
                    organization_id=org.id,
                    status="active",
                    start_date=date.today(),
                )
            )
        db_session.flush()
        return person

    first = estate("first", solutions=1, patterns=2, contracts=2)
    second = estate("second", solutions=3, patterns=1, contracts=4)
    # a third person in the first organisation, who may not open the Contracts register
    outsider = User(
        email=f"dash-outsider-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Outsider",
        last_name="Tester",
        organization_id=first.organization_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(outsider)
    db_session.commit()
    first.outsider = outsider
    return first, second


def _rendered_counts(app, client, login_as, person, path):
    seen = []

    def record(sender, template, context, **extra):
        seen.append(context)

    template_rendered.connect(record, app)
    try:
        login_as(client, person)
        response = client.get(path, follow_redirects=True)
    finally:
        template_rendered.disconnect(record, app)
    assert response.status_code == 200, response.status_code
    context = next(c for c in seen if "contract_count" in c)
    return context["solution_count"], context["pattern_count"], context["contract_count"]


@pytest.mark.parametrize("path", ["/architecture/solutions-architecture", "/enterprise/solutions_architecture_dashboard"])
def test_each_organisation_sees_only_its_own_counts(app, client, login_as, two_organisations, path):
    first, second = two_organisations
    assert _rendered_counts(app, client, login_as, first, path) == (1, 2, 2)
    assert _rendered_counts(app, client, login_as, second, path) == (3, 1, 4)


@pytest.mark.parametrize("path", ["/architecture/solutions-architecture", "/enterprise/solutions_architecture_dashboard"])
def test_the_contract_count_is_what_the_contracts_register_lists(app, client, login_as, two_organisations, path):
    from app.models.application_portfolio import VendorContract

    for person in two_organisations:
        seen = []

        def record(sender, template, context, **extra):
            seen.append(context)

        template_rendered.connect(record, app)
        try:
            login_as(client, person)
            response = client.get("/procurement/contracts")
        finally:
            template_rendered.disconnect(record, app)
        assert response.status_code == 200, response.status_code
        listed = next(c for c in seen if "contracts" in c)["contracts"]
        assert len(listed) == VendorContract.query.filter_by(organization_id=person.organization_id).count()
        assert _rendered_counts(app, client, login_as, person, path)[2] == len(listed)


def test_the_solutions_api_lists_only_the_signed_in_organisations_rows(client, login_as, two_organisations):
    first, second = two_organisations
    for person, solutions, patterns in ((first, 1, 2), (second, 3, 1)):
        login_as(client, person)
        body = client.get("/enterprise/api/solutions").get_json()
        assert len(body["solutions"]) == solutions
        assert len(body["patterns"]) == patterns
        label = "first" if person is first else "second"
        assert all(row["name"].startswith(label) for row in body["solutions"] + body["patterns"])


@pytest.mark.parametrize("path", ["/architecture/solutions-architecture", "/enterprise/solutions_architecture_dashboard"])
def test_someone_who_may_not_open_the_register_is_not_given_its_count(app, client, login_as, two_organisations, path):
    outsider = two_organisations[0].outsider
    assert client.get("/procurement/contracts").status_code in (302, 401, 403)
    login_as(client, outsider)
    assert client.get("/procurement/contracts").status_code == 403
    assert _rendered_counts(app, client, login_as, outsider, path) == (1, 2, None)
    login_as(client, outsider)
    assert client.get("/enterprise/api/solutions").get_json()["contracts"] == []
