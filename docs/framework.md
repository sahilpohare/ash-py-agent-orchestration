# Ironbridge Framework — Developer Reference

The framework is a thin layer over SQLAlchemy and Restate. It adds three
things and nothing else:

1. `class Meta` — declares tenant isolation and Restate binding
2. `@action` — marks a method as a durable, routable handler
3. A global registry — maps resource names to classes at import time

Everything else — ORM schema, migrations, Restate `VirtualObject` handlers —
is **derived** from those declarations at startup.

---

## Resource

A `Resource` subclass IS a SQLAlchemy model. Columns are declared exactly as
you would in any SQLAlchemy project. No custom field types, no wrappers.

```python
from datetime import datetime, timezone
from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from ironbridge.shared.framework import Resource, ActionKind, action

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(timezone.utc)

class Widget(Resource):
    class Meta:
        tenant_scoped  = True   # generates RLS policy, enforces via tenant_session()
        restate_object = True   # generates a Restate VirtualObject keyed by id

    __tablename__ = "widgets"

    id         : Mapped[str]      = mapped_column(String, primary_key=True, default=_cuid)
    name       : Mapped[str]      = mapped_column(String, nullable=False)
    created_at : Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at : Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @action(kind=ActionKind.EXCLUSIVE)
    def create(self, name: str) -> "Widget":
        self.id = _cuid()
        self.name = name
        return self

    @action(kind=ActionKind.SHARED)
    def get(self) -> "Widget":
        return self
```

`tenant_id` is injected automatically when `tenant_scoped = True` — never
declare it in the domain model.

### Meta options

| Option | Type | Default | Effect |
|---|---|---|---|
| `tenant_scoped` | `bool` | `False` | Generates Postgres RLS policy. All queries filtered automatically. |
| `restate_object` | `bool` | `False` | Derives a Restate `VirtualObject`. Each `@action` becomes a handler. |

---

## Actions

`@action` marks a method as a durable handler.

```python
@action(kind=ActionKind.EXCLUSIVE)   # serialized per object key — writes
def create(self, name: str) -> "Widget": ...

@action(kind=ActionKind.SHARED)      # concurrent — reads
def get(self) -> "Widget": ...
```

### ActionKind

| Kind | Restate handler | Concurrency | Use for |
|---|---|---|---|
| `EXCLUSIVE` | exclusive | serialized per key | writes, state mutations |
| `SHARED` | shared | concurrent | reads |

### Action body rules

- **Return `self`** — the framework upserts the return value automatically.
- **No I/O inside actions** — the framework wraps the call in `ctx.run()` and
  `tenant_session()`. Don't open DB connections or call external services.
- **No raw SQL** — use `SqlAlchemyRepository` or domain action methods.
- **Pure domain logic** — validate, mutate, return. The framework handles persistence.

```python
# Good
@action(kind=ActionKind.EXCLUSIVE)
def deactivate(self) -> "Widget":
    if self.status == "INACTIVE":
        raise ValueError("Already inactive")
    self.status = "INACTIVE"
    return self

# Bad — I/O in action body
@action(kind=ActionKind.EXCLUSIVE)
def deactivate(self) -> "Widget":
    db.execute(...)      # don't — framework handles persistence
    requests.post(...)   # don't — use a separate step
    return self
```

---

## SqlAlchemyRepository

Generic upsert repository for any `Resource` subclass.

```python
from ironbridge.shared.db import tenant_session
from ironbridge.shared.derive.repository import SqlAlchemyRepository

with tenant_session("tenant-abc") as db:
    repo = SqlAlchemyRepository(db, Widget)

    # Find
    w = repo.find_by_id("widget-123")
    w = repo.find_by(name="foo")
    ws = repo.list(status="ACTIVE")

    # Save (always upsert — safe to call multiple times)
    widget = Widget()
    widget.create(name="foo")
    repo.save(widget)
    db.commit()
```

`save()` issues `INSERT ... ON CONFLICT DO UPDATE`. Safe for Restate replay.

---

## Channel Adapters

Every channel integration extends `BaseChannelAdapter`.

```python
from services.channels.adapters.base import BaseChannelAdapter
from ironbridge.platform.channels.registry import register_adapter
from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage, TextPart

class MyAdapter(BaseChannelAdapter):
    channel_type = "myservice"   # must match Channel.channel_type in DB

    def on_message(
        self,
        message: ChannelMessage,
        config: dict,
        ctx: ChannelContext,
    ) -> None:
        # Called for every thread message. Filter by role/type.
        if message.role != "ASSISTANT":
            return
        text = " ".join(p.text for p in message.parts if isinstance(p, TextPart))
        # send text to myservice via config["api_key"] etc.

register_adapter(MyAdapter())
```

Import the adapter in `main.py` — the `register_adapter()` call at module level
triggers self-registration.

### BaseChannelAdapter

