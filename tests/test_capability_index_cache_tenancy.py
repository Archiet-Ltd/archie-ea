"""The architecture assistant's capability lookup cache is kept per organisation.

``_get_capability_by_id`` / ``_get_capability_by_name`` used to index every
capability visible to whichever organisation asked first and hand that index to
everyone for five minutes, so a second organisation resolving a capability id or
name received the first organisation's capability.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def world(db_session, make_org):
    from app.models.unified_capability import UnifiedCapability
    from app.services import architecture_assistant_service as svc

    svc._capability_cache.clear()

    class World:
        pass

    w = World()
    w.org_a, w.org_b = make_org("cia"), make_org("cib")
    tag = uuid.uuid4().hex[:6]
    w.a_name, w.b_name = f"Alpha capability {tag}", f"Beta capability {tag}"
    w.cap_a = UnifiedCapability(name=w.a_name, organization_id=w.org_a.id)
    w.cap_b = UnifiedCapability(name=w.b_name, organization_id=w.org_b.id)
    db_session.add_all([w.cap_a, w.cap_b])
    db_session.commit()
    yield w
    svc._capability_cache.clear()


@pytest.mark.parametrize("first,second", [("a", "b"), ("b", "a")])
def test_a_second_organisation_cannot_resolve_the_first_organisations_capability(
    tenant_ctx, world, first, second
):
    from app.services.architecture_assistant_service import (
        _get_capability_by_id,
        _get_capability_by_name,
    )

    orgs = {"a": world.org_a, "b": world.org_b}
    caps = {"a": world.cap_a, "b": world.cap_b}
    names = {"a": world.a_name, "b": world.b_name}

    with tenant_ctx(orgs[first].id):
        assert _get_capability_by_id(caps[first].id).id == caps[first].id
        assert _get_capability_by_name(names[first]).id == caps[first].id
    with tenant_ctx(orgs[second].id):
        assert _get_capability_by_id(caps[first].id) is None
        assert _get_capability_by_name(names[first]) is None
        assert _get_capability_by_id(caps[second].id).id == caps[second].id


def test_each_organisation_is_cached_under_its_own_key(tenant_ctx, world):
    from app.services import architecture_assistant_service as svc

    with tenant_ctx(world.org_a.id):
        svc._get_capability_by_id(world.cap_a.id)
    with tenant_ctx(world.org_b.id):
        svc._get_capability_by_id(world.cap_b.id)
    assert set(svc._capability_cache) == {world.org_a.id, world.org_b.id}


def test_nothing_is_cached_without_an_organisation(app, world):
    from app.services import architecture_assistant_service as svc

    with app.test_request_context("/"):
        svc._get_capability_by_id(world.cap_a.id)
    assert svc._capability_cache == {}
