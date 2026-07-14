# Resources

A Resource is a SQLAlchemy model with declarative actions, policies, guards, and relationships. The framework derives REST routes, validation, and persistence from the declaration.

## Basic resource

```python
from datetime import datetime, UTC
from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from ironbridge.shared.framework import Resource

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)

class Branch(Resource):
    class Meta:
        tenant_scoped = False
        default_actions = ["get", "list"]

    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

## Meta options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `tenant_scoped` | `bool` | `False` | Auto-inject `tenant_id` column, filter by tenant on all queries |
| `default_actions` | `bool \| list` | `False` | Which CRUD actions to generate. `True` = all five |
| `extensions` | `list[Extension]` | `[]` | Extensions applied to this resource |
| `data_layer` | `DataLayer` | Postgres | Custom data layer (API-backed, in-memory, etc.) |
| `filters` | `list[str]` | `[]` | Fields that can be filtered on list queries |

## Default actions

`default_actions` generates CRUD endpoints with zero code:

```python
class Meta:
    default_actions = True                     # all: get, list, create, update, delete
    default_actions = ["get", "list"]          # just these
    default_actions = ["get", "list", "create"] # these three
```

What each generates:

| Name | Kind | Route | Policies (tenant_scoped) | Guards |
|------|------|-------|--------------------------|--------|
| `get` | READ | `GET /{id}` | same_tenant, anyone | - |
| `list` | READ | `GET /` | same_tenant, anyone | - |
| `create` | CREATE | `POST /` | same_tenant, role_is(admin,operator,system) | - |
| `update` | UPDATE | `PATCH /{id}` | same_tenant, role_is(admin,operator,system) | not_deleted |
| `delete` | DESTROY | `DELETE /{id}` | same_tenant, role_is(admin,operator,system) | not_deleted |

For non-tenant-scoped resources, `same_tenant` is omitted.

## Custom actions

When default actions don't fit, write a custom one:

```python
from ironbridge.shared.framework import action, ActionKind, policy, guard, role_is, in_state

class Branch(Resource):
    # ...

    @action(kind=ActionKind.CREATE)
    @policy(role_is("admin", "system"))
    def create(self, name: str, slug: str) -> "Branch":
        self.id = _cuid()
        self.name = name
        self.slug = slug
        self.active = True
        return self
```

The framework introspects the method signature to build a Pydantic input model. The generated route accepts `{"name": "...", "slug": "..."}` as JSON body.

### Action kinds

| Kind | HTTP | Behavior |
|------|------|----------|
| `CREATE` | `POST /` | Creates new instance, auto-saves |
| `READ` | `GET /{id}` | Reads existing instance |
| `UPDATE` | `POST /{id}/{name}` | Modifies existing instance, auto-saves |
| `DESTROY` | `DELETE /{id}/{name}` | Deletes (soft or hard) |
| `ACTION` | `POST /{id}/{name}` | Custom action on existing instance |

### Input from method signature

The framework builds a Pydantic model from method parameters:

```python
@action(kind=ActionKind.CREATE)
def create(self, name: str, slug: str, active: bool = True) -> "Branch":
    ...
```

Generates: `{"name": string (required), "slug": string (required), "active": boolean (optional, default true)}`

Skip parameters named `self`, `ctx`, or `return`. Everything else becomes a request field.

### Input from Pydantic model

For complex validation, use a Pydantic model:

```python
from pydantic import BaseModel, field_validator

class CreateBranchInput(BaseModel):
    name: str
    slug: str

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v):
        if not v.replace("-", "").isalnum():
            raise ValueError("Slug must be alphanumeric with hyphens")
        return v.lower()

@action(kind=ActionKind.CREATE)
def create(self, input: CreateBranchInput) -> "Branch":
    self.name = input.name
    self.slug = input.slug
    return self
```

## When to use default actions vs. custom

Ask in order:

1. Can `default_actions` handle it? Use it.
2. Does the action just set fields? Use `default_actions`.
3. Does it need custom validation? Custom `@action` with Pydantic input.
4. Does it need domain logic? Custom `@action` body.
5. Does it need I/O or multi-step logic? Use a Workflow with Signals.

If your `@action` body is just `self.x = x; return self`, use a default action instead.
