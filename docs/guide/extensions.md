# Extensions

Cross-cutting behavior applied to resources at startup. Like middleware but for the domain layer.

## Using extensions

Per-resource:

```python
from ironbridge.shared.framework.extensions.swagger import Swagger

class Job(Resource, Workflow):
    class Meta:
        extensions = [Swagger(tag="Maintenance")]
```

Per-module (applies to all resources in the module):

```python
class MaintenanceModule(Module):
    extensions = [Swagger(tag="Maintenance")]
    resources = [Job, Invoice]
```

## Built-in extensions

### Swagger

Adds OpenAPI tag metadata to derived routes:

```python
Swagger(tag="Maintenance")
```

## Writing custom extensions

Extend the `Extension` base class and override the hooks you need:

```python
from ironbridge.shared.framework import Extension

class AuditLog(Extension):
    """Log every action to an audit table."""

    def __init__(self, actions: list[str] | None = None):
        self.actions = actions  # None = all actions

    def after_action(self, actor, resource, action_name, result):
        if self.actions and action_name not in self.actions:
            return
        write_audit(actor.id, action_name, resource.id)
```

### Available hooks

| Hook | When | Use case |
|------|------|----------|
| `on_resource(cls)` | Once at startup, per resource | Add fields, modify class |
| `on_action(cls, name, meta)` | Once per action | Wrap actions, add policies |
| `on_signal(cls, name, def)` | Once per signal | Modify signal behavior |
| `on_route_derived(router, cls)` | After routes are generated | Add rate limits, middleware |
| `before_action(actor, resource, name)` | Before every action call | Validation, logging |
| `after_action(actor, resource, name, result)` | After every action call | Audit, notifications |
| `before_signal(actor, resource, name, payload)` | Before signal dispatch | Validation |

### Example: SoftDelete

```python
class SoftDelete(Extension):
    """Add is_deleted field and guard to all destructive actions."""

    def on_resource(self, cls):
        # Add is_deleted column if not present
        if not hasattr(cls, "is_deleted"):
            from sqlalchemy import Boolean
            from sqlalchemy.orm import mapped_column, Mapped
            cls.is_deleted = mapped_column(Boolean, default=False)

    def on_action(self, cls, action_name, action_meta):
        from ironbridge.shared.framework import not_deleted
        if action_meta.kind in ("update", "destroy"):
            guards = getattr(action_meta.fn, "_guards", [])
            guards.append(not_deleted())
            action_meta.fn._guards = guards
```

## Extension resolution

Extensions are resolved in order:
1. Module-level extensions (parent first, then child modules)
2. Resource-level extensions (from `Meta.extensions`)

If the same extension type appears at both levels, the resource-level one takes precedence.
