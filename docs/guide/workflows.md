# Workflows

A Workflow is a Resource that accepts external Signals and runs durable multi-step processes. One continuous async function, readable top-to-bottom, pausing at `ctx.receive()` for external input.

## Minimal workflow

```python
from ironbridge.shared.framework import (
    Resource, Workflow, Signal, workflow,
    ActionKind, policy, role_is, system_only,
)

class Job(Resource, Workflow):
    class Meta:
        tenant_scoped = False
        default_actions = ["get", "list"]

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    state: Mapped[str] = mapped_column(String, default="opened")
    description: Mapped[str] = mapped_column(String, default="")
    workflow_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Signals declare entry points. Each generates a POST route.
    start = Signal(kind=ActionKind.CREATE, policies=[role_is("admin")])
    approval = Signal(policies=[role_is("admin")])

    @workflow
    async def on_start(self, ctx, description: str):
        self.id = _cuid()
        self.description = description
        self.state = "pending"
        ctx.save()

        async with ctx.receive("approval") as decision:
            if not decision:  # timed out
                self.state = "expired"
                ctx.save()
                return
            self.state = "approved"
            ctx.save()
```

## Signals

A Signal is a typed entry point that generates an HTTP route and feeds into the workflow.

```python
# CREATE signal -- starts the workflow (POST /jobs/start)
start = Signal(kind=ActionKind.CREATE, policies=[role_is("admin")])

# Mid-workflow signal -- feeds into running workflow (POST /jobs/{id}/approval)
approval = Signal(policies=[role_is("admin")])

# System-only signal (POST /jobs/{id}/quote_received)
quote_received = Signal(policies=[system_only()])
```

### Signal policies

Policies on signals are checked before the signal dispatches:

```python
approval = Signal(policies=[role_is("admin", "operator")])
```

### Handler binding

The handler is found by convention: `on_{signal_name}`.

```python
start = Signal(kind=ActionKind.CREATE)

@workflow
async def on_start(self, ctx, description: str):
    ...
```

Or explicitly:

```python
start = Signal(kind=ActionKind.CREATE, handler=handle_start)

# or via decorator:
start = Signal(kind=ActionKind.CREATE)

@start.handler
async def handle_start(self, ctx, description: str):
    ...
```

### Signal input

The handler's parameters become the signal's input schema:

```python
@workflow
async def on_start(self, ctx, description: str, urgency: str = "routine"):
    ...
```

The generated route accepts `{"description": "...", "urgency": "..."}`.

## WorkflowContext

The `ctx` parameter gives access to durable primitives:

### ctx.save()

Persist current resource state. Durable -- won't re-execute on replay.

```python
self.state = "sourcing"
ctx.save()
```

### ctx.receive()

Pause the workflow and wait for an external signal. Use as async context manager:

```python
async with ctx.receive("quote_received") as quote:
    if not quote:
        # timed out
        return
    self.quote_amount = quote["amount"]
    ctx.save()
    quote.respond({"status": "received"})
```

Or with timeout:

```python
from datetime import timedelta

async with ctx.receive("approval", timeout=timedelta(days=7)) as decision:
    if not decision:
        self.state = "escalated"
        ctx.save()
        return
```

### Signal payload

The received signal carries the payload sent by the caller:

```python
async with ctx.receive("quote_received") as quote:
    amount = quote["amount"]           # dict-style access
    contractor = quote.get("name")     # .get() with default
    quote.respond({"ok": True})        # respond back to sender
```

### ctx.sleep()

Durable sleep:

```python
from datetime import timedelta, datetime, UTC

await ctx.sleep(duration=timedelta(hours=4))
await ctx.sleep(until=datetime(2024, 12, 1, tzinfo=UTC))
```

### ctx.deps

Access services registered via Providers:

```python
contractor = ctx.deps.contractor_service.find(self.branch_id)
```

### ctx.repo()

Access a repository for any resource:

```python
branch = ctx.repo(Branch).find_by_id(self.branch_id)
```

## Full lifecycle example

```python
class MaintenanceJob(Resource, Workflow):
    # ... fields ...

    start = Signal(kind=ActionKind.CREATE, policies=[role_is("admin")])
    quote_received = Signal(policies=[system_only()])
    approval = Signal(policies=[role_is("admin")])

    @workflow
    async def on_start(self, ctx, description: str, urgency: str, branch_id: str):
        self.id = _cuid()
        self.branch_id = branch_id
        self.description = description
        self.urgency = urgency
        self.state = "sourcing"
        ctx.save()

        # Wait for contractor quote (pause point 1)
        async with ctx.receive("quote_received") as quote:
            if not quote:
                self.state = "expired"
                ctx.save()
                return
            self.quote_amount = quote["amount"]
            self.state = "quote_approval"
            ctx.save()

        # Wait for operator approval (pause point 2)
        async with ctx.receive("approval") as decision:
            if not decision:
                self.state = "escalated"
                ctx.save()
                return
            if decision["action"] == "reject":
                self.state = "opened"
                ctx.save()
                return
            self.state = "approved"
            ctx.save()

        # Post-approval: call external services
        notify_contractor(self.contractor_name, self.id)
        push_to_crm(self.id, self.state, self.quote_amount)
```

This reads top-to-bottom as the full lifecycle story. Not a DAG definition. Not separate event handlers. One function.

## How it works under the hood

1. `Signal(kind=ActionKind.CREATE)` generates `POST /jobs/start`
2. When called, the framework starts a DBOS workflow
3. The workflow runs `on_start`, which calls `ctx.save()` (DBOS step) and `ctx.receive()` (DBOS recv)
4. `ctx.receive("quote_received")` suspends the workflow, waiting for a DBOS message
5. When `POST /jobs/{id}/quote_received` is called, the framework sends a DBOS message to the running workflow
6. The workflow resumes from where it paused, processes the quote, pauses again for approval
7. The full workflow state (which line of code it's on, all local variables) is durable across restarts

The developer never imports DBOS. The derive layer handles all wiring.
