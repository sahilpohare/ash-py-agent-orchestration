# Reddit Post: Ash.py + Ironbridge Agentic Platform

**Title: I built Ash.py (a resource framework for Python) and an agentic AI platform on top of it — full architecture writeup**

---

I've been building **Ironbridge**: a multitenant AI agent orchestration platform where humans and agents collaborate in durable, ordered conversation threads. It has two distinct layers worth unpacking separately.

---

## Part 1: Ash.py — a Python resource framework

If you've used [Ash](https://ash-hq.org) in Elixir, you know the idea: declare a resource once, derive everything from it. I wanted that for Python.

The observation: most of the code in a backend service is glue — wiring domain objects to HTTP, databases, and message queues. If the domain model is expressive enough, that glue can be derived.

**One class, many artifacts:**

```python
class Thread(Resource):
    class Meta:
        tenant_scoped  = True   # → Postgres RLS policy, tenant_id column injected
        restate_object = True   # → Restate VirtualObject, one handler per action

    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String, primary_key=True)

    @action(kind=ActionKind.ACTION)
    def add_message(self, action_ctx: ActionContext, content: dict, ...) -> Message:
        # pure domain logic
        action_ctx.send_workflow("AgentRun", key=run_id, arg={...})
        return msg

    @action(kind=ActionKind.READ)
    def get(self) -> ThreadView: ...
```

From this class the framework derives:

| Artifact | Source |
|---|---|
| SQLAlchemy ORM model | `Mapped[]` column declarations |
| `tenant_id` column + Postgres RLS policy | `Meta.tenant_scoped = True` |
| Upsert repository | ORM model |
| Restate VirtualObject + typed handler per action | `Meta.restate_object = True` + `@action` |
| Exclusive vs shared Restate concurrency | `ActionKind` |
| Effect execution (workflow starts, message sends) | `ActionContext` |

**`ResourceMeta` (the metaclass)** runs at class definition time and does three things: injects `tenant_id` for tenant-scoped resources, collects `@action`-decorated methods into `cls.__actions__`, and registers the class globally so the derive layer can find it.

**`ActionKind` is the key abstraction:**

| Kind | DB op | Restate concurrency |
|---|---|---|
| `CREATE` | INSERT + auto-save | exclusive |
| `UPDATE` | UPSERT + auto-save | exclusive |
| `DESTROY` | DELETE | exclusive |
| `ACTION` | domain controls | exclusive |
| `READ` | none | shared |
| `STREAM` | none | shared |

**Side effects without infrastructure imports.** Domain code declares effects via `ActionContext` — no Restate, no SQLAlchemy, no HTTP:

```python
@action(kind=ActionKind.ACTION)
def add_message(self, action_ctx: ActionContext, ...) -> Message:
    msg = Message(...)
    action_ctx.send_workflow("AgentRun", key=run_id, arg={...})  # start agent
    action_ctx.send_after("ChannelDelivery", "deliver", key=channel_id,
                          factory=lambda result: {..., "position": result["position"]})
    return msg
```

`send_after` is the interesting one: the arg factory runs *after* the DB write completes, so position-assigned fields are available. The derive layer picks up effects and fires Restate sends atomically after `ctx.run()`. Effects are data, not calls.

**Tenant isolation is structural, not a filter.** `tenant_scoped = True` means:
- `tenant_id` column auto-injected with `server_default = current_setting('app.tenant_id', true)`
- Every session runs `SET LOCAL app.tenant_id = :tid` before any query
- Postgres RLS enforces it at the DB layer — a missing `WHERE` clause returns zero rows, not all rows

**All writes are upserts.** `INSERT ... ON CONFLICT DO UPDATE` everywhere. Safe for Restate replay — the journal records that a step ran, not what it produced. On replay the DB write is skipped.

**`derive_virtual_object`** is fully generic — zero domain knowledge. It reads `cls.__actions__` and generates a Restate VirtualObject with correct concurrency, position counters, idempotency, and effect execution.

---

## Part 2: Durable AI agents built on top of it

With that foundation, I built an agentic execution layer. Design goal: **agent code has zero infrastructure imports**.

**`BaseAgent`** has one method:

```python
class MyAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> None:
        history = await ctx.step("fetch_history", ctx.get_history)
        response = await ctx.step("llm_call_0", lambda: call_llm(history))
        ctx.write_message(response, message_count=0)
```

Every `ctx.step()` is a Restate-journaled durable step. Crash mid-LLM-call, restart, replay picks up where it left off. No re-billing for completed steps.

**`AgentContext` API:**

