# Architectural Decisions

A running log of decisions made, why, and what changed.

---

## 1. Postgres as source of truth, Restate as execution scheduler

**Decision:** Restate is infrastructure — same category as Postgres, Redis, S3. It owns execution coordination (durability, retry, serialization). Postgres owns all domain data. No domain or framework layer imports from `restate`.

**Reasoning:** Two sources of truth create drift. A crash between a Restate journal write and a DB write leaves them inconsistent with no clean recovery path.

**Consequence:** All `ctx.run()` steps wrap idempotent DB operations. The journal records that a step ran — not what it produced. On replay the DB write is skipped (journaled result returned).

**Restate ctx allowed only for execution coordination:**
- `"position"` — write-through cache of `MAX(position)`, recovers from Postgres on cold start

---

## 2. All writes are upserts

**Decision:** `SqlAlchemyRepository.save()` always issues `INSERT ... ON CONFLICT DO UPDATE`, never a bare `INSERT`.

**Reasoning:** Restate replays handlers on crash. A bare `INSERT` raises a duplicate-key error on replay. An upsert is safe.

**Consequence:** Every resource must have a stable primary key before `save()`. CUIDs are generated in the domain action, not by the DB.

---

## 3. Tenant isolation is structural, not a filter

**Decision:** Postgres Row Level Security enforced via `SET LOCAL app.tenant_id` on every connection. No `WHERE tenant_id = ?` in application queries.

**Reasoning:** Application-layer filters fail open. RLS fails closed — a missing filter returns zero rows.

**Evolution:**
- Started with `filter_by(tenant_id=...)` in every repository method.
- Moved to `tenant_session()` + RLS policies — one enforcement point.

---

## 4. Tenancy key injected by framework, not declared in domain

**Decision:** Resources with `Meta.tenant_scoped = True` have `tenant_id` injected by `ResourceMeta.__new__()`. Domain files never declare it.

**Reasoning:** `tenant_id` is an infrastructure concern — it exists to satisfy RLS, not to model a business concept.

---

## 5. Idempotency key is caller-supplied

**Decision:** `Message.idempotency_key` is explicit, supplied by the caller. `ON CONFLICT (thread_id, idempotency_key) DO NOTHING` at the DB layer.

**Two-layer idempotency:**
1. Restate: same `Idempotency-Key` header → cached response, handler never re-runs (24h window).
2. DB: `UNIQUE(thread_id, idempotency_key)` → permanent backstop after cache expires.

**Evolution:**
- Started with caller-supplied key.
- Moved to content hash to remove it from domain API.
- Reverted: content hash deduplicates identical content across different senders — wrong semantics.

---

## 6. Resource IS the SQLAlchemy model

**Decision:** `Resource` inherits from SQLAlchemy `DeclarativeBase`. Columns declared with `Mapped[]` + `mapped_column()` directly.

**Evolution:**
- Built `fields.py` with `CuidField`, `StringField`, etc. and `derive/orm.py` to emit SQLAlchemy models.
- Recognized this was rebuilding SQLAlchemy poorly. Deleted both.

---

## 7. Restate VirtualObject key = resource primary key

**Decision:** The VirtualObject key for a resource is its primary key. `derive/restate.py` sets `instance.id = ctx.key()` before every handler — domain `create()` must not overwrite a pre-set id.

**Consequence:** `position` assignment is safe without DB-level locking because Restate serializes concurrent `add_message` calls for the same thread.

---

## 8. Public API is Restate ingress; browser-facing endpoints are FastAPI

**Decision:** Core thread/agent operations go through Restate ingress (`:8080`). Browser-facing endpoints (bind, send, auth-required ops) live behind FastAPI (`:9080`) under `/api/`.

**Reasoning:** Restate ingress has no auth layer. Channel adapters that own inbound HTTP (e.g. `WebAdapter`) register their FastAPI routes via `get_router()`, mounted on the FastAPI app.

**Consequence:** HTTP/2 required for Restate. The app runs under `hypercorn`.

---

## 9. Sessions and agents are separate subdomains

**Decision:**
- `platform/identity/` — User, Tenant (auth, roles, lifecycle)
- `platform/sessions/` — Thread, Message (conversation, ordering)
- `platform/agents/` — Agent (definition), AgentRun (execution), HITL
- `platform/channels/` — Channel, ChannelBinding, ChannelDelivery (fanout)

**Reasoning:** Different lifecycles, different authors, different concerns.

**Participant vs User:**
- `User` — real person with auth concerns.
- `Participant` — conversation actor. Free string `participant_id` on `Message`. No FK to users.

---

## 10. Agent execution uses Restate Workflow, not VirtualObject

**Decision:** `AgentRun` is a Restate `Workflow` (one-shot, durable). `Agent` is a VirtualObject (definition, mutable config).

**Reasoning:** An agent run has a clear start and end. Workflow gives a single durable execution with cancel handle and status query.

**Cancellation:** `cancel` handler resolves a durable promise. The main loop checks after each step. No mid-LLM-call interruption — current step completes, then loop exits.

---

## 11. Agent messages write through Thread.add_message

**Decision:** All agent output — text responses, tool calls, HITL prompts — goes through `Thread.add_message`. No direct DB writes from the workflow.

**Reasoning:** Position consistency. The VirtualObject queue serializes all writers.

---

## 12. HITL is message-driven, not a side-channel

**Decision:** HITL interaction is regular `add_message` calls with structured content types — `response_request` and `response_reply`. No separate HTTP endpoint.

**Options model:** `options: null` = free text, `[{id, label}]` = single select, `multi_select: true` = multi-select.

---

## 13. Content uses versioned parts model

**Decision:** `Message.content` is `{"version": 1, "parts": [...]}`. Part type drives rendering and infrastructure behavior.

**Evolution:** `format` → `version`.

---

## 14. Position via ctx cache + DB fallback

**Decision:** `Message.position` = `ctx.get("position") + 1`, with `MAX(position)` recovery on cold start.

**Why not DB sequence per thread:** DDL at runtime, schema management nightmare.
**Why not timestamps:** Clock skew can invert order.
**Why not vector clocks:** Single writer degenerates to scalar counter.

---

## 15. AgentRunEvent is a separate table, not in messages

