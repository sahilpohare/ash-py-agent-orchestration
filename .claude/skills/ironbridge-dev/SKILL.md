# Ironbridge Development Skill

Build domain-driven applications on the Ironbridge framework. This skill covers resource design, workflow implementation, module structure, and code generation.

## Philosophy

### 0. Writing code is the last resort
Before writing ANY code, ask: "Can the framework derive this?" The order of preference:

1. **Default actions** -- `default_actions = ["get", "list", "create", "update", "delete"]`. Full CRUD with zero code.
2. **Declarations** -- fields, relationships, policies, guards, signals, extensions, filters. No logic, just metadata.
3. **Configuration** -- `Meta` options, `Signal()` params, `default_action()` with policies/guards. Still no logic.
4. **Subscriptions** -- `@on(Resource, "action")` for cross-domain side effects. A few lines of glue.
5. **Domain services** -- plain Python classes for complex business logic. Called from workflow handlers.
6. **Custom actions** -- only when default actions + guards can't express the behavior. Keep them thin: validate, mutate, return.
7. **Workflow handlers** -- only when there's a multi-step lifecycle with pause points. One function, top-to-bottom.
8. **Controllers** -- raw FastAPI routes. Absolute last resort, for things the framework can't derive (webhooks, OAuth, file uploads).

If you're writing an `@action` body that's just `self.field = value; return self`, use a default action instead. If you're writing a route handler, you're doing it wrong -- the framework derives routes. If you're writing a repository, you're doing it wrong -- `ctx.repo(Cls)` exists. If you're writing auth checks in a handler body, you're doing it wrong -- use `@policy`.

**The goal: a new CRUD resource is 10 lines. A new workflow is 30-50 lines. Everything else is derived.**

### 1. Declare the what, derive the how
The developer declares fields, relationships, actions, signals, policies, guards. The framework derives routes, validation, serialization, persistence, OpenAPI docs. Never hand-write what the framework can generate.

### 2. Resources are pure data + behavior
A Resource declares its fields, relationships, and actions. No I/O in action bodies. No DB imports. No HTTP imports. Actions validate, mutate state, and return. The framework handles persistence and transport.

Even writing actions should be a last resort. Ask first:
- Can `default_actions` handle it? (CRUD)
- Can a `@guard` prevent invalid state transitions? (preconditions)
- Can a `@policy` enforce authorization? (access control)
- Can a Signal + workflow handle it? (async state changes)

Only write a custom `@action` when the mutation logic can't be expressed declaratively.

### 3. Workflows are continuous functions
A workflow is one async function that runs top-to-bottom, pausing at `ctx.receive()` for external signals. Not a DAG. Not separate handlers per event. One function, readable top-to-bottom, that tells the full story of the lifecycle.

### 4. Modules are self-contained
Each module declares its prefix, resources, extensions, and dependencies. A module can be copied to another project and work standalone. Modules wire their own dependencies in `on_init()`.

### 5. The framework composes with DBOS, doesn't compete
DBOS handles durability (workflow checkpointing, step replay, send/recv). The framework adds domain modeling (Resource, Signal, Policy) and HTTP derivation (routes, validation, OpenAPI). Use DBOS directly for queues, cron, parallel workflows.

### 6. Two primitives, not three
- **Resource**: data + CRUD + relationships + policies + guards
- **Workflow** (mixin): adds Signals to a Resource for durable, interactive processes

No separate "controller" or "service" framework class. Controllers are plain FastAPI routes in the web layer. Services are plain Python classes injected via `ctx.deps`.

### 7. Explicit over magic
- `@workflow` marks a function as durable (not auto-detected)
- `Signal()` declares entry points (not scanned from code)
- `@policy()` / `@guard()` on actions (not global middleware)
- `async with ctx.receive()` for signal lifecycle (not implicit close)

### 8. Measure by what you didn't write
The quality of a domain module is measured by how LITTLE code it contains, not how much. A 10-line Resource with `default_actions = True` is better than a 50-line Resource with hand-written CRUD. A workflow with 3 `ctx.receive()` calls is better than 3 separate `on_` handlers. Zero custom actions means the domain is perfectly expressed by declarations.

