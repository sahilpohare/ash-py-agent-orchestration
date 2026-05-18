# Ironbridge

Multitenant AI agent orchestration platform. Humans and agents collaborate in
durable, ordered threads. The orchestration layer survives restarts, enforces
tenant isolation structurally, and deduplicates messages at the storage layer.

---

## Quick start

```bash
# Install
uv venv && uv pip install -e ".[dev]"

# Configure
cp .env.example .env
# edit DATABASE_URL, OPENAI_API_KEY, etc.

# Start Restate server (Docker)
docker run --rm -p 8080:8080 -p 9070:9070 docker.restate.dev/restatedev/restate

# Run the Restate ASGI service (port 9080 — Restate calls in here)
uvicorn ironbridge.main:restate_app --port 9080

# Register services with Restate (once per deploy)
restate deployments register http://localhost:9080

# Run the public HTTP API (port 8000 — clients call in here)
uvicorn ironbridge.main:http_app --port 8000```
---

## Defining a resource

Everything is derived from a single declarative class. No hand-written
repositories, Restate handlers, or HTTP routes.

```python
# src/ironbridge/platform/domain/widget.py
from cuid2 import cuid_wrapper
from ironbridge.shared.framework import (
    Resource, CuidField, StringField, DateTimeField,
    action, ActionKind,
)

_cuid = cuid_wrapper()

class Widget(Resource):
    class Meta:
        table = "widgets"
        tenant_scoped = True    # Postgres RLS enforced automatically
        restate_object = True   # becomes a Restate VirtualObject

    id         = CuidField(primary_key=True)
    tenant_id  = StringField(nullable=False, index=True)
    name       = StringField(nullable=False)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)

    @action(kind=ActionKind.EXCLUSIVE)   # serialized per widget id
    def create(self, tenant_id: str, name: str) -> "Widget":
        self.id = _cuid()
        self.tenant_id = tenant_id
        self.name = name
        return self

    @action(kind=ActionKind.SHARED)      # concurrent reads OK
    def get(self) -> "Widget":
        return self

    @action(kind=ActionKind.STREAM)      # SSE endpoint
    def watch(self) -> "Widget":
        return self
```

Then register it in `main.py`:

```python
from ironbridge.platform.domain.widget import Widget  # import triggers registration
```

That's it. The framework derives:

| Artifact | Derived from |
|---|---|
| SQLAlchemy ORM model | field declarations |
| Upsert repository | ORM model |
| Restate `VirtualObject` | `Meta.restate_object = True` + `@action` methods |
| HTTP routes | `@action` methods |
| SSE endpoint | `@action(kind=ActionKind.STREAM)` |
| RLS policy (SQL) | `Meta.tenant_scoped = True` |

---

## Field types

```python
CuidField(primary_key=False, unique=False)
StringField(nullable=False, unique=False, index=False, default=None)
IntField(nullable=False, default=None)
BoolField(nullable=False, default=False)
DateTimeField(auto_now_add=False, auto_now=False, nullable=False, index=False)
EnumField(MyEnum, nullable=False)
JsonField(nullable=True, default=None)
HasMany("ResourceName", order_by=None)
ForeignKey("table.column", nullable=False, on_delete="CASCADE")
```

---

## Action kinds

| Kind | Restate handler | HTTP method | Use for |
|---|---|---|---|
| `EXCLUSIVE` | exclusive | `POST` | writes — serialized per object key |
| `SHARED` | shared | `POST` | reads — concurrent |
| `STREAM` | shared | `GET` (SSE) | real-time observation |

---

## HTTP API

All routes are derived automatically. The public API runs on `:8000`.

### Threads

```bash
# Create a thread
POST /threads/{thread_id}/create
X-Tenant-Id: tenant-abc
{"tenant_id": "tenant-abc"}

# Add a message (idempotent)
POST /threads/{thread_id}/add_message
X-Tenant-Id: tenant-abc
Idempotency-Key: client-generated-unique-key
{
  "participant_id": "alice",
  "role": "USER",
  "body": "Hello",
  "idempotency_key": "client-generated-unique-key"
}

# Get thread with full message history
POST /threads/{thread_id}/get
X-Tenant-Id: tenant-abc
{"tenant_id": "tenant-abc"}

# Subscribe to real-time updates (SSE)
GET /threads/{thread_id}/observe
```

### Participants

