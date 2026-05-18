"""Domain tests for Tenant."""

import pytest

from ironbridge.platform.identity.tenant import Tenant, TenantStatus


def make_tenant() -> Tenant:
    t = Tenant()
    t.create(name="Acme", slug="acme")
    return t


def test_create_assigns_id():
    t = Tenant()
    result = t.create(name="Acme", slug="acme")
    assert result.id
    assert len(result.id) > 0


def test_create_returns_self():
    t = Tenant()
    result = t.create(name="Acme", slug="acme")
    assert result is t


def test_create_sets_name_and_slug():
    t = make_tenant()
    assert t.name == "Acme"
    assert t.slug == "acme"


def test_create_status_is_active():
    t = make_tenant()
    assert t.status == TenantStatus.ACTIVE


def test_suspend():
    t = make_tenant()
    t.suspend()
    assert t.status == TenantStatus.SUSPENDED


def test_get_returns_self():
    t = make_tenant()
    assert t.get() is t


def test_tenant_actions():
    actions = set(Tenant.__actions__.keys())
    assert {"create", "suspend", "get"}.issubset(actions)