---

## Architecture

```
ironbridge/              # Framework (never edit for domain work)
    shared/framework/    # Resource, Workflow, Signal, Actor, policies, guards, etc.
    shared/derive/       # Repository, DBOS wiring
    cli/                 # Generators

ironbridge_web/          # Web layer (derive routes, middleware, error handlers)
    derive/router.py     # derive_router() generates FastAPI routes
    middleware/          # Actor resolution, error mapping

lightwork/               # Domain app (YOUR code goes here)
    maintenance/         # Domain module
        job.py           # Resource + Workflow
        invoice.py       # Resource
        module.py        # Module with lifecycle hooks
        service.py       # Business logic (plain Python class)
    scheduling/
    engagement/
    ...
```

---

## SOPs

### SOP 1: Creating a new Resource

1. Generate scaffold:
```bash
ironbridge generate resource Invoice --module maintenance \
  --fields "job_id:str amount:Decimal? status:str=pending" \
  --relationships "job:belongs_to:Job"
```

2. Review the generated file. Fill in:
   - Custom actions beyond CRUD
   - Policies on each action (who can do it)
   - Guards on state-changing actions (preconditions)

3. Add to the module's `resources` list if not auto-added.

4. Run tests: `pytest tests/maintenance/test_invoice.py`

### SOP 2: Creating a new Workflow

1. Generate scaffold:
```bash
ironbridge generate workflow Job --module maintenance \
  --fields "state:str=opened description:str urgency:str=routine" \
  --signals "start:create quote_received approval"
```

2. Implement the `on_start` handler:
   - Set initial state and save
   - Use `async with ctx.receive("signal_name") as handle:` for each pause point
   - Handle timeouts (`if not handle:`)
   - Use `handle.respond(data)` to send results back to signal sender
   - Access dependencies via `ctx.deps.service_name`
   - Access other resources via `ctx.repo(ResourceClass)`

3. Set correct policies on each Signal:
   - CREATE signal: who can start the workflow
   - Mid-workflow signals: who can send them (e.g., `system_only()` for webhooks, `role_is("admin")` for operator actions)

4. Write tests with mock `recv_fn` that simulates the signal sequence.

### SOP 3: Creating a new Module

1. Generate scaffold:
```bash
ironbridge generate module Maintenance --resources "Job Invoice"
```

2. Implement `on_init()`:
   - Resolve shared dependencies from providers (`providers.resolve("db")`)
   - Register module-specific services (`providers.register("contractors", ...)`)

3. Add to the app's module list in `lightwork_web/main.py`.

### SOP 4: Adding a Signal to an existing Workflow

1. Add the Signal declaration on the class:
```python
quote_received = Signal(policies=[system_only()])
```

2. Add `ctx.receive("quote_received")` at the right point in the workflow handler.

3. The framework auto-generates the POST route. No manual route writing.

4. If the signal needs a typed input, add params to the on_ handler or use a Pydantic model.

### SOP 5: Adding Policies and Guards

**Policy** (who): decorates the action or declared on Signal.
```python
@action(kind=ActionKind.ACTION)
@policy(role_is("admin", "operator"))
def approve(self) -> "Job": ...

approval = Signal(policies=[role_is("admin", "operator")])
```

**Guard** (what state): decorates the action.
```python
@action(kind=ActionKind.ACTION)
@guard(in_state("quote_approval"))
@guard(field_set("quote_amount"))
def approve(self) -> "Job": ...
```

Guards on signals: declared on the Signal.
```python
approval = Signal(
    policies=[role_is("admin")],
    guards=[in_state("quote_approval")],  # checked before dispatch
)
```

### SOP 6: Cross-domain Communication

Use `@on` subscriptions. Never import across domains directly.

```python
# In lightwork/subscriptions.py (the wiring file)
from ironbridge.shared.framework import on
from lightwork.maintenance import Job
from lightwork.nurture import Lead

@on(Job, "start")
async def create_lead_on_job(resource, actor):
    Lead.start.send(None, {"source": "job", "source_id": resource.id}, actor=actor)
```

### SOP 7: Testing

