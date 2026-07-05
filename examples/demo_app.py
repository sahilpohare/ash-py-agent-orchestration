"""
Minimal runnable app demonstrating the full framework.

    podman compose up -d postgres
    PYTHONPATH=src .venv/bin/python examples/demo_app.py migrate
    PYTHONPATH=src .venv/bin/python examples/demo_app.py
    open http://localhost:8000/docs

Full lifecycle test:
    # Create a branch
    curl -X POST http://localhost:8000/api/branches -H "Content-Type: application/json" -H "X-User-Role: admin" -d '{"name":"London","slug":"london"}'

    # Start a job (CREATE signal -> workflow begins, pauses at ctx.receive)
    curl -X POST http://localhost:8000/api/maintenance/jobs/start -H "Content-Type: application/json" -H "X-User-Role: admin" -d '{"description":"Boiler broken","urgency":"emergency","branch_id":"b-1"}'

    # Send quote (mid-workflow signal -> workflow resumes, pauses again)
    curl -X POST http://localhost:8000/api/maintenance/jobs/{id}/quote_received -H "Content-Type: application/json" -H "X-User-Role: system" -d '{"amount":"350"}'

    # Approve (mid-workflow signal -> workflow resumes, completes)
    curl -X POST http://localhost:8000/api/maintenance/jobs/{id}/approval -H "Content-Type: application/json" -H "X-User-Role: admin" -d '{"action":"approve"}'
"""

import os
import sys

os.environ.setdefault(
    "DATABASE_URL", "postgresql://ironbridge:ironbridge@localhost:5432/ironbridge"
)

from datetime import UTC, datetime

from cuid2 import cuid_wrapper
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    ActionKind,
    Module,
    Resource,
    ResourceGraph,
    Signal,
    Workflow,
    action,
    belongs_to,
    default_action,
    guard,
    has_many,
    in_state,
    not_deleted,
    on,
    policy,
    role_is,
    system_only,
    workflow,
)
from ironbridge.shared.framework.actor import Actor, Origin
from ironbridge.shared.framework.extensions.swagger import Swagger

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)

DATABASE_URL = os.environ["DATABASE_URL"]


# ============================================================================
# Resources
# ============================================================================


class Branch(Resource):
    """A tenant branch."""

    class Meta:
        tenant_scoped = False
        default_actions = ["get", "list"]

    __tablename__ = "demo_branches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    jobs = has_many("Job", key="branch_id")

    @action(kind=ActionKind.CREATE)
    @policy(role_is("system", "admin"))
    def create(self, name: str, slug: str) -> "Branch":
        self.id = _cuid()
        self.name = name
        self.slug = slug
        self.active = True
        return self


