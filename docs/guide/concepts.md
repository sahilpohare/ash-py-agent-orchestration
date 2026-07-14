# Concepts

Ironbridge is a domain-driven framework for Python. It takes inspiration from [Ash Framework](https://ash-hq.org/) (Elixir) and adapts its ideas for the Python ecosystem with FastAPI and SQLAlchemy.

This page explains why Ironbridge exists, how it thinks about application code, and the principles behind every design decision.

---

## The problem

Most Python web apps start simple. A few FastAPI routes, some SQLAlchemy models, a couple of Pydantic schemas. Then the domain grows:

- Every route handler repeats auth checks, tenant filtering, error handling
- Every model needs a repository with the same find/list/save/delete methods
- Business rules scatter across route handlers, utility functions, and model methods
- Adding a new entity means writing a model, a repo, a router, DTOs, tests -- 200+ lines before you write a single line of domain logic
- Durable processes (multi-step workflows with external signals) get hand-rolled with state columns and polling loops

The codebase grows linearly with the domain. 10 entities = 10x the boilerplate. The framework should absorb that cost.

## The solution: declare, don't code

Ironbridge inverts the relationship between declaration and code. Instead of writing handlers that do things, you declare what things are and the framework derives the rest.

A Resource with `default_actions = True` gives you five REST endpoints, input validation, response serialization, pagination, tenant isolation, authorization, and OpenAPI docs. Zero custom code. 10 lines of declarations.

When the default behavior doesn't fit, you add a custom action -- but even then, policies and guards are declarative. The action body contains only domain logic, never auth checks or state validation.

When a process spans multiple steps and external signals, you write a Workflow -- one continuous function that reads top-to-bottom as the full story of the lifecycle.

## Two primitives

Ironbridge has two domain primitives. Not three, not five. Two.

**Resource**: data + CRUD + relationships + policies + guards. This is your entity. It maps to a database table and a REST API. It declares its fields, who can do what to it, and what state it must be in. The framework derives routes, validation, and persistence.

**Workflow**: a Resource that also accepts Signals and runs durable processes. Workflow is a mixin, not a separate class. A maintenance job is a Resource (it has fields, relationships, actions) that also has a lifecycle (signals, pause points, state transitions).

Everything else in the framework exists to support these two primitives:

- **Signal** -- a typed entry point into a Workflow
- **Policy** -- who can perform an action (authorization)
- **Guard** -- what state must be true (preconditions)
- **Module** -- groups Resources under a prefix
- **Extension** -- cross-cutting behavior
- **Step** -- retriable external call
- **Subscription** -- cross-domain reaction
- **Actor** -- identity + tenancy + origin context

## Writing code is the last resort

Before writing any code, ask: can the framework derive this?

The preference order:

1. **Default actions** -- `default_actions = True`. Full CRUD. Zero code.
2. **Declarations** -- fields, relationships, policies, guards, signals. Metadata, not logic.
3. **Configuration** -- `Meta` options, `Signal()` params. Still no logic.
4. **Subscriptions** -- `@on(Resource, "action")`. A few lines of glue.
5. **Domain services** -- plain Python classes for complex business logic.
6. **Custom actions** -- only when defaults + guards can't express the behavior.
7. **Workflow handlers** -- only for multi-step lifecycles with pause points.
8. **Controllers** -- raw FastAPI routes. Absolute last resort.

The quality of a domain module is measured by how little code it contains. A 10-line Resource is better than a 50-line Resource. A workflow with three `ctx.receive()` calls is better than three separate event handlers.

## Declarative, introspectable, derivable

All behavior stems from explicit, static declarations. A Resource is a configuration that, by itself, does nothing. The derive layer reads it and generates routes, validation, persistence, OpenAPI docs.

This means:
- You can inspect any resource and know its full API surface without running the app
- The derive layer can generate different outputs from the same declaration (FastAPI routes today, gRPC tomorrow)
- Tools can read declarations and generate documentation, diagrams, client SDKs
- AI assistants can read your domain model and generate correct code because the declarations are the source of truth

## Configuration over convention

Explicit configuration produces more discoverable, maintainable, and flexible code than convention-based approaches.

```python
# Explicit: you know exactly what this does by reading it
branch = belongs_to("Branch", key="branch_id")

# Convention: you have to know the framework's naming rules
branch = belongs_to("Branch")  # infers key="branch_id" from target name
```

Where conventions exist (like `on_{signal_name}` for workflow handlers), they are always overridable with explicit configuration and validated at import time.

## The execution backend is pluggable

You never import DBOS, Temporal, or Restate. The framework defines contracts (`@workflow`, `@step`, `ctx.receive`, `ctx.save`). The derive layer implements them with whatever backend is configured.

| You write | Framework contract | DBOS implements |
|---|---|---|
| `@workflow` | This function is durable | `@DBOS.workflow()` |
| `@step(retries=3)` | This function is retriable | `@DBOS.step(max_attempts=4)` |
| `ctx.receive("signal")` | Pause for external input | `DBOS.recv_async()` |
| `ctx.save()` | Persist resource state | `@DBOS.step()` + SQL upsert |
| `Signal.send(id, payload)` | Deliver to running workflow | `DBOS.send_async()` |

Replace DBOS with Temporal, Restate, or a plain Postgres job queue. Your domain code doesn't change. Only the derive layer does.

## Domains don't import domains

Cross-domain communication goes through one explicit wiring point:

```python
# subscriptions.py -- the ONLY place multiple domains appear together
from ironbridge.shared.framework import on
from .maintenance import Job
from .leads import Lead

@on(Job, "start")
async def create_lead(resource, actor):
    Lead.start.send(None, {"source": "job", "source_id": resource.id})
```

No domain module imports from another domain module. This makes domains portable -- copy a module to another project and it works standalone.

## Resources are pure, workflows are orchestration

A Resource is pure data + behavior. No I/O in action bodies. No external service calls. Actions validate, mutate state, and return.

```python
# Pure: no I/O, no side effects, just state mutation
@action(kind=ActionKind.UPDATE)
@guard(in_state("quote_approval"))
def approve(self) -> "Job":
    self.state = "approved"
    return self
```

A Workflow orchestrates. It calls external services via `@step` functions, waits for signals via `ctx.receive()`, and manages state transitions over time.

```python
# Orchestration: I/O via @step, state via ctx.save(), pause via ctx.receive()
@workflow
async def on_start(self, ctx, description: str):
    self.state = "sourcing"
    ctx.save()

    async with ctx.receive("quote_received") as quote:
        self.quote_amount = quote["amount"]
        ctx.save()

    notify_contractor(self.contractor_name, self.id)  # @step -- retriable
```

This separation means:
- Resources are testable with plain asserts. No mocks needed.
- Workflows are testable with mock `recv_fn`.
- Adding a new external service doesn't touch any Resource code.

## Anything, not everything

The framework provides primitives. It does NOT provide tenancy, threads, auth, billing, notifications. Those are patterns you copy in (`ironbridge add`) or build yourself.

The framework unlocks potential. It doesn't prescribe solutions. If the primitives don't cover a use case, use raw FastAPI. The framework should never be a cage.

## FastAPI stays on the outside

Ironbridge mounts onto your FastAPI app. It doesn't wrap it, replace it, or hide it.

```python
app = FastAPI(title="MyApp")         # you own this
ib = Ironbridge(app, modules=[...])  # ironbridge layers on top
```

Add your own middleware, routes, lifespan handlers, WebSocket endpoints, static files. Ironbridge adds domain routes, error handlers, and actor middleware. They coexist.

This is deliberate. Frameworks that own the app object create vendor lock-in. FastAPI is mature, well-documented, and widely understood. Ironbridge builds on it, not around it.
