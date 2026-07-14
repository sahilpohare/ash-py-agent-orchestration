# Subscriptions

React to events on any resource without coupling domains. All wiring lives in one file.

## Basic subscription

```python
# src/my_app/subscriptions.py
from ironbridge.shared.framework import on
from .maintenance import Job
from .leads import Lead

@on(Job, "start")
async def create_lead_on_job(resource, actor):
    """When a job starts, create a lead from the caller."""
    Lead.start.send(None, {"source": "job", "source_id": resource.id}, actor=actor)
```

## Handler arguments

The framework inspects your handler signature and passes only what you ask for:

```python
# Minimal: just the resource
@on(Job, "start")
async def on_start(resource):
    print(resource.id)

# With actor
@on(Job, "start")
async def on_start(resource, actor):
    print(f"{actor.id} started {resource.id}")

# With event name (useful for wildcards)
@on(Job, "*")
async def audit(resource, event_name, actor):
    print(f"Job.{event_name} by {actor.id}")

# With result (what the action returned)
@on(Job, "mark_completed")
async def on_completed(resource, result, actor):
    ...

# With signal payload
@on(Job, "quote_received")
async def on_quote(resource, payload, actor):
    print(f"Quote: {payload['amount']}")
```

Available parameters: `resource`, `actor`, `event_name`, `action_name`, `signal_name`, `result`, `payload`.

## Wildcard subscriptions

React to every action and signal on a resource:

```python
@on(Job, "*")
async def audit_all(resource, event_name, actor):
    await write_audit_log(actor.id, event_name, resource.id)
```

## Rules

1. **Subscriptions run post-commit.** The action has already completed and saved.
2. **Subscriptions don't break the main flow.** If a handler raises, it's logged and the next handler runs.
3. **All subscriptions live in `subscriptions.py`.** This is the only file where multiple domains appear together.
4. **Domains don't import domains.** Use subscriptions for cross-domain side effects, not direct imports.

## Testing

```python
from ironbridge.shared.framework import clear_subscriptions, on
from ironbridge.shared.framework.subscriptions import get_subscriptions

# Check registrations
handlers = get_subscriptions("Job", "start")
assert len(handlers) == 1

# Clear for isolated tests
clear_subscriptions()
```