class Job(Resource, Workflow):
    """A maintenance job. The workflow runs as one continuous function."""

    class Meta:
        tenant_scoped = False
        default_actions = ["get", "list"]
        extensions = [Swagger(tag="Maintenance")]

    __tablename__ = "demo_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    branch_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    state: Mapped[str] = mapped_column(String, nullable=False, default="opened")
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    urgency: Mapped[str] = mapped_column(String, nullable=False, default="routine")
    contractor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quote_amount: Mapped[str | None] = mapped_column(String, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    workflow_id: Mapped[str | None] = mapped_column(String, nullable=True)

    branch = belongs_to(Branch)

    # Signals
    start = Signal(kind=ActionKind.CREATE, policies=[role_is("admin", "operator", "system")])
    quote_received = Signal(policies=[role_is("system")])
    approval = Signal(policies=[role_is("admin", "operator")])

    # One continuous workflow function
    @workflow
    async def on_start(
        self, ctx, description: str, urgency: str = "routine", branch_id: str = "default"
    ):
        """Full maintenance job lifecycle."""
        self.id = _cuid()
        self.branch_id = branch_id
        self.description = description
        self.urgency = urgency
        self.state = "sourcing"
        ctx.save()

        # Pause: wait for contractor quote
        quote = await ctx.receive("quote_received")
        self.quote_amount = quote.get("amount", "0")
        self.state = "quote_approval"
        ctx.save()

        # Pause: wait for operator approval
        decision = await ctx.receive("approval")
        if decision.get("action") == "reject":
            self.state = "opened"
            self.quote_amount = None
            ctx.save()
            return

        self.state = "booking"
        ctx.save()

    # Regular sync action (not a workflow, no DBOS)
    @action(kind=ActionKind.DESTROY)
    @policy(role_is("admin"))
    @guard(not_deleted())
    def archive(self) -> "Job":
        """Soft-delete this job."""
        self.is_deleted = True
        return self


# Subscriptions
@on(Job, "start")
async def log_job_started(resource, actor):
    print(f"[subscription] Job started: {resource.id}")


@on(Job, "*")
async def audit_all(resource, event_name, actor):
    print(f"[audit] Job.{event_name} id={getattr(resource, 'id', '?')}")


# ============================================================================
# App
# ============================================================================


def create_app():
    from dbos import DBOS
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    from ironbridge.shared.framework.actions import ActionKind
    from ironbridge.shared.framework.enforcement import GuardFailed, PolicyDenied
    from ironbridge.shared.framework.signal import register_signal_transport
    from ironbridge.shared.framework.workflow import SignalMessage, WorkflowContext

    app = FastAPI(title="Ironbridge Demo", version="0.1.0")

    # --- Error handlers ---
    @app.exception_handler(PolicyDenied)
    async def on_policy_denied(request: Request, exc: PolicyDenied):
        return JSONResponse(
            403, {"error": "forbidden", "message": str(exc), "policy": exc.policy_name}
        )

    @app.exception_handler(GuardFailed)
    async def on_guard_failed(request: Request, exc: GuardFailed):
        return JSONResponse(
            409, {"error": "conflict", "message": str(exc), "guard": exc.guard_name}
        )

    @app.exception_handler(ValueError)
    async def on_value_error(request: Request, exc: ValueError):
        return JSONResponse(400, {"error": "bad_request", "message": str(exc)})

    # --- Actor middleware ---
    @app.middleware("http")
    async def actor_middleware(request: Request, call_next):
        request.state.actor = Actor(
            id=request.headers.get("X-User-Id", "demo-user"),
            tenant_id=request.headers.get("X-Tenant-Id", "demo-tenant"),
            role=request.headers.get("X-User-Role", "admin"),
            origin=Origin(channel="web_dashboard"),
        )
        return await call_next(request)

    # --- Health ---
    @app.get("/health")
    def health():
        return {"status": "ok"}

    # --- DBOS ---
    DBOS(config={"name": "ironbridge-demo", "system_database_url": DATABASE_URL})
    DBOS.launch()

    # --- DBOS-backed signal transport ---
    # Maps resource_id -> DBOS workflow_id for mid-workflow signal routing
    _active_workflows: dict[str, str] = {}

    @DBOS.step()
    def _dbos_save(cls_name: str, state: dict):
        from ironbridge.shared.db import SessionLocal
        from ironbridge.shared.derive.repository import SqlAlchemyRepository
        from ironbridge.shared.framework import registry

        cls = registry.get(cls_name)
        db = SessionLocal()
        try:
            repo = SqlAlchemyRepository(db, cls)
            instance = cls()
            for k, v in state.items():
                if hasattr(instance, k):
                    setattr(instance, k, v)
            repo.save(instance)
            db.commit()
        finally:
            db.close()

    def _serialize_resource(resource):
        state = {}
        for key in vars(resource):
            if not key.startswith("_"):
                val = getattr(resource, key)
                if not callable(val):
                    if isinstance(val, datetime):
                        val = val.isoformat()
                    state[key] = val
        return state

    def _build_workflow_fn(cls, handler):
        @DBOS.workflow()
        def wf_fn(payload: dict):
            instance = cls()

            def save_fn(resource):
                state = _serialize_resource(resource)
                _dbos_save(cls.__name__, state)
                # Track workflow_id for mid-workflow signals
                rid = getattr(resource, "id", None)
                if rid:
                    resource.workflow_id = DBOS.workflow_id
                    _active_workflows[rid] = DBOS.workflow_id

            def recv_fn(signal_names, timeout):
                timeout_secs = timeout.total_seconds() if timeout else None
                for name in signal_names:
                    result = DBOS.recv(name, timeout_seconds=timeout_secs)
                    if result is not None:
                        return SignalMessage(signal=name, payload=result, actor=None)
                return None

            ctx = WorkflowContext(
                actor=Actor(id="system", tenant_id="demo", role="system", origin=Origin()),
                resource=instance,
                save_fn=save_fn,
                recv_fn=recv_fn,
            )

            # Run the handler synchronously (DBOS workflows are sync)
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(handler(instance, ctx, **payload))
            finally:
                loop.close()

            _active_workflows.pop(getattr(instance, "id", ""), None)
            print(
                f"[workflow] {cls.__name__} completed -> id={instance.id} state={getattr(instance, 'state', '?')}"
            )

        wf_fn.__name__ = f"{cls.__name__}_workflow"
        return wf_fn

    # Build DBOS workflow functions for each resource with a CREATE signal
    _workflow_fns: dict[str, any] = {}
    graph = ResourceGraph()
    graph.build()

    for cls_name, cls in graph.all().items():
        if hasattr(cls, "get_entry_handler"):
            entry = cls.get_entry_handler()
            if entry:
                handler = getattr(cls, entry)
                _workflow_fns[cls_name] = _build_workflow_fn(cls, handler)

    def dbos_transport(signal_def, resource_id, payload, actor=None):
        cls = signal_def.owner_cls
        cls_name = cls.__name__

        if signal_def.kind == ActionKind.CREATE:
            wf_fn = _workflow_fns.get(cls_name)
            if not wf_fn:
                print(f"[transport] no workflow for {cls_name}")
                return
            DBOS.start_workflow(wf_fn, payload or {})
            print(f"[transport] started {cls_name} workflow")
        else:
            if not resource_id:
                print(f"[transport] {signal_def.name} -> no resource_id")
                return
            wf_id = _active_workflows.get(resource_id)
            if wf_id:
                DBOS.send(wf_id, payload or {}, signal_def.name)
                print(f"[transport] {signal_def.name} -> sent to workflow {wf_id}")
            else:
                print(f"[transport] {signal_def.name} -> no active workflow for {resource_id}")

    register_signal_transport(dbos_transport)

    # --- Graph info ---
    errors = graph.validate()
    if errors:
        print(f"Graph errors: {errors}")
    print(f"\nResources: {list(graph.all().keys())}")
    print(f"Roots: {[c.__name__ for c in graph.roots()]}")

    # --- Routes ---
    from ironbridge_web.derive.router import derive_router

    app.include_router(derive_router(Branch, prefix="/branches"), prefix="/api")
    app.include_router(derive_router(Job, prefix="/jobs"), prefix="/api/maintenance")

    # Print routes
    print("\nRoutes:")
    from fastapi.openapi.utils import get_openapi

    openapi = get_openapi(title=app.title, version=app.version, routes=app.routes)
    for path, methods in sorted(openapi.get("paths", {}).items()):
        for method, spec in methods.items():
            print(f"  {method.upper():7s} {path:50s} {spec.get('summary', '')}")

    return app


def migrate():
    from sqlalchemy import create_engine

    from ironbridge.shared.framework.resource import Base

    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    print("Tables created.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        migrate()
    else:
        import uvicorn

        app = create_app()
        uvicorn.run(app, host="0.0.0.0", port=8000)
