# Ironbridge

A Python framework for building domain-driven applications. Declare your resources, relationships, policies, and workflows. The framework derives your REST API, persistence, validation, and durable execution.

```python
from fastapi import FastAPI
from ironbridge_web import Ironbridge

app = FastAPI(title="MyApp")
ib = Ironbridge(app, modules=[MyModule])
```

## Quick start

```bash
ironbridge new MyApp
cd my_app
docker compose up -d postgres
PYTHONPATH=src python -m my_app_web.main migrate
PYTHONPATH=src python -m my_app_web.main
open http://localhost:8000/docs
```

## What you write vs. what the framework derives

You declare:

```python
class Property(Resource):
    class Meta:
        tenant_scoped = True
        default_actions = True

    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    address: Mapped[str] = mapped_column(String, nullable=False)
    postcode: Mapped[str | None] = mapped_column(String, nullable=True)
    bedrooms: Mapped[int] = mapped_column(Integer, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
```

The framework derives:

- `POST /api/properties` (create, with input validation from columns)
- `GET /api/properties` (list, with pagination)
- `GET /api/properties/{id}` (get by id)
- `PATCH /api/properties/{id}` (partial update)
- `DELETE /api/properties/{id}` (soft delete via `is_deleted`)
- Pydantic response models for OpenAPI/Swagger docs
- Tenant isolation via `tenant_id` column (auto-injected)
- Authorization policies (admin/operator for writes, anyone for reads)
- Precondition guards (not_deleted for update/delete)

Zero custom code. 10 lines of declarations.

## Guide

1. [Concepts](concepts.md) -- philosophy, principles, why Ironbridge exists
2. [Getting Started](getting-started.md) -- project setup, first resource, running the app
3. [Resources](resources.md) -- fields, actions, default actions, custom actions
4. [Policies and Guards](policies-and-guards.md) -- authorization and preconditions
5. [Relationships](relationships.md) -- belongs_to, has_many, has_one, references, many_to_many
6. [Workflows](workflows.md) -- signals, ctx.receive, ctx.save, durable execution
7. [Steps](steps.md) -- retriable external calls with backoff
8. [Modules](modules.md) -- grouping resources, lifecycle hooks, dependency injection
9. [Subscriptions](subscriptions.md) -- cross-domain reactions
10. [Extensions](extensions.md) -- cross-cutting plugins
11. [Actor](actor.md) -- identity, tenancy, origin, delegation chain
12. [Testing](testing.md) -- unit tests, integration tests, in-memory repositories
13. [CLI](cli.md) -- generators, components, validation