**Unit tests** (no DB):
- Use `InMemoryRepository` for data
- Use `WorkflowContext` with mock `recv_fn` for workflows
- Use `enforce()` / `can()` to test policies and guards

**Integration tests** (with DB):
- Use the DBOS integration test fixtures
- Test full signal send/recv cycles
- Test pagination and filtering

### SOP 8: Custom Data Layers

For API-backed resources (e.g., Alto properties):

```python
class AltoPropertyLayer(DataLayer):
    def __init__(self, client):
        self.client = client

    def find_by_id(self, id):
        return self.client.get_property(id)

    def list(self, **filters):
        return self.client.search(**filters)

    def paginate(self, **kwargs):
        return self.client.search_paginated(**kwargs)

class Property(Resource):
    class Meta:
        data_layer = AltoPropertyLayer(alto_client)
    ...
```

### SOP 9: Extensions

Apply cross-cutting behavior:

```python
class MaintenanceModule(Module):
    extensions = [
        Swagger(tag="Maintenance"),
        # SoftDelete(),      # when implemented
        # Timestamps(),      # when implemented
        # AuditLog(),        # when implemented
    ]
```

Per-resource:
```python
class Job(Resource, Workflow):
    class Meta:
        extensions = [Swagger(tag="Jobs")]
```

### SOP 10: Escape Hatches

Not everything fits Resource/Workflow. Use plain FastAPI when needed:

- **Webhooks**: FastAPI route in `lightwork_web/controllers/`
- **OAuth flows**: FastAPI route with redirects
- **File uploads**: FastAPI route with `UploadFile`
- **SSE/WebSocket**: FastAPI route with streaming
- **Analytics**: FastAPI route with raw SQL via `ctx.repo(Cls).execute()`
- **Health checks**: Plain `@app.get("/health")`

These live in `lightwork_web/`, not in `lightwork/`. The domain stays clean.

---

## Code Patterns

### Resource pattern
```python
class Invoice(Resource):
    class Meta:
        tenant_scoped = True
        default_actions = ["get", "list"]
        filters = ["status", "job_id"]

    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    job_id: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")

    job = belongs_to("Job")

    @action(kind=ActionKind.CREATE)
    @policy(role_is("admin", "system"))
    def create(self, job_id: str, amount: str) -> "Invoice":
        self.job_id = job_id
        self.amount = amount
        return self

    @action(kind=ActionKind.UPDATE)
    @policy(role_is("admin"))
    @guard(in_state("pending", field="status"))
    def mark_paid(self) -> "Invoice":
        self.status = "paid"
        return self
```

### Workflow pattern
```python
class Job(Resource, Workflow):
    class Meta:
        tenant_scoped = True
        default_actions = ["get", "list"]
        filters = ["state", "urgency", "branch_id"]

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    state: Mapped[str] = mapped_column(String, default="opened")
    branch_id: Mapped[str] = mapped_column(String, nullable=False)
    contractor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quote_amount: Mapped[str | None] = mapped_column(String, nullable=True)

    branch = belongs_to("Branch")
    invoices = has_many("Invoice")

    start = Signal(kind=ActionKind.CREATE, policies=[role_is("admin")])
    quote_received = Signal(policies=[system_only()])
    approval = Signal(policies=[role_is("admin", "operator")])

    @workflow
    async def on_start(self, ctx, description: str, urgency: str, branch_id: str):
        self.id = _cuid()
        self.branch_id = branch_id
        self.description = description
        self.urgency = urgency
        self.state = "sourcing"
        ctx.save()

        contractor = ctx.deps.contractors.find(self.branch_id)
        self.contractor_id = contractor.id
        ctx.save()

        await ctx.deps.messaging.send_quote_request(self, contractor)

        async with ctx.receive("quote_received", timeout=timedelta(days=3)) as quote:
            if not quote:
                self.state = "expired"
                ctx.save()
                return
            self.quote_amount = quote["amount"]
            self.state = "quote_approval"
            ctx.save()
            quote.respond({"state": self.state, "amount": self.quote_amount})

        async with ctx.receive("approval", timeout=timedelta(days=7)) as decision:
            if not decision:
                self.state = "escalated"
                ctx.save()
                return
            if decision["action"] == "reject":
                self.state = "opened"
                self.quote_amount = None
                ctx.save()
                decision.respond({"state": self.state})
                return
            self.state = "booking"
            ctx.save()
            decision.respond({"state": self.state})

    @action(kind=ActionKind.DESTROY)
    @policy(role_is("admin"))
    @guard(not_deleted())
    def archive(self) -> "Job":
        self.is_deleted = True
        return self
```

