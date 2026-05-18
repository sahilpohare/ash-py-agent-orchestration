"""Domain tests for User."""

import pytest

from ironbridge.platform.identity.user import User, UserRole, UserStatus


def make_user() -> User:
    u = User()
    u.create(email="alice@example.com", name="Alice")
    return u


def test_create_assigns_id():
    u = User()
    result = u.create(email="alice@example.com", name="Alice")
    assert result.id
    assert len(result.id) > 0


def test_create_returns_self():
    u = User()
    result = u.create(email="alice@example.com", name="Alice")
    assert result is u


def test_create_lowercases_email():
    u = User()
    u.create(email="ALICE@EXAMPLE.COM", name="Alice")
    assert u.email == "alice@example.com"


def test_create_strips_email():
    u = User()
    u.create(email="  alice@example.com  ", name="Alice")
    assert u.email == "alice@example.com"


def test_create_default_role_is_member():
    u = make_user()
    assert u.role == UserRole.MEMBER


def test_create_custom_role():
    u = User()
    u.create(email="admin@example.com", name="Admin", role="ADMIN")
    assert u.role == UserRole.ADMIN


def test_create_status_is_active():
    u = make_user()
    assert u.status == UserStatus.ACTIVE


def test_create_invalid_role_raises():
    u = User()
    with pytest.raises(ValueError):
        u.create(email="x@example.com", name="X", role="GOD")


def test_change_role():
    u = make_user()
    u.change_role("ADMIN")
    assert u.role == UserRole.ADMIN


def test_change_role_invalid_raises():
    u = make_user()
    with pytest.raises(ValueError):
        u.change_role("SUPERUSER")


def test_deactivate():
    u = make_user()
    u.deactivate()
    assert u.status == UserStatus.DEACTIVATED


def test_get_returns_self():
    u = make_user()
    assert u.get() is u


def test_user_actions():
    actions = set(User.__actions__.keys())
    assert {"create", "change_role", "deactivate", "get"}.issubset(actions)
