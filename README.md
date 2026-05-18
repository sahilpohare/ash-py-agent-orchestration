# Ironbridge

Multitenant AI agent orchestration platform. Humans and agents collaborate in
durable, ordered threads. The orchestration layer survives restarts, enforces
tenant isolation structurally, and deduplicates messages at the storage layer.

Channels are views of threads — every channel (web, CLI, webhook, WhatsApp)
sees the same thread events and decides what to render.

---

## Achievements

### Zero-boilerplate DDD framework — inspired by Ash

Ironbridge ships a meta-programming framework (`shared/framework/`) that derives an entire backend from a single declarative class — no hand-written routes, no manual repository code, no Restate handler wiring.

Inspired by [Ash](https://ash-hq.org) (Elixir's declarative resource framework), adapted to Python + SQLAlchemy + Restate.

**One class produces everything:**

```python
class Thread(Resource):
    class Meta:
        tenant_scoped  = True   # ← Postgres RLS policy, tenant_id injected
        restate_object = True   # ← Restate VirtualObject, one handler per action

    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String, primary_key=True)

    @action(kind=ActionKind.ACTION)
    def add_message(self, action_ctx: ActionContext, ...) -> Message:
        # pure domain logic — no imports from Restate, SQLAlchemy, or FastAPI
        action_ctx.send_workflow("AgentRun", key=run_id, arg={...})
        return msg
```

What gets derived at startup, zero additional code:

| Artifact | Source |
|---|---|
| SQLAlchemy ORM model | column `Mapped[]` declarations |
| `tenant_id` column + RLS policy | `Meta.tenant_scoped = True` |
| Upsert repository (`SqlAlchemyRepository`) | ORM model |
| Restate `VirtualObject` + handler per action | `Meta.restate_object = True` + `@action` |
| Exclusive vs shared concurrency | `ActionKind` (CREATE/UPDATE/DESTROY/ACTION → exclusive, READ/STREAM → shared) |
| Implicit `repo.save()` on CREATE/UPDATE | `ActionKind.implicit_save` |
| Implicit `repo.delete()` on DESTROY | `ActionKind.implicit_delete` |
| Effect execution (workflow starts, sends) | `ActionContext` — zero Restate imports in domain |

**Key design properties:**

- **Domain has no infrastructure imports.** Actions return domain objects and declare effects via `ActionContext`. They never touch Restate, SQLAlchemy sessions, or HTTP clients.
- **`ResourceMeta` is a SQLAlchemy metaclass extension.** It intercepts class creation, collects `@action` methods, injects tenancy columns, and registers the class — before SQLAlchemy processes the table definition.
- **Effects are data, not calls.** `ActionContext` collects `SendEffect` and `WorkflowEffect` objects. Infrastructure (`derive/restate.py`) executes them via `ctx.generic_send` and `ctx.workflow_send` after the DB write — atomically, journaled by Restate.
- **Tenant isolation is structural.** `tenant_id` is never declared in domain code. The metaclass injects it as a `server_default` backed by `current_setting('app.tenant_id')` — enforced by Postgres RLS, not application filters.
- **Idempotency at the storage layer.** All saves are `INSERT ... ON CONFLICT DO UPDATE`. Restate replay re-executes the same action — the upsert makes it safe without any application-level guard.

---

## Quick Start

```bash
# Install
uv venv && uv pip install -e ".[dev]"

# Configure
cp .env.example .env
# edit PUSHER_*, CEREBRAS_API_KEY, etc.

# Start everything (migrations + registration run automatically)
podman compose up -d

# Open the UI
open http://localhost:9080
```

The UI is served directly from the app at `:9080`.

---

## Architecture Overview

```
Browser
  │
  ├── GET  /                   → frontend/index.html (static)
  ├── POST /api/{tenant}/channels/web/bind  → WebAdapter (create channel, bind thread)
  ├── POST /api/{tenant}/channels/web/send  → WebAdapter → Restate Thread
  │
  └── Pusher subscription      ← WebAdapter pushes outbound messages

Restate :8080
  └── /Thread/{id}/add_message → Thread VirtualObject
        ├── write message to Postgres
        ├── enqueue AgentRun workflow (if HUMAN message)
        └── fan out to ChannelDelivery (Service) → ctx.run → adapter.on_message()

AgentRun Workflow (durable, one-shot)
  └── agent.run(ctx)
        ├── fetch history from Postgres
        ├── call LLM
        ├── request_approval() → HITL suspend/resume via named promise
        └── write_message() → Thread.add_message
```

---

## Testing

### Test layout

```
tests/
├── platform/                   Unit tests — no DB, no Restate, no HTTP
│   ├── agents/                 AgentRegistry, AgentContext, StubAgent helpers
│   ├── channels/               Adapter registry, channel_type contract
│   ├── identity/               Tenant, User domain model
│   └── sessions/               Thread, Message domain model, content schema
└── integration/                Require running postgres (and optionally Restate)
    ├── test_tenant_isolation.py  RLS — all 7 tenant-scoped tables
    ├── test_recording_adapter.py RecordingAdapter contract + StubAgent LLM helpers
    ├── test_stub_agent.py        Full agent run via HTTP API (requires live stack)
    ├── test_idempotency.py       Duplicate message dedup
    ├── test_ordering.py          Message position ordering
    └── test_observable.py        Web channel bind/send endpoints
```

### Running tests

```bash
# Unit tests only (no infrastructure needed)
uv run pytest tests/platform/ -v

# Integration tests (requires postgres running)
uv run pytest tests/integration/test_tenant_isolation.py \
               tests/integration/test_recording_adapter.py -v

# Full integration suite (requires postgres + restate + app)
uv run pytest tests/integration/ -v

# All tests
uv run pytest tests/ -v
```

### Methodology

Every test states its contract explicitly:

- **Preconditions** — what must be true before the test runs (state, inputs)
- **Invariants** — properties that must hold throughout
- **Postconditions** — what must be true after the operation completes

```python
def test_create_does_not_overwrite_preset_id():
    """
    Pre:  Thread.id already set — simulates derive/restate.py doing
          `instance.id = ctx.key()` before calling create()
    Inv:  create() MUST NOT overwrite the pre-set id (ADR-7)
    Post: t.id unchanged after create()
    """
    t = Thread()
    t.id = "pre-set-id-from-restate"
    t.create()
    assert t.id == "pre-set-id-from-restate"
```

### Test tiers

| Tier | What it tests | Infrastructure |
|---|---|---|
| **Unit** (`tests/platform/`) | Domain model logic, pure functions, registry contracts | None |
| **DB integration** | RLS isolation, upsert idempotency, position ordering | Postgres only |
| **Stack integration** | Full agent run, channel delivery, HITL | Postgres + Restate + app |

### What is tested against each decision

Key architectural decisions (ADRs in `docs/decisions.md`) have corresponding tests:

| ADR | Decision | Test file |
|---|---|---|
| ADR-2 | All writes are upserts — `_content_key` determinism and exclusions | `test_message_schema.py` |
| ADR-3 | RLS enforced on all 7 tenant-scoped tables; fails closed; cross-tenant write blocked | `test_tenant_isolation.py` |
| ADR-5 | `UNIQUE(thread_id, idempotency_key)` declared on `Message`; caller key stored unchanged | `test_message_schema.py` |
| ADR-7 | `create()` never overwrites a pre-set id | `test_message_schema.py` |
| ADR-13 | All 8 part types parse; unknown types dropped silently; `version` field preserved | `test_message_schema.py` |
| ADR-21 | `import ironbridge.agents.stub` registers `"stub"` in module-level singleton | `test_stub_agent.py` |
| ADR-25 | `register_adapter(MyAdapter())` at module level triggers self-registration on import | `test_adapter_registry.py` |

### Adapter testing without a live stack

Use `RecordingAdapter` to test channel delivery logic in-process:

```python
from tests.channel_adapter_stub import RecordingAdapter
from ironbridge.platform.channels.message import ChannelMessage, TextPart

adapter = RecordingAdapter.install()  # singleton — safe to call repeatedly
adapter.clear()

msg = ChannelMessage.from_dict({
    "thread_id": "t1",
    "participant_id": "alice",
    "participant_type": "HUMAN",
    "role": "USER",
    "content": {"version": 1, "parts": [{"type": "text", "text": "hello"}]},
})
adapter.on_message(msg, {}, ctx)

received = adapter.received(thread_id="t1")
assert len(received) == 1
assert isinstance(received[0].parts[0], TextPart)
```

`RecordingAdapter` records every `on_message()` call, supports thread-id filtering, and returns copies (mutations don't affect internal state).

---

## Defining a Resource

Everything is derived from a single declarative class.

```python
from cuid2 import cuid_wrapper
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from ironbridge.shared.framework import Resource, ActionKind, action

_cuid = cuid_wrapper()

class Widget(Resource):
    class Meta:
        tenant_scoped  = True   # Postgres RLS enforced automatically
        restate_object = True   # becomes a Restate VirtualObject

    __tablename__ = "widgets"

    id   : Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    name : Mapped[str] = mapped_column(String, nullable=False)

    @action(kind=ActionKind.EXCLUSIVE)
    def create(self, name: str) -> "Widget":
        self.id = _cuid()
        self.name = name
        return self

    @action(kind=ActionKind.SHARED)
    def get(self) -> "Widget":
        return self
```

Import it in `main.py` — that's it. The framework derives:

| Artifact | Derived from |
|---|---|
| SQLAlchemy ORM model | column declarations |
| Upsert repository | ORM model |
| Restate `VirtualObject` | `Meta.restate_object = True` + `@action` |
| RLS policy | `Meta.tenant_scoped = True` |

---

## Writing a Channel Adapter

A channel is a view of a thread. Every message written to a thread — user, assistant, system events, HITL cards — is fanned out to every bound channel. The adapter decides what to render.

### 1. Implement the adapter

```python
# services/channels/adapters/myservice.py
from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage, TextPart
from ironbridge.platform.channels.registry import register_adapter
from services.channels.adapters.base import BaseChannelAdapter


class MyAdapter(BaseChannelAdapter):
    channel_type = "myservice"           # must be unique; matches Channel.channel_type in DB

    def on_message(
        self,
        message: ChannelMessage,
        config: dict,                    # Channel.config from DB (credentials, settings)
        ctx: ChannelContext,             # write-back handle to the thread
    ) -> None:
        # Filter to what this channel cares about
        if message.role != "ASSISTANT":
            return
        text = " ".join(p.text for p in message.parts if isinstance(p, TextPart))
        if not text:
            return
        # Deliver to your service using config["api_key"], config["webhook_url"], etc.
        import httpx
        httpx.post(config["webhook_url"], json={"text": text}, timeout=10)


# Self-register — must be at module level so import triggers registration
register_adapter(MyAdapter())
```

### 2. Register by importing in main.py

```python
# src/ironbridge/main.py
from services.channels.adapters.myservice import MyAdapter  # noqa: F401 — registers "myservice"
```

That's it for outbound. The adapter now receives every thread event.

### 3. Create the Channel record (one per tenant, at setup time)

The adapter is code-level. The `Channel` record is the DB-level configuration for a specific tenant's use of that channel type. Create it once:

```bash
POST http://localhost:8080/Channel/{channel_id}/create
{
  "tenant_id": "tenant-a",
  "user_name": "admin",
  "name": "My Service",
  "channel_type": "myservice",
  "config": {"webhook_url": "https://...", "api_key": "..."},
  "default_agent_id": "stub"
}
```

Or create it programmatically in your adapter's setup route.

### 4. Bind a thread to the channel

A `ChannelBinding` connects a specific thread to a specific channel. Without a binding, no messages are delivered:

```bash
POST http://localhost:8080/ChannelBinding/{binding_id}/create
{
  "tenant_id": "tenant-a",
  "user_name": "admin",
  "thread_id": "thread-xyz",
  "channel_id": "channel-abc"
}
```

Bindings are idempotent — the upsert makes duplicate calls safe.

### 5. Inbound messages (optional)

For channels with inbound HTTP (e.g. webhooks, browser clients), implement `get_router()` returning a FastAPI `APIRouter`. `main.py` mounts it automatically via:

```python
_adapter = get_adapter("myservice")
if _adapter and hasattr(_adapter, "get_router"):
    fastapi_app.include_router(_adapter.get_router())
```

Inside the route, call `self.receive(...)` which posts to Restate ingress:

```python
def get_router(self) -> APIRouter:
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
    router = APIRouter(prefix="/api")

    @router.post("/{tenant_id}/channels/myservice/inbound")
    async def inbound(tenant_id: str, body: dict, request: Request) -> JSONResponse:
        # validate, extract text, call self.receive(...)
        self.receive(
            content={"version": 1, "parts": [{"type": "text", "text": body["text"]}]},
            thread_id=body["thread_id"],
            tenant_id=tenant_id,
            participant_id=body["user_id"],
            idempotency_key=body.get("message_id"),
        )
        return JSONResponse({"ok": True})

    return router
```

### 6. Write-back to the thread from the adapter

`ChannelContext` lets adapters inject messages back into the thread without going through HTTP:

```python
def on_message(self, message: ChannelMessage, config: dict, ctx: ChannelContext) -> None:
    # Send a system event back to the thread
    ctx.send_event("MESSAGE_DELIVERED", delivery_id="dlv-123")

    # Or send a text message
    ctx.send_message("Your message was delivered.")
```

### Channel adapter checklist

- [ ] `channel_type` class attribute set — unique string, matches DB `Channel.channel_type`
- [ ] `register_adapter(MyAdapter())` at module level (not inside `if __name__ == "__main__"`)
- [ ] Import in `main.py` to trigger registration
- [ ] `Channel` record created in DB for each tenant that uses this channel
- [ ] `ChannelBinding` created to connect threads to the channel
- [ ] `on_message` never raises — log and continue on delivery failures
- [ ] No Restate ctx ops inside `ctx.run()` — `ChannelContext` constructed before the run block (ADR-33)

---

## Writing an Agent

```python
# services/agents/my_agent.py
from ironbridge.platform.agents.base import BaseAgent
from ironbridge.platform.agents.context import AgentContext
from ironbridge.platform.agents.registry import agent_registry

@agent_registry.register("my_agent")
class MyAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> None:
        history = await ctx.step("fetch_history", ctx.get_history)

        # Optional HITL
        reply = await ctx.request_approval(
            prompt="Should I proceed?",
            options=[{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
        )
        if reply["selected"][0] != "yes":
            return

        await ctx.write_message({"version": 1, "parts": [{"type": "text", "text": "Done."}]})
```

Import in `main.py`. No Restate imports in agent code.

---

## HTTP API

Core thread operations go through Restate ingress (`:8080`).
Browser-facing endpoints go through FastAPI (`:9080/api/`).

### Thread (via Restate)

```bash
# Create
POST http://localhost:8080/Thread/{thread_id}/create
{"tenant_id": "tenant-a", "user_name": "alice"}

# Add message
POST http://localhost:8080/Thread/{thread_id}/add_message
{"participant_id": "alice", "participant_type": "HUMAN", "role": "USER",
 "content": {"version": 1, "parts": [{"type": "text", "text": "Hello"}]},
 "idempotency_key": "msg-001", "tenant_id": "tenant-a", "user_name": "alice",
 "agent_id": "weather"}

# Get
POST http://localhost:8080/Thread/{thread_id}/get
{"tenant_id": "tenant-a", "user_name": "alice"}
```

### Web Channel (via FastAPI)

```bash
# Bind thread to web channel (idempotent)
POST /api/{tenant}/channels/web/bind
X-Tenant-Id: tenant-a
X-User-Name: alice
{"thread_id": "thread-xyz"}

# Send inbound message from browser
POST /api/{tenant}/channels/web/send
X-Tenant-Id: tenant-a
X-User-Name: alice
{"thread_id": "thread-xyz", "text": "Hello", "participant_id": "alice", "agent_id": "weather"}
```

---

## Multi-Tenant Isolation

Isolation is structural — not a filter added by application code.

```sql
-- Set once per connection
SET LOCAL app.tenant_id = 'tenant-abc';

-- Policy on every tenant-scoped table
CREATE POLICY tenant_isolation ON threads
    USING (tenant_id = current_setting('app.tenant_id', true));
```

```python
with tenant_session("tenant-abc") as db:
    # All queries on this connection return only tenant-abc rows
    repo = SqlAlchemyRepository(db, Thread)
    threads = repo.list()   # no WHERE clause needed
```

---

## HITL (Human-in-the-Loop)

HITL is message-driven — part of the thread timeline, not a side-channel.

1. Agent calls `ctx.request_approval(prompt, options)` → writes `response_request` part → workflow suspends
2. UI renders the approval card
3. Human clicks → `response_reply` part written → named promise resolved → workflow resumes

```python
reply = await ctx.request_approval(
    prompt="Call get_weather for London?",
    context={"tool": "get_weather", "args": {"city": "London"}},
    options=[
        {"id": "approve", "label": "Approve"},
        {"id": "reject",  "label": "Reject"},
    ],
)
if reply["selected"][0] == "approve":
    result = get_weather("London")
```

---

## Project Structure

```
src/ironbridge/
├── shared/
│   ├── db.py                    tenant_session(), SQLAlchemy engine
│   ├── framework/               Resource, @action, ActionKind, registry
│   └── derive/
│       ├── restate.py           Resource → Restate VirtualObject
│       ├── restate_workflow.py  AgentRun Workflow
│       └── repository.py        SqlAlchemyRepository
└── platform/
    ├── identity/                Tenant, User
    ├── sessions/                Thread, Message
    ├── agents/                  BaseAgent, AgentContext, AgentRegistry, HITL
    └── channels/                Channel, ChannelBinding, ChannelDelivery,
                                 ChannelContext, ChannelMessage, adapter registry

services/
├── agents/                      Concrete agent implementations
└── channels/
    └── adapters/                Concrete channel adapters (web, cli, webhook)

frontend/
└── index.html                   Single-file UI (Pusher, HITL cards)

alembic/
└── versions/                    DB migrations
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `RESTATE_URL` | Restate server base URL (default: `http://localhost:8080`) |
| `PUSHER_APP_ID` | Pusher app id |
| `PUSHER_KEY` | Pusher key (also used in frontend JS) |
| `PUSHER_SECRET` | Pusher secret |
| `PUSHER_CLUSTER` | Pusher cluster (default: `eu`) |
| `CEREBRAS_API_KEY` | Cerebras API key (used by WeatherAgent) |

---

## Development Notes

- **New thread IDs after code changes** — Restate journals are tied to handler
  code. Changing handler logic and reusing a thread ID causes journal mismatch.
  Use a new thread ID or run `podman compose down -v` to clear journals + DB.
- **Rebuild after Python changes** — `podman compose build app && podman compose up -d app` — registration runs automatically on startup
- **`TerminalError` must be re-raised** — never swallow it in Restate handlers.
- **No HTTP calls inside `ctx.run()` callbacks** — they re-execute on replay. Call `httpx`/`_call_add_message` after `await ctx.run(...)` returns, never inside the lambda.
- **No Restate ctx ops inside `ctx.run()` callbacks** — `ChannelContext.send_message/send_event` use `ctx.generic_send`. Construct `ChannelContext` before calling `ctx.run()`.
- **No `Any` types in adapter interfaces** — use concrete Pydantic types.
- **No raw SQL in domain or adapter code** — use `SqlAlchemyRepository`.