| Method | Required | Description |
|---|---|---|
| `on_message(message, config, ctx)` | yes | Called for every thread message |
| `receive(content, thread_id, tenant_id, participant_id, ...)` | inherited | Post inbound message to Restate thread |
| `get_router()` | optional | Return a FastAPI `APIRouter` for inbound HTTP |

### ChannelMessage

Pydantic model with discriminated `parts`:

| Part type | Description |
|---|---|
| `TextPart` | plain text |
| `TextDeltaPart` | streaming chunk |
| `StreamEndPart` | streaming complete |
| `EventPart` | lifecycle event (AGENT_RUN_QUEUED, FAILED, RETRY, ORPHANED, etc.) |
| `ResponseRequestPart` | HITL approval prompt |
| `ResponseReplyPart` | HITL reply |
| `ToolCallPart` | tool invocation |
| `ReasoningPart` | chain-of-thought |

### ChannelContext

```python
ctx.send_message("text")          # fire-and-forget text to thread
ctx.send_event("MY_EVENT", **kw)  # fire-and-forget system event to thread
```

---

## Tenant Isolation

All DB access goes through `tenant_session()`:

```python
with tenant_session("tenant-abc") as db:
    # SET LOCAL app.tenant_id = 'tenant-abc' runs automatically
    repo = SqlAlchemyRepository(db, Widget)
    widgets = repo.list()   # RLS returns only tenant-abc rows
```

Resources with `tenant_scoped = False` (e.g. `Tenant`) are not RLS-filtered.

---

## Registration

Resources register at import time via the metaclass. Adapters register via
`register_adapter()` at module level. Import everything in `main.py` before
the registry is consumed.

```python
# main.py
from ironbridge.platform.sessions.thread import Thread    # registers Thread resource
from services.channels.adapters.web import WebAdapter     # registers web adapter
from services.agents.stub import StubAgent                # registers stub agent

from ironbridge.shared.framework import registry
from ironbridge.shared.derive.restate import derive_virtual_object

restate_services = [
    derive_virtual_object(cls)
    for cls in registry.all_resources().values()
    if cls.__meta__.get("restate_object")
]
```

---

## File Map

```
src/ironbridge/
├── shared/
│   ├── db.py                    engine, SessionLocal, tenant_session()
│   ├── framework/
│   │   ├── __init__.py          exports: Resource, action, ActionKind, registry
│   │   ├── resource.py          Resource base + ResourceMeta (extends SA DeclarativeBase)
│   │   ├── actions.py           @action decorator, ActionKind enum
│   │   └── registry.py          global dict of Resource subclasses
│   └── derive/
│       ├── restate.py           Resource → restate.VirtualObject
│       ├── restate_workflow.py  AgentRun Workflow + HITL wiring
│       └── repository.py        generic SqlAlchemyRepository
└── platform/
    ├── identity/
    │   ├── tenant.py            Tenant (not RLS-scoped)
    │   └── user.py              User (OWNER | ADMIN | MEMBER)
    ├── sessions/
    │   ├── thread.py            Thread aggregate
    │   └── message.py           Message (position, idempotency_key)
    ├── agents/
    │   ├── base.py              BaseAgent ABC
    │   ├── context.py           AgentContext
    │   ├── registry.py          agent_registry
    │   ├── hitl.py              HITL named promises
    │   └── agent_run_event.py   lifecycle events
    └── channels/
        ├── channel.py           Channel resource
        ├── channel_binding.py   ChannelBinding resource
        ├── delivery.py          ChannelDelivery VirtualObject
        ├── registry.py          adapter registry (register_adapter / get_adapter)
        ├── context.py           ChannelContext
        └── message.py           ChannelMessage + part types

services/
├── agents/                      concrete agent implementations
└── channels/
    └── adapters/
        ├── base.py              BaseChannelAdapter ABC + receive()
        ├── web.py               WebAdapter (Pusher + FastAPI)
        ├── cli.py               CliAdapter (stdout + REPL)
        └── webhook.py           WebhookAdapter (HTTP POST)
```

---

## What the Framework Does NOT Do

- No validation framework — use `raise ValueError(...)` in action bodies
- No auth framework — enforce in FastAPI middleware or adapter `get_router()`
- No migration runner — use Alembic directly (`alembic upgrade head`)
- No raw SQL in domain code — use `SqlAlchemyRepository` or `find_by()`
- No `Any` types in adapter interfaces — use concrete Pydantic types

---

## Restate Safety Rules

| Rule | Reason |
|---|---|
| No HTTP calls inside `ctx.run()` callbacks | Re-executes on replay — duplicate side effects |
| No `ctx.*` operations inside `ctx.run()` callbacks | Restate forbids nested ctx access |
| Construct `ChannelContext` before `ctx.run()` | `ChannelContext` uses `ctx.generic_send` — a ctx operation |
| `RetryableError` caught outside `ctx.run()`, not inside | Catching inside the callback prevents proper re-raise to Restate |
| `_serialize()` skips relationship collections | Prevents 413 from oversized journal/Pusher payloads |
