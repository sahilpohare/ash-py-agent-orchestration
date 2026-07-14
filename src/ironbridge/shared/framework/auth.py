"""
Auth primitives -- Role, Membership, permission registry, and role/requires policies.

Roles are named sets of permissions. Memberships bind an actor to a role on a scope.
Permissions are auto-derived from resources ({module}:{action}) at startup.

Two policy styles:

    # Role-based: readable, seeds the permission grid with defaults
    @policy(role("admin", "viewer"))
    def mark_done(self) -> "Enquiry": ...

    # Permission-based: granular, references the grid directly
    @policy(requires("enquiries:mark_done"))
    def mark_done(self) -> "Enquiry": ...

Both check the same underlying system. Both are overridable from the permission grid UI.

The permission grid (DB-backed) can override code defaults at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .policies import PolicyDef, PolicyVerdict


# ---------------------------------------------------------------------------
# Role -- a named set of permissions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Role:
    """A named set of permissions. Defined in code, editable via grid."""
    name: str
    permissions: frozenset[str]

    def allows(self, permission: str) -> bool:
        """Check if this role grants a permission. Supports wildcards.

        Wildcard patterns:
            "*"            -- matches everything
            "enquiries:*"  -- matches any action in enquiries module
            "*:get"        -- matches get action in any module
            "*:*"          -- matches everything (same as "*")
        """
        if "*" in self.permissions:
            return True
        if permission in self.permissions:
            return True
        if ":" not in permission:
            return False
        module, action = permission.split(":", 1)
        # Module wildcard: "enquiries:*" matches "enquiries:archive"
        if f"{module}:*" in self.permissions:
            return True
        # Action wildcard: "*:get" matches "enquiries:get"
        if f"*:{action}" in self.permissions:
            return True
        # Full wildcard
        if "*:*" in self.permissions:
            return True
        return False


# ---------------------------------------------------------------------------
# Membership -- actor has role on scope
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Membership:
    """Actor has a role on a scope (e.g., branch, org)."""
    scope: str          # "branch:london", "org:acme"
    role: str           # "admin", "viewer" -- resolved to Role at check time


# ---------------------------------------------------------------------------
# Role registry
# ---------------------------------------------------------------------------

_roles: dict[str, Role] = {}
_scope_hierarchy: Any = None  # optional: scope -> parent scope


def register_roles(*roles: Role) -> None:
    """Register role definitions. Call at app startup."""
    for r in roles:
        _roles[r.name] = r


def get_role(name: str) -> Role | None:
    return _roles.get(name)


def get_all_roles() -> dict[str, Role]:
    return dict(_roles)


def set_scope_hierarchy(fn) -> None:
    """Set a function that resolves parent scope. e.g., branch:london -> org:acme."""
    global _scope_hierarchy
    _scope_hierarchy = fn


def _parent_scope(scope: str) -> str | None:
    if _scope_hierarchy:
        return _scope_hierarchy(scope)
    return None


# ---------------------------------------------------------------------------
# Permission registry (auto-populated at startup from resources)
# ---------------------------------------------------------------------------

@dataclass
class PermissionDef:
    """A permission derived from a resource action."""
    slug: str               # "enquiries:archive"
    module: str             # "enquiries"
    action: str             # "archive"
    description: str        # from docstring
    default_roles: list[str]  # from @policy(role("admin")) annotation


_permissions: dict[str, PermissionDef] = {}
# Runtime overrides from DB (permission grid). Loaded at startup.
_permission_overrides: dict[str, set[str]] = {}  # permission_slug -> set of role names


def register_permission(perm: PermissionDef) -> None:
    _permissions[perm.slug] = perm


def get_all_permissions() -> dict[str, PermissionDef]:
    return dict(_permissions)


def set_permission_overrides(overrides: dict[str, set[str]]) -> None:
    """Load permission grid overrides from DB. Call at startup."""
    global _permission_overrides
    _permission_overrides = overrides


def _roles_for_permission(permission: str) -> set[str]:
    """Get roles that have a given permission. Checks overrides first, then defaults."""
    if permission in _permission_overrides:
        return _permission_overrides[permission]
    perm = _permissions.get(permission)
    if perm:
        return set(perm.default_roles)
    return set()


# ---------------------------------------------------------------------------
# Actor permission check
# ---------------------------------------------------------------------------

def actor_has_permission(actor: Any, scope: str, permission: str) -> bool:
    """Check if actor has a permission on a scope, via memberships + role definitions."""
    if actor.is_system:
        return True

    memberships = getattr(actor, "memberships", ())

    # Check direct scope
    for m in memberships:
        if m.scope == scope:
            role_def = get_role(m.role)
            if role_def and role_def.allows(permission):
                return True

    # Check parent scope (org-level roles grant branch access)
    parent = _parent_scope(scope)
    while parent:
        for m in memberships:
            if m.scope == parent:
                role_def = get_role(m.role)
                if role_def and role_def.allows(permission):
                    return True
        parent = _parent_scope(parent)

    return False


def actor_has_role(actor: Any, scope: str, *role_names: str) -> bool:
    """Check if actor has one of the named roles on a scope."""
    if actor.is_system:
        return True

    memberships = getattr(actor, "memberships", ())

    for m in memberships:
        if m.scope == scope and m.role in role_names:
            return True

    # Check parent scope
    parent = _parent_scope(scope)
    while parent:
        for m in memberships:
            if m.scope == parent and m.role in role_names:
                return True
        parent = _parent_scope(parent)

    return False


# ---------------------------------------------------------------------------
# Resolve scope from resource (via TenantScoped key)
# ---------------------------------------------------------------------------

def _resolve_scope(resource: Any) -> str:
    """Get the scope string for a resource. Uses TenantScoped key."""
    meta = getattr(type(resource), "__meta__", {})
    tenancy_key = meta.get("tenancy_key", ("tenant_id",))
    key_field = tenancy_key[0] if tenancy_key else "tenant_id"
    scope_id = resource.__dict__.get(key_field) if hasattr(resource, "__dict__") else getattr(resource, key_field, None)
    if scope_id:
        return f"branch:{scope_id}"
    # Fallback: actor's tenant
    return ""


# ---------------------------------------------------------------------------
# Policy factories
# ---------------------------------------------------------------------------

def role(*role_names: str, message: str = "Insufficient role") -> PolicyDef:
    """
    Policy: actor must have one of these roles on the resource's scope.

    Also seeds the permission grid: when the framework collects permissions at startup,
    the role names listed here become the default roles for that permission.

        @policy(role("admin", "viewer"))
        def mark_done(self) -> "Enquiry": ...

    PM reads: "admins and viewers can mark done."
    """
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        if actor.is_system:
            return PolicyVerdict.ALLOW
        scope = _resolve_scope(resource)
        if not scope:
            # No scope -- fall back to flat role check
            if hasattr(actor, "role") and actor.role in role_names:
                return PolicyVerdict.ALLOW
            return PolicyVerdict.DENY
        if actor_has_role(actor, scope, *role_names):
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY

    p = PolicyDef(name=f"role({','.join(role_names)})", check=check, message=message)
    # Tag with default roles so the permission collector can read them
    object.__setattr__(p, "_default_roles", list(role_names))
    return p


def requires(*permissions: str, message: str = "Missing permission") -> PolicyDef:
    """
    Policy: actor must have ALL listed permissions on the resource's scope.

        @policy(requires("enquiries:archive"))
        def archive(self) -> "Enquiry": ...

    Checks the actor's role permissions against the permission grid.
    """
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        if actor.is_system:
            return PolicyVerdict.ALLOW
        scope = _resolve_scope(resource)
        if not scope:
            return PolicyVerdict.DENY
        for perm in permissions:
            if not actor_has_permission(actor, scope, perm):
                return PolicyVerdict.DENY
        return PolicyVerdict.ALLOW

    return PolicyDef(name=f"requires({','.join(permissions)})", check=check, message=message)


def owner(field: str = "created_by", message: str = "Not the owner") -> PolicyDef:
    """Policy: actor must be the creator of this resource."""
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        if actor.is_system:
            return PolicyVerdict.ALLOW
        owner_id = getattr(resource, field, None)
        if owner_id and owner_id == actor.id:
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY

    return PolicyDef(name=f"owner({field})", check=check, message=message)


def system(message: str = "System action only") -> PolicyDef:
    """Policy: only system/agent actors. Bypasses permission grid."""
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        if actor.is_system:
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY

    return PolicyDef(name="system", check=check, message=message)
