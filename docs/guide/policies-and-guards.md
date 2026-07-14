# Policies and Guards

Two layers of protection on every action:

1. **Policy** (who): Is this actor allowed? Checked first. Fails with 403.
2. **Guard** (what state): Is the resource in the right state? Checked second. Fails with 409.

Both are declarative. No auth checks in action bodies.

## Policies

A policy is an authorization check. It inspects the Actor and the Resource and returns ALLOW or DENY.

```python
from ironbridge.shared.framework import action, ActionKind, policy, role_is, same_tenant

@action(kind=ActionKind.UPDATE)
@policy(role_is("admin", "operator"))
@policy(same_tenant())
def approve_quote(self) -> "Job":
    self.state = "approved"
    return self
```

Multiple `@policy` decorators are AND-ed. All must allow.

### Built-in policies

| Policy | Allows when |
|--------|------------|
| `role_is("admin", "operator")` | Actor's role matches any listed role. System actors always pass. |
| `same_tenant()` | Actor's `tenant_id` matches resource's `tenant_id`. Skipped if resource has no `tenant_id`. |
| `system_only()` | Actor's role is `system` or `agent`. For webhooks, cron, internal calls. |
| `anyone()` | Any authenticated actor. |
| `has_scope("read:properties")` | Actor has all listed scopes. System actors always pass. |
| `initiator_is("admin")` | The original human in the `on_behalf_of` chain has the role. For agent actions. |

### Custom policies

```python
from ironbridge.shared.framework.policies import PolicyDef, PolicyVerdict

def owns_resource():
    def check(actor, resource):
        if getattr(resource, "created_by", None) == actor.id:
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY
    return PolicyDef(name="owns_resource", check=check, message="Not the owner")

@action(kind=ActionKind.UPDATE)
@policy(owns_resource())
def edit(self, title: str) -> "Post":
    self.title = title
    return self
```

### On signals

Policies on signals are declared inline:

```python
start = Signal(kind=ActionKind.CREATE, policies=[role_is("admin")])
quote_received = Signal(policies=[system_only()])
```

### Enforcement error

When a policy denies, the framework raises `PolicyDenied`, which the error handler maps to:

```json
HTTP 403
{
  "error": "forbidden",
  "message": "Insufficient role",
  "policy": "role_is(admin,operator)"
}
```

## Guards

A guard is a precondition check. It inspects the resource state and returns true/false.

```python
from ironbridge.shared.framework import guard, in_state, not_deleted, field_set

@action(kind=ActionKind.UPDATE)
@policy(role_is("admin"))
@guard(in_state("quote_approval", field="state"))
@guard(field_set("quote_amount"))
def approve_quote(self) -> "Job":
    self.state = "approved"
    return self
```

Multiple `@guard` decorators are AND-ed. All must pass.

### Built-in guards

| Guard | Passes when |
|-------|------------|
| `in_state("opened", "sourcing")` | `resource.state` is one of the listed values |
| `not_in_state("completed", "cancelled")` | `resource.state` is NOT one of the listed values |
| `not_deleted()` | `resource.is_deleted` is `False` |
| `field_set("quote_amount")` | All listed fields are not `None` |
| `field_equals("status", "active")` | Field has the exact value |
| `field_true("verified")` | Field is truthy |
| `custom("name", check_fn, "message")` | Custom check function returns `True` |

### Custom guards

```python
from ironbridge.shared.framework.guards import custom

def budget_under_cap():
    def check(resource, **kwargs):
        amount = float(getattr(resource, "quote_amount", 0) or 0)
        cap = float(getattr(resource, "spend_cap", 0) or 0)
        return amount <= cap
    return custom("budget_under_cap", check, "Quote exceeds spend cap")

@action(kind=ActionKind.UPDATE)
@guard(budget_under_cap())
def auto_approve(self) -> "Job":
    self.state = "approved"
    return self
```

### Guard failure error

```json
HTTP 409
{
  "error": "conflict",
  "message": "Must be in state: quote_approval",
  "guard": "in_state(quote_approval)"
}
```

## Checking without raising

For conditional logic (not in action decorators):

```python
from ironbridge.shared.framework import enforce, can

# Raises PolicyDenied or GuardFailed
enforce(actor, resource, Job.approve_quote)

# Returns bool
if can(actor, resource, Job.approve_quote):
    ...
```
