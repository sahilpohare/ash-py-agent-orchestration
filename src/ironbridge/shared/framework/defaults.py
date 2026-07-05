"""
Default actions for Resources.

Two ways to use:

1. Meta.default_actions — pick which defaults you want:

    class Job(Resource):
        class Meta:
            default_actions = True                          # all five
            default_actions = ["create", "get", "list"]     # pick
            default_actions = False                         # none (default)

2. default_action() — declare individually with custom policies/guards:

    class Job(Resource):
        create = default_action(ActionKind.CREATE)
        get = default_action(ActionKind.READ)
        delete = default_action(ActionKind.DESTROY,
            policies=[system_only()],
            guards=[in_state("closed")],
        )

Both can coexist. Explicit default_action() declarations override Meta defaults.
"""
from __future__ import annotations

from typing import Any

from .actions import ActionKind, action
from .guards import GuardDef, not_deleted
from .policies import PolicyDef, anyone, policy, role_is, same_tenant


# ---------------------------------------------------------------------------
# default_action() — the public API
# ---------------------------------------------------------------------------

def default_action(
    kind: ActionKind,
    *,
    policies: list[PolicyDef] | None = None,
    guards: list[GuardDef] | None = None,
) -> Any:
    """
    Declare a default action with standard implementation.

    The framework provides the body. You control the kind, policies, and guards.

        create = default_action(ActionKind.CREATE)
        get = default_action(ActionKind.READ)
        delete = default_action(ActionKind.DESTROY,
            policies=[system_only()],
            guards=[in_state("closed")],
        )
    """
    body = _BODIES[kind]
    name = _infer_name(kind, body)

    fn = action(kind=kind, name=name)(body)

    if policies:
        for p in reversed(policies):
            fn = policy(p)(fn)

    if guards:
        fn._guards = list(getattr(fn, "_guards", [])) + list(guards)

    # Mark so inject_defaults knows this was explicit
    fn._is_default_action = True
    return fn


# ---------------------------------------------------------------------------
# Standard implementations
# ---------------------------------------------------------------------------

def _body_create(self: Any, **kwargs: Any) -> Any:
    """Set fields from kwargs."""
    for k, v in kwargs.items():
        if hasattr(self, k):
            setattr(self, k, v)
    return self


def _body_get(self: Any) -> Any:
    """Return self (loaded by derive layer)."""
    return self


def _body_list(self: Any) -> Any:
    """Return self (derive layer handles actual listing)."""
    return self


def _body_update(self: Any, **kwargs: Any) -> Any:
    """Partial update from kwargs."""
    for k, v in kwargs.items():
        if hasattr(self, k):
            setattr(self, k, v)
    return self


def _body_delete(self: Any) -> Any:
    """Soft delete if is_deleted exists, otherwise hard delete handled by derive layer."""
    if hasattr(self, "is_deleted"):
        self.is_deleted = True
    return self


_BODIES = {
    ActionKind.CREATE: _body_create,
    ActionKind.READ: _body_get,
    ActionKind.UPDATE: _body_update,
    ActionKind.DESTROY: _body_delete,
}

# For READ kind, we need to distinguish get vs list.
# default_action(READ) called as `get = ...` infers name from variable name.
# But since we can't read the variable name at call time, we use a fallback.
# The metaclass will rename based on the attribute name in the namespace.

def _infer_name(kind: ActionKind, body: Any) -> str:
    """Best-effort name from the body function."""
    name_map = {
        _body_create: "create",
        _body_get: "get",
        _body_list: "list",
        _body_update: "update",
        _body_delete: "delete",
    }
    return name_map.get(body, kind.value)


# ---------------------------------------------------------------------------
# inject_defaults — called by ResourceMeta for Meta.default_actions
# ---------------------------------------------------------------------------

_ALL_DEFAULTS = ["create", "get", "list", "update", "delete"]

_DEFAULT_POLICIES = {
    "create": lambda ts: [same_tenant(), role_is("admin", "operator", "system")] if ts
                         else [role_is("admin", "operator", "system")],
    "get":    lambda ts: [same_tenant(), anyone()] if ts else [anyone()],
    "list":   lambda ts: [same_tenant(), anyone()] if ts else [anyone()],
    "update": lambda ts: [same_tenant(), role_is("admin", "operator", "system")] if ts
                         else [role_is("admin", "operator", "system")],
    "delete": lambda ts: [same_tenant(), role_is("admin", "operator", "system")] if ts
                         else [role_is("admin", "operator", "system")],
}

_DEFAULT_GUARDS = {
    "update": [not_deleted()],
    "delete": [not_deleted()],
}

_DEFAULT_KINDS = {
    "create": ActionKind.CREATE,
    "get": ActionKind.READ,
    "list": ActionKind.READ,
    "update": ActionKind.UPDATE,
    "delete": ActionKind.DESTROY,
}

_DEFAULT_BODIES = {
    "create": _body_create,
    "get": _body_get,
    "list": _body_list,
    "update": _body_update,
    "delete": _body_delete,
}


def inject_defaults(namespace: dict, meta: dict) -> None:
    """
    Inject default action methods into a Resource's namespace.
    Called by ResourceMeta.__new__ before the class is created.

    Reads meta["default_actions"]:
        True                         -> all five
        ["create", "get", "list"]    -> only those
        False / missing              -> none
    """
    da = meta.get("default_actions", False)
    if da is False:
        return

    if da is True:
        names = _ALL_DEFAULTS
    elif isinstance(da, (list, tuple)):
        names = list(da)
    else:
        return

    tenant_scoped = meta.get("tenant_scoped", False)

    for name in names:
        if name not in _DEFAULT_KINDS:
            raise ValueError(f"Unknown default action: {name!r}. Must be one of {_ALL_DEFAULTS}")

        # Don't override user-defined actions or explicit default_action() calls
        if name in namespace:
            existing = namespace[name]
            # Fix up the action name if it was declared via default_action()
            # since default_action can't know the variable name at call time
            if callable(existing) and getattr(existing, "_is_default_action", False):
                if hasattr(existing, "__action__"):
                    existing.__action__.name = name
            continue

        fn = default_action(
            _DEFAULT_KINDS[name],
            policies=_DEFAULT_POLICIES[name](tenant_scoped),
            guards=_DEFAULT_GUARDS.get(name),
        )
        # Set correct name (default_action infers from body, but we know the real name)
        if hasattr(fn, "__action__"):
            fn.__action__.name = name
        namespace[name] = fn
