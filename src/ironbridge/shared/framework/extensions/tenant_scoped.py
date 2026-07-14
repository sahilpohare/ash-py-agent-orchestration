"""
TenantScoped extension -- multi-tenant isolation via a FK column.

    class Job(Resource):
        class Meta:
            extensions = [TenantScoped(key="branch_id")]

What it does:
    - Injects the FK column if not already declared (at class creation time)
    - Adds same_tenant() policy to all write actions
    - Marks the resource so the derive layer filters queries by tenant

Configuration:
    TenantScoped(key="branch_id")                  # FK column name
    TenantScoped(key="branch_id", enforce=False)    # skip policy injection
"""
from __future__ import annotations

from typing import Any

from ironbridge.shared.framework.extension import Extension
from ironbridge.shared.framework.actions import ActionKind


class TenantScoped(Extension):

    def __init__(self, key: str = "tenant_id", enforce: bool = True):
        self.key = key
        self.enforce = enforce

    def inject_columns(self, namespace: dict, meta: dict) -> None:
        """Inject tenant FK column at class creation time."""
        meta["tenant_scoped"] = True
        meta["tenancy_key"] = (self.key,)

        if self.key not in namespace:
            from sqlalchemy import String, text as sa_text
            from sqlalchemy.orm import mapped_column
            namespace[self.key] = mapped_column(
                String,
                nullable=False,
                index=True,
                server_default=sa_text("current_setting('app.tenant_id', true)"),
            )

    def on_action(self, cls: type, action_name: str, action_meta: Any) -> None:
        """Add same_tenant policy to write actions."""
        if not self.enforce:
            return

        if action_meta.kind in (ActionKind.CREATE, ActionKind.UPDATE, ActionKind.DESTROY, ActionKind.ACTION):
            from ironbridge.shared.framework.policies import same_tenant
            existing = getattr(action_meta.fn, "_policies", [])
            if not any(p.name == "same_tenant" for p in existing):
                action_meta.fn._policies = [same_tenant()] + list(existing)