**Decision:** `agent_run_events` table stores workflow lifecycle events (RUNNING, COMPLETED, CANCELLED, FAILED). Not in `messages`.

**Reasoning:** Lifecycle events are operational metadata, not conversation content.

*(Superseded by Decision 20 on tenant scoping.)*

---

## 16. Working memory deferred

**Decision:** No working memory implementation.

**Rejected:** `ctx.get/set("working_memory")` — domain data in infra state, lost on Restate wipe.
**Future:** `thread_state` table or external service (Supermemory).

---

## 17. Pydantic models for workflow I/O

**Decision:** `AgentRunRequest` and `AgentRunResult` are Pydantic `BaseModel`, not dataclasses.

**Reasoning:** Restate's SDK auto-selects `PydanticJsonSerde` for Pydantic models. Dataclasses deserialized as plain dicts — `.thread_id` raises `AttributeError`.

---

## 18. Migrations are autogenerated

**Decision:** `alembic revision --autogenerate` against a live DB. RLS policies in `0001` are the only manual exception.

---

## 19. HITL reconciliation for orphaned awakeables

**Decision:** After Restate restart, awakeables created before the restart are dead. `HITL.request_response()` checks Postgres for an existing `response_reply` before suspending. If found, returns immediately without waiting.

**Protocol:** `ctx.run("check_existing_reply:{request_id}", _find_existing_reply)` — journaled, idempotent on replay.

---

## 20. AgentRunEvents are tenant-scoped with RLS

**Decision:** `agent_run_events` has `tenant_id` + RLS policy. Supersedes Decision 15.

**Migration:** `a1b2c3d4e5f6` adds the column, enables RLS, creates the policy.

---

## 21. Pluggable agent architecture — BaseAgent / AgentContext / AgentRegistry

**Decision:** Three primitives in `platform/agents/`:
- `BaseAgent(ABC)` — `async def run(self, ctx: AgentContext) -> None`
- `AgentContext` — wraps Restate `WorkflowContext`, exposes domain methods
- `agent_registry` — maps `agent_id` string → `BaseAgent` subclass

**Agent implementations** live in `services/` (not platform). Platform is infrastructure. `services/agents/` holds concrete agents (stub, weather, etc.).

**Registration:** import side-effect in `main.py` triggers `agent_registry.register(...)`.

---

## 22. ConversationWorkflow deferred — Restate WorkflowSharedContext limitation

**Decision:** Revert from single `ConversationWorkflow` per thread back to Thread VirtualObject + AgentRun Workflow.

**Root cause:** Restate SDK `0.17.x` shared handler `ctx.get/set` fails when handler input is a Pydantic model — SDK tries to serialize input during journal bookkeeping.

**Future:** Re-evaluate with SDK `0.18.x`.

---

## 23. X-Tenant-Id header auth for browser-facing FastAPI routes

**Decision:** FastAPI routes under `/api/` require `X-Tenant-Id` matching the URL `{tenant_id}`. Restate ingress routes are internal — no auth layer there.

---

## 24. Thread is the model, Channel is the view

**Decision:** Every channel adapter receives every thread event (not just ASSISTANT messages). Adapters decide what to render. Thread owns the canonical message log; channels are projections of it.

**Reasoning:** Email, Slack, CLI all render the same thread differently. The thread does not know about channels. Channels observe the thread.

**Implementation:** `Thread.add_message` sends every message to `ChannelDelivery` (Restate Service, stateless + concurrent). `ChannelDelivery` wraps the DB lookup and `adapter.on_message(message, config, ctx)` in a single `ctx.run()` — journaled, deduplicated on retry. `ChannelContext` is constructed outside `ctx.run()` so adapters can call `ctx.generic_send` (a Restate ctx operation) without violating the no-ctx-ops-inside-ctx.run rule.

---

## 25. Channel adapter strategy pattern — BaseChannelAdapter

**Decision:** Every channel integration extends `BaseChannelAdapter` (in `services/channels/adapters/base.py`):
- `on_message(message, config, ctx)` — outbound: called for every thread message
- `receive(...)` — inbound: posts to Restate ingress
- `get_router()` — optional: returns a FastAPI `APIRouter` for inbound HTTP endpoints

**Reasoning:** Channels like WhatsApp, Email, CLI, Web all differ in transport but share the same contract. A class hierarchy enforces the contract without HTTP proxy boilerplate.

**Adapter implementations** live in `services/channels/adapters/`. They are NOT part of `platform/channels/` — platform owns the infrastructure primitives (delivery, registry, context, message types). Concrete adapters are service-layer concerns.

**Registration:** `register_adapter(instance)` at module import. `main.py` imports each adapter — the import triggers self-registration.

---

## 26. WebAdapter owns both directions of the web channel

**Decision:** `WebAdapter` in `services/channels/adapters/web.py` owns:
- Outbound: Pusher trigger on `on_message()`
- Inbound: FastAPI routes `/api/{tenant}/channels/web/bind` and `/api/{tenant}/channels/web/send` via `get_router()`

**Reasoning:** The web channel is a self-contained integration. A separate proxy controller (`thread_controller.py`) was creating split ownership — the adapter had the Pusher logic but an unrelated controller had the bind logic.

**Consequence:** `thread_controller.py` stripped to an empty router. `main.py` mounts `WebAdapter.get_router()` directly. No raw SQL in adapter — uses `Channel.create()` + `SqlAlchemyRepository`.

---

## 27. No raw SQL in domain or adapter code

**Decision:** Domain actions use `@action` + `SqlAlchemyRepository`. Adapters that need DB access use `SqlAlchemyRepository.find_by()` and domain actions. Raw `db.execute(text(...))` is forbidden outside of RLS setup and aggregate queries with no domain equivalent.

**Reasoning:** Raw SQL bypasses the domain model — it cannot be replayed safely, it doesn't participate in the framework's upsert semantics, and it breaks the derivation contract.

---

## 28. No `Any` type in domain or adapter interfaces

**Decision:** All method signatures use concrete types. `ChannelMessage`, `ChannelContext`, `dict`, typed Pydantic models — never `Any`.

**Reasoning:** `Any` defeats type checking at the most important boundary. Channel adapters are a public contract; the compiler must enforce it.

---

## 29. Pusher is web infrastructure, not core