### Module pattern
```python
class MaintenanceModule(Module):
    prefix = "/maintenance"
    resources = [Job, Invoice]
    extensions = [Swagger(tag="Maintenance")]

    @classmethod
    def on_init(cls, providers: Providers):
        db = providers.resolve("db")
        twilio = providers.resolve("twilio")
        providers.register("contractors", ContractorRepo(db))
        providers.register("messaging", TwilioMessaging(twilio))

    @classmethod
    def on_ready(cls):
        print("Maintenance module ready")
```

---

## Anti-patterns

### Code you should NOT be writing

1. **Don't write actions that just set fields.** If the action body is `self.x = x; return self`, use `default_actions` instead. The framework generates CRUD for you.

```python
# BAD: hand-written CRUD
@action(kind=ActionKind.CREATE)
def create(self, name: str, email: str) -> "User":
    self.name = name
    self.email = email
    return self

# GOOD: declare it
class User(Resource):
    class Meta:
        default_actions = ["create", "get", "list", "update"]
```

2. **Don't write route handlers.** If you're writing `@router.post(...)`, the framework should be deriving it. Use a Resource action or Signal instead.

3. **Don't write repositories.** Use `ctx.repo(Cls)`. If you need raw SQL, use `ctx.repo(Cls).execute()`.

4. **Don't write auth checks in handler bodies.** Use `@policy(role_is(...))`. The framework enforces before the handler runs.

5. **Don't write state checks in handler bodies.** Use `@guard(in_state(...))`. The framework returns 409 before the handler runs. Exception: complex business rules that can't be expressed as guards -- those use `raise ValueError(...)` in the body.

6. **Don't write separate DTO classes** unless you need complex validation. The framework introspects input types from method signatures. Only write a DTO class when the input shape differs significantly from the method params.

7. **Don't put I/O in action bodies.** Actions return the resource. Side effects go in `@on` subscriptions or workflow handlers.

8. **Don't import across domain modules.** Use `@on` subscriptions or `Signal.send()` for cross-domain communication.

9. **Don't use `asyncio.create_task` for background work.** Use `ctx.emit()` (fire-and-forget), `ctx.enqueue()` (durable queue), or `DBOS.start_workflow()` (parallel workflow).

10. **Don't wrap DBOS.** Use DBOS directly for queues, cron, parallel workflows. The framework wraps `recv/send` (signals) and `step` (ctx.save) because it adds domain semantics. Everything else, use DBOS as-is.

### The "should I write code?" checklist

Before writing any code, go through this:

| Need | Solution (no code) | Solution (minimal code) | Last resort |
|---|---|---|---|
| CRUD endpoint | `default_actions = True` | - | - |
| Filtered list | `filters = ["field1", "field2"]` on Meta | - | - |
| Authorization | `@policy(role_is("admin"))` | - | - |
| State precondition | `@guard(in_state("opened"))` | - | - |
| Field validation | Pydantic type in method signature | Pydantic `BaseModel` as input | - |
| Relationship | `belongs_to(Parent)` / `has_many(Child)` | - | - |
| Side effect | `@on(Resource, "action")` subscription | - | - |
| Multi-step process | Signals + `@workflow` handler | - | - |
| Cross-domain reaction | `@on` subscription in subscriptions.py | - | - |
| Custom business rule | - | `raise ValueError(...)` in action | Custom `@action` body |
| Complex orchestration | - | - | `@workflow` handler with `ctx.receive()` |
| External API call | - | Domain service class + `ctx.deps` | - |
| Non-REST endpoint | - | - | Raw FastAPI in lightwork_web |

If your answer is in the first column, write zero code. If it's in the second column, write 1-3 lines. The third column should be rare.
