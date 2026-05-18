## The problem
Build the core substrate of a platform where humans collaborate with AI
agents over time. The piece you are building today is the orchestration
layer: the system that holds work together while agents and humans send
messages back and forth, survives restarts, and stays consistent under
concurrent input.
You are not building the AI agents themselves. The agent for this exercise
is a stub — a function that sleeps 100ms and returns a response (a
hardcoded string, an echo of the input, or a real model call if you feel
like wiring one up — it does not matter, the agent is not the point). The
orchestration around the stub is the point.
## What the system has to do
1. Accept incoming messages over an HTTP POST endpoint. The payload shape
   is your call, but it must carry at minimum a tenant identifier, a
   participant identifier, and a message body. The system should be
   shaped so that additional intake mechanisms (a CLI client, an email
   poller, a scheduled trigger, a different webhook shape) could be added
   without redesigning the core. You may be asked to add a second intake
   mechanism live during the walkthrough.
2. For each incoming message, either start a new ongoing **unit of work**
   or attach the message to an existing one.
3. Route the message to the agent stub. The stub's response is part of
   the same unit of work as the message that triggered it.
4. A human observer can watch a unit of work in real time and send their
   own messages into it. Agents and humans are both participants in the
   same unit of work.
5. Every interaction is durable. If the server process is killed mid-flow,
   on restart every unit of work resumes with its full history intact and
   the next incoming message picks up where the last one left off.
## Hard requirements
These properties are non-negotiable. Your tests must prove each one.
- **Multi-tenant — structurally, not by filter.** Tenant A's data must be
  unreachable from Tenant B's session at a layer below the application
  code. A developer who forgets a `WHERE tenant_id = ?` clause in a query
  helper must not be able to leak data across tenants. Acceptable shapes:
  Postgres row-level security bound to a session-local GUC, schema-per-
  tenant with `search_path`, a tenant-bound database role, or equivalent.
  Not acceptable: an application-layer helper that "always appends" a
  tenant filter — that fails open the first time someone bypasses the
  helper.
- **Durable.** State survives process restarts. The full history of every
  unit of work is recoverable from durable storage. In-memory-only state
  does not satisfy this requirement.
- **Concurrent-safe with defined ordering.** Two messages arriving
  simultaneously for the same unit of work both land. Each accepted
  message gets a strictly monotonic position within its unit (1, 2, 3, …
  with no gaps and no duplicates). Every observer sees the same order.
- **Idempotent at the intake boundary.** A client that retries an HTTP
  POST (same idempotency key) because it did not receive a 200 in time
  must not produce a duplicate message. Idempotency is enforced at the
  storage layer. The test is the absence of a check-then-insert *window*,
  not the absence of a literal "check" call in your app code — your test
  must hammer the same key with concurrent requests and prove exactly one
  message is persisted.
- **Observable in real time, not by polling.** A separate observer
  (browser, CLI client, curl + server-sent events — your choice) can
  subscribe to a unit of work and see messages arrive as they happen. Two
  separate observers subscribed to the same unit of work both receive
  every message, in the same order. Polling-based "subscription" does not
  satisfy this requirement.
- **Multi-participant.** A unit of work has many participants — agents
  and humans — over its lifetime. Any participant's message is part of
  the same unit.
## Scenarios the system must handle correctly
1. A client POSTs `{"tenant":"t1","participant":"alice","body":"ping"}`.
   The system creates a unit of work, dispatches to the agent stub, the
   stub's response arrives ~100ms later. Both messages appear on a live
   observer in real time. Alice POSTs again to the same unit of work; the
   agent responds again. The full exchange is durable.
2. Twenty clients POST concurrently to the same unit of work. All twenty
   messages plus the twenty agent responses are persisted with strictly
   monotonic positions. Every observer sees the same order.
3. The server process is killed mid-conversation with `kill -9`. On
   restart, every unit of work is recoverable in its full state from
   durable storage. The next POST to an existing unit of work continues
   it seamlessly.
4. A client POSTs the same message twice (same idempotency key, retried
   because the network ate the first response). The duplicate is detected
   at the storage layer and recorded once. A concurrent burst of 20
   identical retries also yields exactly one persisted message.
5. Tenant A's database session, executing the raw SQL
   `SELECT * FROM units_of_work`, returns only Tenant A's rows — without
   the application layer adding a `WHERE` clause. The access boundary
   lives below the application. Your test should prove this directly
   against the database, not just through the API.
6. Two separate observer processes subscribe to the same unit of work.
   Both receive every message in the same order as they arrive. Neither
   is favoured over the other.