**Decision:** Pusher is used exclusively in `WebAdapter`. Removed from `restate.py` and any shared layer.

**Reasoning:** Pusher is a browser transport. CLI, WhatsApp, email channels do not use it. Placing Pusher in core `restate.py` tied all channels to a web-specific dependency.

**Evolution:** Pusher emit was originally in `restate.py` after every `add_message`. Moved to `WebAdapter.on_message()`.

---

## 30. ChannelContext gives adapters write-back capability

**Decision:** `ChannelContext` is passed to every `on_message()` call. It exposes:
- `send_message(text)` — fire-and-forget text to thread
- `send_event(event, **kwargs)` — fire-and-forget system event to thread

**Reasoning:** Adapters sometimes need to acknowledge receipt or inject system messages (e.g. "message delivered", "rate limited"). They must go through the same `Thread.add_message` path — no direct DB writes.

---

## 32. AGENT_RUN_RETRY events surfaced to thread on RetryableError

**Decision:** When `AgentContext.step()` catches a `RetryableError` propagating out of `ctx.run()`, it calls `_write_retry_event()` before re-raising. This writes an `AGENT_RUN_RETRY` system message to the thread via `_call_add_message`.

**Reasoning:** Without this, a retrying agent run is silent from the UI's perspective — the run appears to hang. Surfacing retries lets the user see that work is in progress and why it's delayed.

**Idempotency:** The key is `sha256(run_id:retry:step_name:int(time()))[:16]`. Using wall-clock seconds gives distinct keys across retries while being stable enough to avoid duplicates within the same retry window. These are best-effort notifications — not lifecycle state.

**Rule:** `_call_add_message` (HTTP) is called *outside* `ctx.run()`. Retryable errors propagate from `await ctx.run(name, fn)` — caught in `step()` after the durable boundary, not inside the callback.

---

## 33. No HTTP calls inside ctx.run() callbacks

**Decision:** `ctx.run()` callbacks must be pure functions. No `httpx`, no `_call_add_message`, no Restate ctx operations inside them.

**Reasoning:** On Restate replay, `ctx.run()` callbacks re-execute. HTTP calls inside callbacks fire again on every replay, defeating durable execution semantics. Even if idempotency keys protect against data corruption, the extra network calls add latency and can cause false retries.

**Affected fixes:**
- `restate_workflow.py`: `write_error_message` step removed — `_call_add_message` now called directly after `await ctx.run("mark_failed", ...)`.
- `restate_workflow.py`: `write_orphaned_message` step removed — `_write_orphaned_message` called directly after `await ctx.run("write_orphaned_event:...", ...)`.
- `context.py`: `_write_retry_event` called in `except RetryableError` on the `await ctx.run(...)` return, not inside `_guarded()`.
- `delivery.py`: `ChannelContext` constructed before `ctx.run("deliver", ...)`, not inside the callback.

---

## 34. _serialize skips relationship collections

**Decision:** `_serialize()` in `derive/restate.py` no longer recurses into SQLAlchemy relationship collections (one-to-many). It returns `None` for missing scalar relations, `_serialize(val)` for scalar relations, and skips collections entirely.

**Reasoning:** Recursing into collections causes the entire related graph to be serialized into the Restate journal and Pusher payload. This caused a Pusher 413 error. Collections are not needed in handler return values — they are not part of the domain action contract.

---

## 35. Agent implementations and channel adapters are in `services/`, not `platform/`

**Decision:**
- `services/agents/` — concrete agent implementations (stub, weather, etc.)
- `services/channels/adapters/` — concrete channel adapters (web, cli, webhook)
- `src/ironbridge/platform/` — infrastructure primitives only (base classes, registries, delivery, context)

**Reasoning:** Platform is infrastructure. It should contain no business logic and no concrete implementations. `services/` is where integrations live. This mirrors the separation between a framework and applications built on it.

**pyproject.toml:** Both `src/ironbridge` and `services` are declared as packages so both are on the Python path.

---

## 36. DeferredSendEffect for post-action effects that need the action result

**Decision:** `ActionContext.send_after(service, handler, key, factory)` queues a `DeferredSendEffect`. The `factory` callable receives the serialized result dict from `ctx.run()` and builds the effect arg at execution time.

**Reasoning:** `SendEffect` args are constructed before `ctx.run()` executes — before position is assigned. The first attempt patched position in `_execute_effects` by sniffing `effect.service == "ChannelDelivery"`, which violated domain boundaries. `DeferredSendEffect.factory(result)` is called *after* `ctx.run()` returns with the correct result, so position (or any other computed field) is available without `restate.py` knowing anything about `ChannelDelivery`.

**Consequence:** `thread.py` uses `send_after` with a `_deliver_arg` factory. `restate.py` calls `factory(result)` generically.

---

## 37. Message insert uses ON CONFLICT (thread_id, idempotency_key) DO NOTHING

**Decision:** `Message.Meta` sets `conflict_columns = ("thread_id", "idempotency_key")` and `conflict_action = "nothing"`. The repository issues `ON CONFLICT (thread_id, idempotency_key) DO NOTHING`.

**Reasoning:** After Restate purge, replay generates a new message `id` but the same `idempotency_key`. `ON CONFLICT (id) DO UPDATE` violated the unique constraint on `(thread_id, idempotency_key)`. `DO NOTHING` on the natural key is correct — if the message already exists in the DB (source of truth), skip silently.

**Consequence:** `ResourceMeta.__new__` was extended to parse `conflict_columns` and `conflict_action` from `Meta`. The repository checks for these before falling back to the PK upsert path.

---

## 38. Dead run recovery in _enqueue_run via Restate status API

**Decision:** Before queuing a new run, `_enqueue_run` calls `GET /AgentRun/{active_run_id}/status` via Restate ingress. If the status is not `"running"`, it clears `active_run_id` and `pending_runs` and fires immediately.

**Reasoning:** After Restate purge, `active_run_id` is set in Thread VirtualObject state but the workflow no longer exists. `_run_done` will never fire. Without this check the thread queue is permanently blocked. DB `agent_run_events` is a reflection of Restate state, not the authority — checking the DB run state would be wrong.

**Consequence:** Restate is the owner of run lifecycle. The status check adds one HTTP call per `_enqueue_run` invocation when a run is active, but this is inside `ctx.run()` so it is journaled and not repeated on replay.

