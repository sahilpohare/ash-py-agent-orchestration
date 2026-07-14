# Actor

The Actor carries the full context of who is doing what. It threads through policies, guards, workflows, subscriptions, and audit logs.

## Structure

```python
@dataclass(frozen=True)
class Actor:
    id: str                    # user id, agent id, "system"
    tenant_id: str             # which tenant
    role: str                  # admin, operator, viewer, system, agent
    scopes: frozenset[str]     # fine-grained permissions
    origin: Origin             # how this flow started
    on_behalf_of: Actor | None # delegation chain
    metadata: dict             # arbitrary context
```

## Origin

How the flow started:

```python
@dataclass(frozen=True)
class Origin:
    channel: str          # web_dashboard, twilio, nylas, cron, api
    source_type: str      # call, enquiry, maintenance_job
    source_id: str        # the resource that triggered this
    ip: str               # client IP
    user_agent: str       # browser/client
    idempotency_key: str  # caller-supplied dedup key
```

## Constructors

```python
from ironbridge.shared.framework import Actor, Origin, from_request, from_webhook, from_cron

# From an authenticated HTTP request
actor = from_request(user_id="u1", tenant_id="t1", role="admin", ip="1.2.3.4")

# From an inbound webhook
actor = from_webhook(channel="twilio", tenant_id="t1", idempotency_key="wh_abc")

# From a scheduled job
actor = from_cron(tenant_id="t1", job_name="daily_sync")

# Manual
actor = Actor(id="u1", tenant_id="t1", role="admin", origin=Origin(channel="web"))
```

## Delegation chain

When an agent acts on behalf of a user:

```python
user = from_request(user_id="u1", tenant_id="t1", role="admin")
agent = user.as_agent("scheduling-agent")
system = user.as_system("auto-approve")

# Walk the chain
agent.initiator       # -> the original user Actor
agent.on_behalf_of    # -> user Actor
agent.is_system       # True (role is "agent")
```

The `initiator_is("admin")` policy checks the original human, not the agent.

## Actor resolution

The framework resolves the Actor from the HTTP request via a pluggable resolver. Set yours:

```python
from ironbridge_web.middleware.actor import set_actor_resolver

async def resolve_from_jwt(request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    payload = jwt.decode(token, SECRET)
    return Actor(
        id=payload["sub"],
        tenant_id=payload["tenant_id"],
        role=payload["role"],
        origin=Origin(channel="web", ip=request.client.host),
    )

set_actor_resolver(resolve_from_jwt)
```

This is project-specific. The framework provides `ActorMiddleware` which calls your resolver and attaches the Actor to `request.state.actor`. If no resolver is set, the default reads from `X-Tenant-Id`, `X-User-Id`, `X-User-Role` headers (dev only).

## Queries

```python
actor.is_system           # True if role is "system" or "agent"
actor.has_role("admin")   # True if role matches
actor.has_scope("write")  # True if scope is present
actor.initiator           # walk on_behalf_of chain to the original actor
```
