# Steps

`@step` marks a function as a retriable, durable unit of work. Use it for external service calls that might fail.

## Basic usage

```python
from ironbridge.shared.framework import step

@step(retries=3, backoff=2.0, interval=1)
def notify_contractor(name: str, job_id: str) -> dict:
    return http_client.post(f"/api/notify/{name}", json={"job": job_id}).json()
```

No try/except. No retry loops. No backoff logic. Declare the retry policy, write the happy path.

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `retries` | `int` | `0` | Max retry attempts on failure |
| `backoff` | `float` | `1.0` | Exponential backoff multiplier |
| `interval` | `int` | `1` | Initial interval between retries (seconds) |

With `retries=3, backoff=2.0, interval=1`:
- Attempt 1: immediate
- Attempt 2: after 1s
- Attempt 3: after 2s
- Attempt 4: after 4s

## How it works

`@step` is a framework marker. It sets `fn._is_step = True` and stores the config. At startup, the derive layer (`derive_all`) wraps each `@step` function with `@DBOS.step()`:

- If the function succeeds, the result is journaled. On replay, the cached result is returned.
- If the function fails, DBOS retries with the configured policy.
- If all retries are exhausted, the workflow fails with the last exception.

## Use in workflows

Call step functions directly inside a workflow handler:

```python
@workflow
async def on_start(self, ctx, description: str):
    self.state = "approved"
    ctx.save()

    # These are durable steps -- retried on failure, cached on replay
    notify_contractor(self.contractor_name, self.id, description)
    push_to_crm(self.id, self.state, self.quote_amount)
```

## Use outside workflows

Step functions can also be called outside workflows. Without an active DBOS workflow context, they run as normal functions (the `@step` marker is a no-op).

## When to use

- External HTTP API calls (PMS, CRM, notification services)
- Email/SMS sending
- File uploads to cloud storage
- Anything that talks to an external system that might be temporarily unavailable

Don't use `@step` for:
- Pure computation (no I/O)
- Database queries (use `ctx.repo()`)
- In-memory operations
