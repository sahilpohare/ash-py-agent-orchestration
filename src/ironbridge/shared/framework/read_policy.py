# Read policies -- filter which records an actor can see.
#
# Simple policies (PolicyDef) answer yes/no: "can this actor do this action?"
# Read policies answer "which records can this actor see?" by returning query filters.
#
# Usage on a resource:
#
#     class Enquiry(Resource):
#         class Meta:
#             read_policy = visible_enquiries
#
#     @read_filter
#     def visible_enquiries(actor, query, cls):
#         if actor.is_system:
#             return query                                # see all
#         scope = f"branch:{actor.tenant_id}"
#         if actor_has_role(actor, scope, "admin"):
#             return query                                # admin sees all in tenant
#         if actor_has_role(actor, scope, "viewer"):
#             return query.filter(cls.done_by == actor.id) # viewer sees own
#         return query.filter(cls.id == None)              # see nothing
#
# The derive layer applies read_policy to every list and get query.
# For ?include= joins, the included resource's read_policy filters the join.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReadPolicyDef:
    """A named read filter. Returns a modified query."""
    name: str
    apply: Callable  # (actor, query, cls) -> query


def read_filter(fn: Callable) -> ReadPolicyDef:
    """Decorator: turn a function into a ReadPolicyDef.

        @read_filter
        def visible_enquiries(actor, query, cls):
            if actor.is_system:
                return query
            return query.filter(cls.branch_id == actor.tenant_id)
    """
    return ReadPolicyDef(name=fn.__name__, apply=fn)


# ---------------------------------------------------------------------------
# Built-in read policies
# ---------------------------------------------------------------------------

def tenant_visible(tenant_key: str = "branch_id") -> ReadPolicyDef:
    """Filter by tenant. System sees all. Others see their tenant only."""
    def apply(actor: Any, query: Any, cls: Any) -> Any:
        if actor.is_system:
            return query
        col = getattr(cls, tenant_key, None)
        if col is not None:
            return query.filter(col == actor.tenant_id)
        return query

    return ReadPolicyDef(name=f"tenant_visible({tenant_key})", apply=apply)


def owner_visible(owner_field: str = "created_by") -> ReadPolicyDef:
    """Only see records you created. System and admin see all."""
    def apply(actor: Any, query: Any, cls: Any) -> Any:
        if actor.is_system:
            return query
        # Check if actor has admin role on scope
        from .auth import actor_has_role
        scope = f"branch:{actor.tenant_id}"
        if actor_has_role(actor, scope, "admin", "owner", "superadmin"):
            return query
        col = getattr(cls, owner_field, None)
        if col is not None:
            return query.filter(col == actor.id)
        return query

    return ReadPolicyDef(name=f"owner_visible({owner_field})", apply=apply)


def role_visible(*role_filters: tuple[str, ...]) -> ReadPolicyDef:
    """Different visibility per role. Ordered: first match wins.

        role_visible(
            ("admin", None),                          # admin sees all
            ("viewer", lambda q, cls: q.filter(...)), # viewer sees filtered
        )
    """
    def apply(actor: Any, query: Any, cls: Any) -> Any:
        if actor.is_system:
            return query
        from .auth import actor_has_role
        scope = f"branch:{actor.tenant_id}"
        for role_name, filter_fn in role_filters:
            if actor_has_role(actor, scope, role_name):
                if filter_fn is None:
                    return query  # no filter = see all
                return filter_fn(query, cls)
        # No matching role = see nothing
        return query.filter(cls.id == None)  # noqa: E711

    return ReadPolicyDef(name="role_visible", apply=apply)


# ---------------------------------------------------------------------------
# Apply read policy to a query
# ---------------------------------------------------------------------------

def apply_read_policy(actor: Any, query: Any, cls: type) -> Any:
    """Apply the resource's read_policy to a query. Returns filtered query."""
    meta = getattr(cls, "__meta__", {})
    read_pol = meta.get("read_policy")
    if read_pol is None:
        return query
    return read_pol.apply(actor, query, cls)