---

## 39. ctx.generic_send replaces httpx.post for add_message in workflow handlers

**Decision:** `restate_workflow.py` uses `ctx.generic_send` + `AddMessageRequest.model_dump_json()` instead of `_call_add_message` (which used `httpx.post`).

**Reasoning:** `httpx.post` from inside a Restate workflow handler causes a deadlock-like timeout — the workflow is executing while trying to call back into the Restate ingress synchronously. This caused workflows to pause and `_run_done` to never fire, permanently blocking the thread queue.

**Consequence:** All message writes from workflow handlers go through `ctx.generic_send`. `_call_add_message` and `_write_orphaned_message` helpers were removed.

---

## 40. LLM provider via env vars

**Decision:** `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`, and `OPENROUTER_API_KEY` control the LLM. LiteLLM prefix conventions apply: `cerebras/<model>`, `openrouter/<provider>/<model>`.

**Note:** The plain weather agent (`weather_agent.py`) uses the raw OpenAI client — it strips the `openrouter/` prefix and sets `base_url` to OpenRouter manually. LiteLLM is not used there.

---

## 41. New human message auto-cancels the active run

**Decision:** In `_enqueue_run`, when a run is active and `"running"`, call `cancel()` on the active `AgentRun` workflow via `ctx.workflow_send` before clearing state and firing the new run. The pending queue is also drained.

**Reasoning:** If a run is suspended on a HITL promise and the user sends a new message, they are signalling intent to move on. Keeping the HITL run alive blocks the queue indefinitely. The user can re-answer the question by typing in the message box — the next run will re-read the full thread history and re-ask any disambiguation if needed.

**Consequence:** Only one `AgentRun` is ever active per thread at a time and the queue never grows beyond depth 1. The cancelled run's `AgentCancelledError` path fires cleanly, `_run_done` is sent, but since `active_run_id` is already cleared by the time it arrives, it is a no-op. `reply.approved` / `reply.selected` are never resolved for abandoned HITL promises — the workflow exits without resolving them, which is safe.

---

## 42. Dead run recovery uses workflow_call, cancel uses workflow_send

**Decision:** The status check in `_enqueue_run` uses `ctx.workflow_call(status_fn, ...)` (awaited, journaled) instead of `httpx.get`. The cancel uses `ctx.workflow_send(cancel_fn, ...)` (fire-and-forget, journaled).

**Reasoning:** HTTP calls inside Restate handlers must go through `ctx.run()` to be journaled. Using the SDK's `workflow_call`/`workflow_send` is already durable and avoids the extra `ctx.run()` wrapper. It also removes the dependency on `httpx` from this path entirely.

---

## 43. HITL supports arbitrary multiple-choice options

**Decision:** `ctx.request_approval(options=[...])` accepts any list of `{id, label}` pairs. Callers use `reply.selected[0]` to read the result, not `reply.approved` (which is only `True` for id `"approve"` or `"yes"`).

**Reasoning:** Binary approve/deny is too limiting. Location disambiguation, report format selection, and similar choices need N options. The HITL mechanism is generic — the option IDs are arbitrary strings.

**Consequence:** All multi-choice HITL consumers must check `reply.timed_out` and `reply.selected`, never `reply.approved`. `StubAgent` demonstrates this pattern: messages containing "choose", "pick", or "options" trigger a 4-option card.

---

## 44. Many-to-many thread ↔ channel bindings

**Decision:** `ChannelBinding` uses `UNIQUE(thread_id, channel_id)` — one thread can be bound to multiple channels (e.g. web UI + Discord). `resolve_channels_for_thread` returns `list[str]`. Thread.add_message fans out to all bound channels.

**Reasoning:** A thread is the canonical conversation. Multiple channel surfaces (web, Discord, Slack) may need to observe the same thread. A `UNIQUE(thread_id)` constraint would prevent this.

**Consequence:** The fanout loop in `Thread.add_message` uses a closure-capture fix (`cid: str = _channel_id` default arg) to avoid the classic Python loop-capture bug.

---

## 45. BaseChannelAdapter provides lifecycle helpers

**Decision:** `get_or_create_channel`, `new_thread`, and `bind_thread` live on `BaseChannelAdapter`, not on individual adapters.

**Reasoning:** Every adapter needs the same DB operations to provision its channel record and manage thread bindings. Duplicating this logic per adapter was error-prone.

**Consequence:** Adapters call `self.get_or_create_channel(tenant_id)` on first use (idempotent). `new_thread` creates a Restate Thread and binds it in one call. `bind_thread` uses `UNIQUE(thread_id, channel_id)` — safe to call on every inbound request.

---

## 46. Actor carries full flow context, not just identity

**Decision:** `Actor` is a frozen dataclass with identity (id, tenant_id, role, scopes), origin (channel, source_type, source_id, ip, idempotency_key), and a chain (`on_behalf_of: Actor | None`). It threads through the entire execution: policies, guards, agent runs, channel deliveries, audit logs.

**Reasoning:** A webhook handler creates the initial Actor. When it kicks off an agent, the agent gets a derived Actor (`actor.as_agent("scheduling")`) whose `on_behalf_of` points back to the webhook Actor. The chain lets any point in the flow answer: "Who started this? Through what channel? On what resource?"

**Consequence:** `actor.initiator` walks the chain to the root. Policies like `initiator_is("admin")` check the human behind an agent action, not the agent itself. `actor.to_dict()` serializes the full chain for audit logs.

**Constructors:** `from_request()` (JWT/session), `from_webhook()` (Twilio/Nylas/Stripe), `from_cron()` (scheduled jobs). Derivation: `as_agent()`, `as_system()`, `with_source()`.

---

## 47. Policies and guards are separate from actions

**Decision:** Policies (authorization: who) and guards (preconditions: what state) are attached to actions via `@policy()` and `@guard()` decorators. They are pure functions with no DB or I/O. Enforcement runs policies first, then guards.

**Reasoning:** In Ash, policies and actions are orthogonal. We follow the same principle. An action body contains only domain logic. Authorization and precondition checks are declarative, composable, and testable in isolation.