```bash
# Register a human
POST /participants/{id}/create
{"tenant_id": "tenant-abc", "type": "HUMAN", "name": "Alice"}

# Register an agent
POST /participants/{id}/create
{
  "tenant_id": "tenant-abc",
  "type": "AGENT",
  "name": "researcher-agent",
  "config": {"model": "gpt-4o", "instructions": "You are a research assistant."}
}
```

### Tenants & Users

```bash
POST /tenants/{id}/create        {"name": "Acme", "slug": "acme"}
POST /tenants/{id}/suspend       {}
POST /users/{id}/create          {"tenant_id": "...", "email": "...", "name": "...", "role": "OWNER"}
POST /users/{id}/change_role     {"new_role": "ADMIN"}
POST /users/{id}/deactivate      {}
```

---

## Idempotency

Pass an `Idempotency-Key` header on any `POST`. Two layers enforce exactly-once:

1. **Restate (24h)** — same key returns the cached response, handler never
   re-executes.
2. **DB (permanent)** — `UNIQUE(thread_id, idempotency_key)` with
   `ON CONFLICT DO NOTHING` catches replays after the Restate cache expires.

```bash
POST /threads/{id}/add_message
Idempotency-Key: msg-2026-alice-001
{"participant_id": "alice", "role": "USER", "body": "ping", "idempotency_key": "msg-2026-alice-001"}
```

Retrying the exact same request 20 times produces exactly one persisted message.

---

## Multi-tenant isolation

Tenant isolation is **structural** — not a filter added by application code.

Every tenant-scoped resource sets `Meta.tenant_scoped = True`. The framework
generates the RLS policy:

```sql
ALTER TABLE threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE threads FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON threads
    USING (tenant_id = current_setting('app.tenant_id', true));
```

Every DB connection is opened via `tenant_session(tenant_id)`:

```python
with tenant_session("tenant-abc") as db:
    # SET LOCAL app.tenant_id = 'tenant-abc' is executed automatically
    # All queries on this connection are RLS-filtered to tenant-abc
    # A raw SELECT * FROM threads returns only tenant-abc rows
    ...
```

A developer who forgets a `WHERE tenant_id = ?` clause cannot leak data — the
database enforces the boundary.

---

## Durability model

```
Client POST → HTTP API (:8000) → Restate (:8080) → Handler (:9080) → Postgres
```

- **Restate** journals which steps ran and their outputs. On crash + restart,
  completed steps are skipped; incomplete steps retry from where they left off.
- **Postgres** is the source of truth. Restate holds no business state.
- **All writes are upserts** (`INSERT ... ON CONFLICT DO UPDATE`), so a
  replayed handler never produces duplicate-key errors.

Kill the server mid-request with `kill -9`. On restart, every thread is
recoverable from Postgres. The next message to an existing thread continues
seamlessly.

---

## Real-time observation

```bash
# Subscribe — streams messages as they arrive
GET /threads/{thread_id}/observe

# Two observers on the same thread both receive every message in order
curl http://localhost:8000/threads/thread-123/observe &
curl http://localhost:8000/threads/thread-123/observe &
```

Internally proxied to Restate's `/restate/invocation/{id}/attach` SSE stream.
Clients never see Restate URLs or invocation IDs.

---

## Project structure

```
src/ironbridge/
├── shared/
│   ├── db.py                   # SQLAlchemy engine + tenant_session()
│   ├── framework/
│   │   ├── fields.py           # Field types
│   │   ├── actions.py          # @action decorator + ActionKind
│   │   ├── resource.py         # Resource base + ResourceMeta
│   │   └── registry.py         # Global resource registry
│   └── derive/
│       ├── orm.py              # Resource → SQLAlchemy model
│       ├── repository.py       # Generic upsert repository
│       ├── restate.py          # Resource → Restate VirtualObject
│       └── http.py             # Resource → Starlette routes + SSE
└── platform/
    ├── domain/
    │   ├── tenant.py           # Tenant resource
    │   ├── user.py             # User resource (OWNER | ADMIN | MEMBER)
    │   ├── participant.py      # Participant (HUMAN | AGENT | SYSTEM)
    │   ├── thread.py           # Thread aggregate (add_message, observe)
    │   └── message.py          # Message entity (position, idempotency_key)
    └── main.py                 # Wires registry → Restate app + HTTP app
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/ironbridge` | Postgres connection string |
| `RESTATE_URL` | `http://localhost:8080` | Restate server base URL |
| `APP_HOST` | `0.0.0.0` | Host for the Restate ASGI service |
| `APP_PORT` | `9080` | Port for the Restate ASGI service |
