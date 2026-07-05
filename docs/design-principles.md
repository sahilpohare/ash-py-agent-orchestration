# Ironbridge Design Principles

Inspired by [Ash Framework](https://ash.hexdocs.pm/design-principles.html), adapted for Python.

---

## 1. Anything, not Everything

Build a framework capable of doing anything, not one that already does everything. The first is possible, the second is not.

The framework provides primitives: Resource, Workflow, Signal, Policy, Guard, Extension, Step. It does NOT provide tenancy, threads, auth, billing, notifications. Those are patterns you copy in (`ironbridge add`) or build yourself.

The framework unlocks potential. It doesn't prescribe solutions. Use the primitives to build whatever your domain requires. If the primitives don't cover a use case, use raw FastAPI. The framework should never be a cage.

**Practical implications:**
- No built-in tenant model. Use `ironbridge add tenancy` for a starting point, then customize.
- No built-in thread/conversation model. Use `ironbridge add threads`, own the code.
- No built-in auth. The framework provides Actor. You provide the JWT/session resolution.
- External service integrations are plain Python classes, not framework abstractions.

---

## 2. Declarative, Introspectable, Derivable

All behavior stems from explicit, static declarations. A Resource is a configuration that, by itself, does nothing. The derive layer reads it and generates routes, validation, persistence, OpenAPI docs.

This means:
- You can inspect any resource and know its full API surface without running the app.
- The derive layer can generate different outputs from the same declaration (FastAPI routes today, gRPC tomorrow).
- Tools can read declarations and generate documentation, diagrams, client SDKs.

**Practical implications:**
- Fields are `Mapped[str] = mapped_column(...)`. The derive layer reads them.
- Policies are `@policy(role_is("admin"))`. The enforcement layer reads them.
- Signals are `Signal(kind=ActionKind.CREATE)`. The derive layer generates routes.
- Relationships are `belongs_to("Branch", key="branch_id")`. The graph reads them.
- Input schemas are introspected from method signatures. No separate DTO classes.

---

## 3. Configuration over Convention

Explicit configuration produces more discoverable, maintainable, and flexible code than convention-based approaches. The framework avoids assumptions about naming patterns or implicit behavior.

Where conventions exist (e.g., `on_{signal_name}` handler matching), they are:
- Always overridable with explicit configuration (`Signal(handler=fn)`, `@signal.handler`)
- Validated at import time (wrong name = warning, not silent failure)
- Documented as conventions, not rules

**Practical implications:**
- `belongs_to("Branch", key="branch_id")` - explicit key, not inferred from name
- `has_many("Invoice", key="job_id")` - explicit FK, not guessed
- `Signal(handler=handle_start)` or `@start.handler` - explicit handler binding
- `Signal(name="open-job")` - explicit route name when it differs from attribute name
- No file-path-based discovery. Resources register via metaclass, not by directory structure.

---

## 4. Pragmatism First

Focus on addressing current needs with simple, practical solutions. Avoid building abstractions for hypothetical future requirements.

**Applied decisions:**
- We debated LiveObject, Reactor, codegen, and deferred all three. Not needed now.
- We kept `on_` convention alongside explicit handlers. Breaking all existing code for purity wasn't pragmatic.
- We use SQLAlchemy directly for schema definition instead of building a custom ORM layer.
- The `@step` decorator is backend-agnostic, but DBOS is the only implemented backend. Temporal support comes when someone needs it.

---

## 5. Writing Code is the Last Resort

Before writing any code, ask: "Can the framework derive this?" The order of preference:

1. **Default actions** - `default_actions = ["get", "list", "create"]`. Zero code.
2. **Declarations** - fields, relationships, policies, guards, signals. Metadata, not logic.
3. **Configuration** - `Meta` options, `Signal()` params. Still no logic.
4. **Subscriptions** - `@on(Resource, "action")` for cross-domain reactions. A few lines.
5. **Domain services** - plain Python classes for complex business logic.
6. **Custom actions** - only when defaults + guards can't express the behavior.
7. **Workflow handlers** - only for multi-step lifecycles with pause points.
8. **Controllers** - raw FastAPI routes. Absolute last resort.

A 10-line Resource with `default_actions = True` is better than a 50-line Resource with hand-written CRUD. The quality of a domain module is measured by how LITTLE code it contains.

---

## 6. The Execution Backend is Pluggable

The framework defines contracts. The derive layer implements them. The developer never imports the execution backend directly.

| Framework primitive | What it means | Backend implements |
|---|---|---|
| `@workflow` | This function is durable | DBOS wraps in `@DBOS.workflow()` |
| `@step(retries=5)` | This function is retriable | DBOS wraps in `@DBOS.step()` |
| `ctx.receive("signal")` | Pause for external input | DBOS uses `DBOS.recv()` |
| `ctx.save()` | Persist resource state | DBOS uses `@DBOS.step()` |
| `Signal.send(id, payload)` | Deliver to running workflow | DBOS uses `DBOS.send()` |

Replace DBOS with Temporal, Restate, or a plain Postgres job queue. The domain code doesn't change. Only the derive layer does.

---

## 7. Domains Don't Import Domains

Cross-domain communication goes through explicit wiring:
- `@on(Resource, "action")` subscriptions in `subscriptions.py`
- `Signal.send()` for inter-workflow signals

No domain module imports from another domain module. The subscription file is the ONLY place where multiple domains appear together. This makes domains portable: copy a domain to another project and it works standalone.

---

## 8. Resources are Pure, Workflows are Orchestration

A Resource is pure data + behavior. No I/O in action bodies. No external service calls. Actions validate, mutate state, and return.

A Workflow orchestrates. It calls external services, waits for signals, manages state transitions over time. Workflows use `ctx.deps` for injected services and `ctx.repo()` for data access.

This separation means:
- Resources are testable with plain asserts. No mocks needed.
- Workflows are testable with mock `recv_fn` and mock dependencies.
- Adding a new external service doesn't touch any Resource code.