**Consequence:**
- `@policy(role_is("admin", "operator"))` -- checked before the action runs. DENY -> 403.
- `@guard(in_state("quote_approval"), field_set("quote_amount"))` -- checked after policies pass. Fail -> 409.
- `enforce(actor, resource, action_fn)` runs both. `can()` returns bool without raising.
- Built-in policies: `role_is`, `same_tenant`, `system_only`, `has_scope`, `anyone`, `initiator_is`.
- Built-in guards: `in_state`, `not_in_state`, `not_deleted`, `field_set`, `field_equals`, `field_true`, `custom`.

---

## 48. Default actions via default_action() and Meta.default_actions

**Decision:** Resources can get standard CRUD actions without writing the body. Two mechanisms:

1. `Meta.default_actions` -- `True` (all five), `["create", "get", "list"]` (pick), or `False` (none).
2. `default_action(kind, policies=..., guards=...)` -- explicit declaration with custom policies/guards.

Both can coexist. Explicit `default_action()` declarations override Meta defaults.

**Default policies for Meta-injected actions:**
- Writes (create/update/delete): `role_is("admin", "operator", "system")`. Plus `same_tenant()` if tenant_scoped.
- Reads (get/list): `anyone()`. Plus `same_tenant()` if tenant_scoped.
- Update/delete also get `not_deleted()` guard.

**Reasoning:** Most resources need the same CRUD. Writing five trivial action bodies per resource is boilerplate. But the authorization profile differs: some resources are admin-only to create, some are system-only to delete. `default_action()` lets you customize per-action without rewriting the body.

**Override:** Defining a method with the same name in the Resource class shadows the default. The metaclass skips injection if the name already exists in the namespace.

---

## 49. Three-layer package structure: ironbridge / ironbridge_web / services

**Decision:** One repo, three packages with clear dependency direction:
- `ironbridge` -- framework primitives (Resource, Actor, policies, guards, enforcement, Thread, Channel, Agent). No HTTP, no business logic.
- `ironbridge_web` -- web layer (FastAPI router derivation, middleware, actor resolution, error handlers). Knows FastAPI but not the business domain.
- `services` -- domain layer (Call, Enquiry, MaintenanceJob, Lead, Viewing, channel adapters, PMS connectors). Imports from both.

**Dependency direction:** `services/ -> ironbridge_web/ -> ironbridge/`. `ironbridge/` imports from nothing external.

**Reasoning:** The framework should be testable without FastAPI. The web layer should be reusable across domains. The domain should be swappable without touching infrastructure.

---

## 50. Derive layer generates FastAPI routes from Resource actions

**Decision:** `derive_router(ResourceCls)` in `ironbridge_web` reads a Resource's `__actions__` and generates a FastAPI `APIRouter` with one route per action. Route shape follows REST conventions:

| ActionKind | Route |
|---|---|
| CREATE | `POST /{resources}` |
| READ (get) | `GET /{resources}/{id}` |
| READ (list) | `GET /{resources}` |
| READ (named) | `GET /{resources}/{id}/{action_name}` |
| UPDATE (update) | `PATCH /{resources}/{id}` |
| UPDATE (named) | `POST /{resources}/{id}/{action_name}` |
| DESTROY | `DELETE /{resources}/{id}` |
| ACTION | `POST /{resources}/{id}/{action_name}` |

Each generated handler: resolves Actor, loads resource from DB, calls `enforce()`, invokes the action, saves the result.

**Reasoning:** Hand-writing route handlers per resource is the bulk of boilerplate in Labs v1. Deriving them eliminates it while keeping full control via policies, guards, and custom actions.

**No Restate dependency.** This is a direct FastAPI derive, not a Restate proxy. Restate derivation (`derive/restate.py`) remains a separate, optional path.

---

## 51. Workflow extends Resource with Signals and durable handlers

**Decision:** Two base classes: `Resource` (CRUD, sync) and `Workflow(Resource)` (signals, async, durable). A Workflow IS a Resource that also accepts Signals and has `on_` handlers.

**Resource:** Fields, relationships, actions (sync request/response), policies, guards. Pure data with behavior. No external I/O in action bodies.

**Workflow:** Everything Resource has, plus Signal declarations and `on_{signal_name}` async handlers. Handlers receive a `WorkflowContext` with `save()`, `receive()`, `sleep()`, `emit()`. Handlers delegate to domain services for business logic.

**Consequence:** The test is simple. Does it have a state machine or long-running process? Workflow. Is it just data you read and write? Resource.

---

## 52. Signals are not Actions

**Decision:** Actions are synchronous request/response (caller waits, gets result, 200). Signals are asynchronous fire-and-forget (caller gets 202, workflow handles it when ready). Both generate HTTP routes. Both have policies. Both have typed input (introspected from handler signature). Different execution model.

**Signal declarations:** Class attributes on a Workflow. `open = Signal(kind=ActionKind.CREATE, policies=[...])`. The `on_open` method is the handler, matched by convention (`on_` + signal name).

**Signal transport:** Pluggable via `register_signal_transport()`. DBOS uses `DBOS.send()/recv()`. Could be a message bus, WebSocket, or plain async. The domain doesn't know the transport.

**Programmatic send:** `MaintenanceJob.approval.send(job_id, payload, actor=actor)`. Same enforcement as HTTP.

---

## 53. Input/output schema introspected from method signatures

**Decision:** The `@action` decorator and Signal handlers introspect the method signature at class creation time to build Pydantic input models. Three styles detected automatically:

- No params (besides self/ctx) -> no request body
- Single `BaseModel` param -> validate through that model
- Plain typed params -> auto-generate a Pydantic model from the signature

Output: if return type is a `BaseModel` subclass, serialize through it. Otherwise serialize all resource fields.

**Reasoning:** NestJS-style. The method signature IS the API contract. No separate DTO classes unless you want custom validation (use a BaseModel param). No config. Type hints drive everything.

**Performance:** Introspection runs once at import time. Generated Pydantic models are compiled by pydantic-core (Rust). Zero per-request overhead.

---

## 54. DBOS for durable workflow execution

**Decision:** DBOS (a Python library, not a sidecar) provides durable workflow execution. It checkpoints workflow state in Postgres system tables. On crash, workflows resume from the last completed step.

