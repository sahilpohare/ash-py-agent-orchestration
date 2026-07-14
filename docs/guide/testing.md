# Testing

## Unit tests (no database)

Use `InMemoryRepository` for data and mock `recv_fn` for workflows.

### Testing a resource action

```python
from ironbridge.shared.framework import enforce, can, PolicyDenied, GuardFailed
from ironbridge.shared.framework.actor import Actor, Origin

def test_only_admin_can_create():
    actor = Actor(id="u1", tenant_id="t1", role="viewer", origin=Origin())
    branch = Branch()

    assert not can(actor, branch, Branch.create)

    try:
        enforce(actor, branch, Branch.create)
        assert False, "Should have raised"
    except PolicyDenied as e:
        assert e.policy_name == "role_is(admin,system)"

def test_admin_can_create():
    actor = Actor(id="u1", tenant_id="t1", role="admin", origin=Origin())
    branch = Branch()
    assert can(actor, branch, Branch.create)
```

### Testing guards

```python
def test_cannot_archive_deleted():
    job = Job()
    job.is_deleted = True

    actor = Actor(id="u1", tenant_id="t1", role="admin", origin=Origin())
    assert not can(actor, job, Job.archive)

def test_mark_completed_requires_approved_state():
    job = Job()
    job.state = "sourcing"
    job.quote_amount = "100"

    actor = Actor(id="u1", tenant_id="t1", role="admin", origin=Origin())
    assert not can(actor, job, Job.mark_completed)

    job.state = "approved"
    assert can(actor, job, Job.mark_completed)
```

### Testing a workflow

Mock the `recv_fn` to simulate the signal sequence:

```python
import pytest
from ironbridge.shared.framework.workflow import WorkflowContext, SignalMessage
from ironbridge.shared.framework.actor import Actor, Origin

@pytest.mark.asyncio
async def test_job_lifecycle():
    signals = [
        SignalMessage(signal="quote_received", payload={"amount": "350", "contractor": "Bob"}, actor=None),
        SignalMessage(signal="approval", payload={"action": "approve"}, actor=None),
    ]
    signal_iter = iter(signals)

    saved_states = []

    def save_fn(resource):
        saved_states.append(resource.state)

    async def recv_fn(names, timeout):
        return next(signal_iter, None)

    job = Job()
    ctx = WorkflowContext(
        actor=Actor(id="system", tenant_id="t1", role="system", origin=Origin()),
        resource=job,
        save_fn=save_fn,
        recv_fn=recv_fn,
    )

    await job.on_start(job, ctx, description="Boiler broken", urgency="emergency", branch_id="b1")

    assert saved_states == ["sourcing", "quote_approval", "approved"]
    assert job.quote_amount == "350"
    assert job.contractor_name == "Bob"
```

### Testing subscriptions

```python
from ironbridge.shared.framework import clear_subscriptions
from ironbridge.shared.framework.subscriptions import get_subscriptions, notify

@pytest.fixture(autouse=True)
def clean_subs():
    clear_subscriptions()
    yield
    clear_subscriptions()

@pytest.mark.asyncio
async def test_subscription_fires():
    calls = []

    @on(Job, "start")
    async def handler(resource, actor):
        calls.append(resource.id)

    job = Job()
    job.id = "test-1"
    actor = Actor(id="u1", tenant_id="t1", role="admin", origin=Origin())

    await notify(job, "start", actor=actor)
    assert calls == ["test-1"]
```

## Integration tests (with database)

Use the real database and DBOS for full signal send/receive cycles.

```python
import pytest

@pytest.fixture
def db():
    """Create tables, yield session, drop tables."""
    from sqlalchemy import create_engine
    from ironbridge.shared.framework.resource import Base

    engine = create_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
```

## In-memory repository

For unit tests without SQLAlchemy:

```python
from ironbridge.shared.framework import InMemoryRepository

repo = InMemoryRepository(Job)
job = Job()
job.id = "test-1"
job.state = "opened"
repo.save(job)

assert repo.find_by_id("test-1").state == "opened"
assert len(repo.list()) == 1

repo.delete("test-1")
assert repo.find_by_id("test-1") is None
```

Clean up between tests:

```python
@pytest.fixture(autouse=True)
def clean():
    InMemoryRepository.clear_all()
    yield
    InMemoryRepository.clear_all()
```