| Method | What it does |
|---|---|
| `ctx.step(name, fn)` | Durable step — journaled, cancel-checked before running |
| `ctx.run(name, fn)` | Durable step — journaled, no cancel check (setup/teardown) |
| `ctx.get_history()` | Fetch thread from Postgres, strips control messages |
| `ctx.write_message(content)` | Fire-and-forget write to thread queue |
| `ctx.request_approval(prompt, options)` | Suspend workflow, show HITL card |
| `ctx.call(tool, **kwargs)` | Run a tool as a durable step, with optional HITL gate |

**Automatic cancellation.** `ctx.step()` peeks a "cancel" durable promise before every step. Call `cancel()` from outside (e.g. when a new user message arrives) and the agent exits cleanly at the next step boundary. The runner catches `AgentCancelledError` and marks the run `CANCELLED`. Agents implement zero cancellation logic.

**Pydantic transparency.** Restate journals JSON. If your step function returns a Pydantic model, the framework serializes to dict for journaling, reads the return type annotation, and reconstructs the model. Your agent sees typed objects throughout.

**Error classification.** HTTP 4xx → `TerminalError` → Restate stops retrying immediately. HTTP 5xx → retryable. Agent raises normal exceptions; the framework classifies.

**Human-in-the-loop without a side channel.** HITL is part of the thread timeline, not a separate system:

```python
choice = await ctx.request_approval(
    prompt="Which report format?",
    options=[
        {"id": "pdf", "label": "PDF"},
        {"id": "csv", "label": "CSV"},
        {"id": "cancel", "label": "Cancel"},
    ],
    timeout=timedelta(hours=24),
)
if choice.timed_out or choice.selected[0] == "cancel":
    return
generate_report(format=choice.selected[0])
```

Under the hood: agent suspends on a named durable promise (`hitl:{request_id}`), a `response_request` message appears in the thread, the human replies with a `response_reply` message, `Thread.add_message` sees it, looks up the `run_id`, fires `resolve_hitl` on the AgentRun workflow, which resolves the promise. Agent resumes. The whole thing goes through the same message log — same idempotency, same ordering guarantees as every other message. No webhooks, no polling, no side-channel state.

Timeouts are a `restate.select` race between the HITL promise and `ctx.sleep(24h)`.

**New message auto-cancels active run.** If a run is suspended on HITL and the user sends a new message, `_enqueue_run` cancels the active workflow, drains the queue, and fires a fresh run. One active run per thread, ever. The user can answer the HITL question by typing in the message box — the next run reads full history and can re-ask if needed.

**Dead run recovery.** After a Restate purge, `active_run_id` may be set in VirtualObject state for a workflow that no longer exists. `_enqueue_run` checks run status via `ctx.workflow_call` before assuming a run is active. If dead, clears state and fires immediately. No permanent queue block.

**Orphan detection.** If `resolve_hitl` is called for a run that's already done (completed/failed/cancelled), it checks workflow state, writes `AGENT_RUN_ORPHANED` to the thread, and returns. Silent failures don't exist — everything is observable in the message log.

**Run queuing.** The `_enqueue_run` / `_run_done` handler pair on the Thread VirtualObject manages the one-active-run-per-thread invariant in durable Restate state. Not an external queue.

**Retry surfacing.** On `RetryableError`, `AgentContext.step()` writes `AGENT_RUN_RETRY` to the thread before re-raising. A retrying agent is visible in the UI, not silent.

---

## Architecture summary

```
Browser POST /api/{tenant}/channels/web/send
  → WebAdapter → Restate ingress POST /Thread/{id}/add_message
    → Thread VirtualObject (derive/restate.py)
      → INSERT message, assign position
      → [HUMAN message] → _enqueue_run → AgentRun Workflow
      → [ALL messages]  → ChannelDelivery → adapter.on_message()

AgentRun Workflow
  → agent_registry.resolve(agent_id)
  → agent.run(AgentContext)
    → ctx.step() → durable LLM call
    → ctx.request_approval() → suspend on named promise
    [human replies] → Thread.add_message → resolve_hitl → resume
    → ctx.write_message() → Thread.add_message
```

**Stack:** Python, FastAPI, Restate, Postgres (RLS), Alembic, Hypercorn (HTTP/2 required for Restate), Podman Compose.

The thing I'm most satisfied with: the framework layer (`shared/`) and the agent layer (`platform/agents/`) are genuinely decoupled — neither knows the other's internals. A new `Resource` gets Restate durability, idempotency, tenant isolation, and effect routing for free. A new `BaseAgent` gets crash recovery, HITL, cancellation, retry surfacing, and run queuing for free. And all of it is derived from the domain model at startup — no hand-written glue.

45 ADRs logged if anyone wants to go deep on specific decisions.