**Wiring:** The derive layer applies `@DBOS.workflow()` to `on_` handlers and maps WorkflowContext methods to DBOS primitives:
- `ctx.receive(signal)` -> `DBOS.recv(topic, timeout)`
- `ctx.save()` -> `@DBOS.step()` that persists the resource
- `ctx.sleep(duration)` -> `DBOS.sleep(seconds)`
- `Signal.send(id, payload)` -> `DBOS.send(workflow_id, payload, topic)`

**Scale:** 20K concurrent workflows. Most are suspended (rows in Postgres, zero resources). Shared connection pool (~20 connections), not one per workflow. DBOS queues provide concurrency control for burst scenarios.

**No lock-in:** DBOS is optional. Without it, `ctx.receive` polls a DB table, `ctx.save` is a plain DB write, `ctx.sleep` is `asyncio.sleep`. Same handler code, less durability.

---

## 55. Three-package structure: ironbridge / lightwork / lightwork_web

**Decision:** Supersedes Decision 49.

- `ironbridge/` -- the framework. Resource, Workflow, Signal, Actor, policies, guards, enforcement, relationships, graph. No HTTP, no business logic, no external dependencies.
- `lightwork/` -- the domain app. Resources, workflows, services, connectors, channels, subscriptions. Imports from ironbridge.
- `lightwork_web/` -- the web entry point. FastAPI app, route derivation, middleware (actor resolution, error handlers), startup wiring. Imports from both.

**Dependency direction:** `lightwork_web/ -> lightwork/ -> ironbridge/`. `ironbridge/` imports nothing.

**Analogy:** ironbridge = Ash. lightwork = your Phoenix app contexts. lightwork_web = your Phoenix endpoint + router.

---

## 56. Domain services hold business logic, workflows are thin

**Decision:** Workflow `on_` handlers are thin. They delegate to domain service classes for business logic. Domain services are plain Python classes with injected dependencies (connectors, repos).

```
Resource  = what it IS (data, relationships, policies)
Workflow  = what happens TO it (signals, thin handlers, ctx.save)
Service   = what it DOES (business logic, external calls)
```

**Reasoning:** The handler should be readable in 5 lines. "Load, delegate, save." The service is where the real logic lives. Services are testable with mock dependencies. Handlers are so thin they barely need tests.

**DI:** Services are constructed at startup in `create_services()` with their connectors. Stored on `app.state.services`. Workflow handlers access them via `ctx.services`. No DI container, no framework.

---

## 57. Connectors are plain clients, not framework abstractions

**Decision:** External service clients (Twilio, Nylas, Alto, Stripe, Anthropic, etc.) are plain Python classes in `lightwork/connectors/`. No `BaseConnector`, no registry, no derive. Just classes with methods, initialized at startup, injected into services.

**Reasoning:** There's ~12 connectors. Each is unique. A framework abstraction would add ceremony without reducing code. Just init and pass.

---

## 58. Modules define URL structure and domain grouping

**Decision:** A `Module` declares a URL prefix and groups resources. Modules nest for sub-domain structure. Routes are auto-derived from the resources in each module.

```python
class MaintenanceModule(Module):
    prefix = "/maintenance"
    resources = [MaintenanceJob, Invoice]

class LightworkApp(Module):
    prefix = "/api"
    modules = [MaintenanceModule, SchedulingModule, ...]
```

`LightworkApp.mount(app)` recursively mounts all resources as FastAPI routes.

**Signal routes included:** Signals generate POST routes alongside action routes. CREATE signals -> `POST /{prefix}/{signal_name}`. Other signals -> `POST /{prefix}/{id}/{signal_name}`.

---

## 59. Relationships are class attributes, graph built at startup

**Decision:** Relationships declared as class attributes on the Resource using `belongs_to()`, `has_many()`, `has_one()`, `many_to_many()`. The metaclass collects them into `__relationships__`. A `ResourceGraph` built at startup resolves string references and validates the full domain model.

```python
class MaintenanceJob(Workflow):
    branch     = belongs_to(Branch)
    contractor = belongs_to(Contractor, optional=True)
    invoices   = has_many(Invoice)
```

**Convention:** `belongs_to(Foo)` infers `key="foo_id"`. Override with `key=` when the convention doesn't match (e.g., `belongs_to(Branch, key="office_id")`).

**Target:** Class reference preferred (autocomplete, refactoring). String for circular/forward references.

**Graph enables:** Auto-nested routes (children under parent), authorization cascade (walk up to check parent), startup validation (missing FKs, unregistered targets), API schema endpoint.

---

## 60. Resource maps 1-to-1 to an Ash resource

**Decision:** A Resource declares everything about a domain concept in one class: data layer config (Meta), attributes (Pydantic-style fields), relationships (belongs_to/has_many), actions (sync CRUD), policies, and guards. Workflow extends it with signals and async handlers.

**Field style:** Pydantic (`title: str`, `status: str = "open"`, `amount: Decimal = Field(gt=0)`). The framework converts to SQLAlchemy columns under the hood. The developer never writes `mapped_column()` or `ForeignKey()`.

| Ash | Ironbridge |
|---|---|
| `use Ash.Resource` | `class Foo(Resource)` / `class Foo(Workflow)` |
| `data_layer: AshPostgres` | Framework handles (SQLAlchemy under the hood) |
| `postgres do table "x" end` | `class Meta: table = "x"` |
| `attribute :name, :string` | `name: str` |
| `belongs_to :user, MyApp.User` | `user = belongs_to(User)` |
| `has_many :comments, MyApp.Comment` | `comments = has_many(Comment)` |
| `defaults [:create, :read]` | `default_actions = ["create", "get", "list"]` |
| `update :close do ... end` | `@action(ActionKind.UPDATE) def close(self)` |
| `policy action(:close) do ... end` | `@policy(role_is("admin"))` on the action |

**Reasoning:** One class per domain concept. Everything visible. No split across model/dto/repo/service/router files. The framework derives the infrastructure from the declarations.

---

## 61. `references` relationship for shared resources

**Decision:** A fifth relationship type: `references(Target)`. Declares "I link to a shared resource, but neither owns the other." Distinct from `belongs_to` which implies parent-child ownership.

```python
class MaintenanceJob(Workflow):
    branch = belongs_to(Branch)       # child of Branch
    thread = references(Thread)       # links to Thread, but Thread isn't mine
```

**Route effect:** The referenced resource's children (e.g., Message belongs_to Thread) get mounted under the referencing resource, with ACL checked against the referencing resource.

```
GET /api/maintenance/jobs/{job_id}/messages  -> loads job, checks ACL, filters by job.thread_id
```

**Graph behavior:** `references` does NOT count as `belongs_to` for `parent_of()`, `roots()`, or `ancestry()`. A resource with only `references` (no `belongs_to`) is still a root. `references_for()` returns these relationships separately.

**Use case:** Thread is a platform resource (ironbridge). Multiple domain resources (Call, Enquiry, MaintenanceJob) reference it. Thread doesn't belong to any one domain.

---

## 62. Route scoping: listed = top-level, unlisted children auto-nest

**Decision:** Resources listed in a Module's `resources` are top-level routes under the module prefix. Resources NOT listed but with a `belongs_to` pointing to a listed resource auto-nest under the parent's routes.

```python
class MaintenanceModule(Module):
    prefix = "/maintenance"
    resources = [MaintenanceJob]
    # Invoice belongs_to MaintenanceJob (via graph) -> auto: /jobs/{id}/invoices
    # JobMessage belongs_to MaintenanceJob -> auto: /jobs/{id}/messages
```

**Override:** Use a dict instead of a list for explicit paths:
```python
resources = {
    MaintenanceJob: "/jobs",
    Invoice: "/jobs/{job_id}/invoices",   # or "/invoices" for top-level
}
```

**List = auto-nest from graph. Dict = explicit control.**

---

## 63. Custom route names via `name=` parameter

**Decision:** `@action(name="approve-quote")` and `Signal(name="open-job")` control the URL segment. The Python method/attribute name is the identifier in code. The `name` is the URL path.

```python
@action(kind=ActionKind.ACTION, name="approve-quote")
def approve_quote(self) -> "MaintenanceJob":  # Python name
    ...
# POST /maintenance/jobs/{id}/approve-quote   # URL name
```

Default: method name used as route name. Override when Python naming conventions (snake_case) don't match desired URL conventions (kebab-case).

---

## 64. Extension system: per-resource, per-module, graph-inherited

**Decision:** Extensions (plugins) are instances with config, declared at three levels. They transform resources at registration time -- adding fields, actions, policies, guards, hooks.

**Three levels, cascading:**
- **Resource-level:** `class Meta: extensions = [SoftDelete()]` -- this resource only
- **Module-level:** `class MaintenanceModule(Module): extensions = [AuditLog()]` -- all resources in module
- **Graph-inherited:** Extension on Branch propagates to all resources that `belongs_to` Branch

**Merge rule:** Same extension type on a child overrides the parent's. Different types accumulate.

**Extension base class:**
```python
class Extension:
    # Startup hooks (once per resource)
    def on_resource(self, cls): ...           # add fields, policies, guards
    def on_action(self, cls, name, meta): ... # wrap or modify actions
    def on_signal(self, cls, name, sdef): ... # wrap or modify signals
    def on_route_derived(self, router, cls): ...

    # Per-request hooks
    def before_action(self, actor, resource, action_name, **kw): ...
    def after_action(self, actor, resource, action_name, result): ...
    def before_signal(self, actor, resource, signal_name, payload): ...
    def after_signal(self, actor, resource, signal_name, payload): ...
```

**Core features as extensions:**
- `TenantIsolation(key="tenant_id")` -- injects column, RLS, same_tenant policy
- `SoftDelete(field="is_deleted")` -- injects field, not_deleted guard, replaces destroy
- `Timestamps()` -- injects created_at, updated_at, auto-updates
- `AuditLog(actions="*")` -- injects created_by/updated_by, after-action logging
- `Pagination(default_per_page=25)` -- wraps list routes with page/per_page
- `ReadOnly()` -- removes all write actions and signals
- `RateLimiting(default="100/min")` -- applies limits to derived routes

**Reasoning:** Follows Ash's extension model (AshPostgres, AshJsonApi are extensions). Core features like tenancy become optional, configurable, and composable. The framework core shrinks to: Resource, Action, Signal, Workflow, Graph. Everything else plugs in.

---

## 65. Default action override via `default_create` / `default_update` helpers

**Decision:** To add side effects to a default action without rewriting it, import the default body function and call it:

```python
from ironbridge.shared.framework.defaults import default_create

@action(kind=ActionKind.CREATE)
def create(self, **kwargs) -> "MaintenanceJob":
    default_create(self, **kwargs)
    log_audit("created", self.id)
    return self
```

No hooks, no super(), no magic method names. Just function composition.

For cross-cutting concerns (observability on all actions), use `@on` subscriptions:
```python
@on(MaintenanceJob, "*")
async def audit_all(job, action_name, actor):
    log_audit(action_name, job.id, actor.id)
```

---

## 66. Auth strategy is a FastAPI dependency, not framework concern

**Decision:** The framework provides `ActorMiddleware` and `resolve_actor()`. The app provides the actual auth logic as FastAPI dependencies.

Different routes can use different auth strategies:
```python
class MaintenanceModule(Module):
    auth = web_actor          # JWT/session

class TwilioWebhookModule(Module):
    auth = twilio_actor       # signature verification

class PublicModule(Module):
    auth = public_actor       # anonymous
```

The Actor's `metadata: dict` carries whatever the auth strategy puts in it (JWT claims, session data, API key permissions). The framework never prescribes what's in metadata. Custom policies can read it:
```python
@policy(has_claim("org_type", "enterprise"))
```

---

## 67. Codegen considered, deferred

**Decision:** Runtime derive (current approach) is sufficient for now. Codegen (generating FastAPI routes as static files) was considered for better debugging and IDE support but deferred.

**Trade-off:** Runtime derive has one source of truth (the Resource class). Codegen creates two (Resource + generated files) with drift risk. The debugging benefit doesn't justify the maintenance cost at current scale.

**Future:** If debugging derived routes becomes a pain, add `ironbridge generate --check` as an optional CI step that verifies generated files match the resource definitions. Same derive logic, output to disk instead of memory.

---

## 68. `@workflow` decorator marks durable functions (per-function, not per-class)

**Decision:** The `@workflow` decorator marks a specific function as durable (DBOS wraps it). The `Workflow` mixin marks the class as workflow-capable (has signals). They work together.

```python
class Job(Resource, Workflow):
    @workflow                          # durable, DBOS wraps this
    async def on_start(self, ctx): ...

    def archive(self) -> "Job": ...    # not durable, regular action

    @action(kind=ActionKind.ACTION)
    @workflow                          # action + durable, composes
    async def reassign(self, ctx): ...
```

**Reasoning:** Durability is a per-function decision. A class may have one durable handler and ten sync actions. Wrapping everything in DBOS wastes DB writes. The decorator is explicit, visible, and composable with `@action`, `@policy`, `@guard`.

---

## 69. Workflow is a single continuous function with pause points

**Decision:** A workflow is one async function that runs top-to-bottom, pausing at `ctx.receive()` to wait for signals. Not a DAG of steps (Reactor), not separate handlers per signal.

```python
@workflow
async def on_start(self, ctx, description: str):
    self.state = "sourcing"
    ctx.save()
    quote = await ctx.receive("quote_received")     # pause
    self.state = "quote_approval"
    ctx.save()
    decision = await ctx.receive("approval")         # pause
    self.state = "booking"
    ctx.save()
```

**CREATE signals** start the workflow (the `on_` handler runs). **Non-CREATE signals** feed into the running workflow's `ctx.receive()`. No separate `on_` handler per signal.

**Reasoning:** Interactive workflows (wait for human approval, wait for webhook) are conversations, not pipelines. A continuous function reads naturally. For parallel orchestration, use `asyncio.gather()` or `DBOS.start_workflow()` inside the function.

---

## 70. Framework composes with DBOS, doesn't compete

**Decision:** DBOS is used directly for low-level durability primitives. The framework adds domain modeling and HTTP derivation on top. No wrapping of DBOS APIs that developers might need.

| DBOS provides | Framework adds |
|---|---|
| `@DBOS.workflow()` | `@workflow` decorator (marks which functions) |
| `DBOS.recv()` | `ctx.receive()` (adds actor update, signal lifecycle) |
| `DBOS.send()` | `Signal.send()` (adds policy enforcement) |
| `DBOS.set_event/get_event` | `ctx.respond()` / `SignalHandle.respond()` |
| `DBOS.step()` | `ctx.save()` (persist resource state) |
| `DBOS.start_workflow()` | Used directly in workflow code for parallel work |
| `Queue` | Used directly for rate-limited execution |
| `@DBOS.scheduled()` | Used directly for cron jobs |

The developer can use DBOS APIs directly inside a `@workflow` function when the framework abstractions don't cover their use case.

---

## 71. Signal lifecycle with `async with ctx.receive()`

**Decision:** Signals have explicit lifecycle: open on enter, closed on exit. The `async with` context manager makes this explicit.

```python
async with ctx.receive("approval", timeout=timedelta(days=7)) as approval:
    if not approval:                    # timed out
        self.state = "escalated"
        ctx.save()
        return
    self.state = "booking"
    ctx.save()
    approval.respond({"state": self.state})   # response to sender
# Signal CLOSED. POST /jobs/{id}/approval -> 410 Gone.
```

**SignalHandle** returned by `async with` carries payload + response channel:
- `approval["amount"]` -- access payload
- `approval.respond(data)` -- send response to signal sender
- `bool(approval)` -- False if timed out
- `approval.signal` -- which signal name
- `approval.actor` -- who sent it

**What `with` provides:**
- **On enter:** Signal channel opens. Route accepts POSTs. DBOS.recv() starts.
- **On exit:** Signal channel closes. Late POSTs return 410 Gone. Stale messages discarded.
- **On timeout:** Handle is falsy. Workflow handles it. Channel still closes on exit.
- **On exception:** Channel closes. Cleanup guaranteed.

**Why not plain `await`:** Without explicit close, stale signals can be consumed by future `ctx.receive()` calls on the same topic. A workflow that loops back to "approval" would pick up an old approval from a previous cycle. The `with` block prevents this by discarding unconsumed messages on exit.

**Plain `await` still works** for simple cases where you don't need explicit close:
```python
quote = await ctx.receive("quote_received")
```
The signal closes implicitly at the next receive or workflow completion. `with` is for when you need the guarantee.

---

## 72. `ctx.respond()` for bidirectional signal communication

**Decision:** Workflow signal handlers can respond to the signal sender with assembled data. The response can include data from multiple resources the workflow touched.

```python
approval = Signal(
    policies=[role_is("admin")],
    response=ApprovalResponse,       # response type for OpenAPI
)

async with ctx.receive("approval") as decision:
    self.state = "booking"
    contractor = await ctx.services.contractors.get(self.contractor_id)
    ctx.save()
    decision.respond(ApprovalResponse(
        job_id=self.id,
        state=self.state,
        contractor_name=contractor.name,
    ))
```

**Signal declaration** includes `response=` for OpenAPI schema generation. The derive layer uses it to document the response type on the signal route.

**Without respond:** Caller gets 202 `{"accepted": true}` (fire-and-forget).
**With respond:** Caller gets 200 with the assembled response (sync).

**Implementation:** `respond()` uses `DBOS.set_event()`. The derive layer for `responds=True` signals calls `DBOS.get_event()` to wait for the response before returning to the HTTP caller.

**Best-effort delivery:** If the HTTP caller disconnects before `respond()` is called, the response is dropped. The workflow continues. The state change is committed regardless. The caller can `GET /jobs/{id}` to see the result.

---

## 73. Guards on signals prevent stale signal delivery

**Decision:** Signals can declare guards (same as actions). The derive layer checks guards before dispatching the signal to the workflow. This provides state protection without requiring `with` lifecycle management.

```python
approval = Signal(
    policies=[role_is("admin")],
    guards=[in_state("quote_approval")],
)
```

If the job is no longer in `quote_approval` state when the approval signal arrives, the derive layer returns 409 Conflict. The signal never reaches the workflow. No stale message in the queue.

**Combined with `with` lifecycle:** Guards check resource state. `with` manages the receive channel. Both protect against stale signals, at different levels:
- Guards: "is this resource in the right state for this signal?" (resource-level)
- `with`: "is this workflow currently waiting for this signal?" (workflow-level)
