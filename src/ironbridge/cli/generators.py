"""
Generators - scaffold files and directory structure for resources, workflows, modules, and full apps.

Usage:
    # Individual
    ironbridge generate resource Job --module maintenance --fields "state:str description:str"
    ironbridge generate workflow Job --module maintenance --signals "start:create quote_received approval"
    ironbridge generate module maintenance --resources "Job Invoice"

    # Full app from spec
    ironbridge generate app --spec app_spec.yaml
    ironbridge generate app --name lightwork

Creates full project structure with all modules, resources, workflows, connectors,
subscriptions, web layer, and tests.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def generate_module(
    name: str,
    base_path: str = "src/lightwork",
    test_path: str = "tests",
    resources: list[str] | None = None,
) -> list[str]:
    """
    Scaffold a module directory with __init__.py and module.py.
    Returns list of created file paths.
    """
    created = []
    snake = _snake(name)
    module_dir = Path(base_path) / snake
    test_dir = Path(test_path) / snake

    # Create directories
    module_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    # __init__.py
    init_file = module_dir / "__init__.py"
    if not init_file.exists():
        init_content = ""
        if resources:
            init_content = "\n".join(f"from .{_snake(r)} import {r}" for r in resources) + "\n"
        init_file.write_text(init_content)
        created.append(str(init_file))

    # module.py
    module_file = module_dir / "module.py"
    if not module_file.exists():
        module_file.write_text(_module_template(name, resources or []))
        created.append(str(module_file))

    # test __init__.py
    test_init = test_dir / "__init__.py"
    if not test_init.exists():
        test_init.write_text("")
        created.append(str(test_init))

    return created


def generate_resource(
    name: str,
    module: str,
    fields: list[dict] | None = None,
    relationships: list[dict] | None = None,
    default_actions: list[str] | None = None,
    tenant_scoped: bool = True,
    base_path: str = "src/lightwork",
    test_path: str = "tests",
) -> list[str]:
    """
    Scaffold a Resource file + test file inside a module.
    Returns list of created file paths.
    """
    created = []
    snake_mod = _snake(module)
    snake_name = _snake(name)

    # Ensure module directory exists
    created += generate_module(module, base_path, test_path)

    module_dir = Path(base_path) / snake_mod
    test_dir = Path(test_path) / snake_mod

    # Resource file
    resource_file = module_dir / f"{snake_name}.py"
    if not resource_file.exists():
        resource_file.write_text(_resource_template(
            name=name,
            table=f"{snake_mod}_{snake_name}s",
            fields=fields or [],
            relationships=relationships or [],
            default_actions=default_actions or ["get", "list"],
            tenant_scoped=tenant_scoped,
        ))
        created.append(str(resource_file))

    # Test file
    test_file = test_dir / f"test_{snake_name}.py"
    if not test_file.exists():
        test_file.write_text(_test_template(name, fields or []))
        created.append(str(test_file))

    # Update __init__.py to export
    _update_init(module_dir / "__init__.py", name, snake_name)

    return created


def generate_workflow(
    name: str,
    module: str,
    fields: list[dict] | None = None,
    signals: list[dict] | None = None,
    relationships: list[dict] | None = None,
    default_actions: list[str] | None = None,
    tenant_scoped: bool = True,
    base_path: str = "src/lightwork",
    test_path: str = "tests",
) -> list[str]:
    """
    Scaffold a Workflow Resource file + test file inside a module.
    Returns list of created file paths.
    """
    created = []
    snake_mod = _snake(module)
    snake_name = _snake(name)

    created += generate_module(module, base_path, test_path)

    module_dir = Path(base_path) / snake_mod
    test_dir = Path(test_path) / snake_mod

    # Workflow file
    wf_file = module_dir / f"{snake_name}.py"
    if not wf_file.exists():
        wf_file.write_text(_workflow_template(
            name=name,
            table=f"{snake_mod}_{snake_name}s",
            fields=fields or [],
            signals=signals or [{"name": "start", "create": True}],
            relationships=relationships or [],
            default_actions=default_actions or ["get", "list"],
            tenant_scoped=tenant_scoped,
        ))
        created.append(str(wf_file))

    # Test file
    test_file = test_dir / f"test_{snake_name}.py"
    if not test_file.exists():
        test_file.write_text(_workflow_test_template(name, fields or [], signals or []))
        created.append(str(test_file))

    _update_init(module_dir / "__init__.py", name, snake_name)

    return created


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _resource_template(name, table, fields, relationships, default_actions, tenant_scoped):
    rel_types = set(r["type"] for r in relationships) if relationships else set()
    rel_imports = f"    {', '.join(sorted(rel_types))}," if rel_types else ""

    field_lines = _render_fields(fields)
    rel_lines = _render_relationships(relationships)

    tenant_line = "        tenant_scoped = True" if tenant_scoped else "        tenant_scoped = False"

    return f'''from datetime import datetime, UTC

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String, Boolean, Integer, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, guard,
    role_is, same_tenant,
{rel_imports}
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class {name}(Resource):
    """{name} resource.

    TODO: Add domain-specific actions, policies, and guards.
    """

    class Meta:
{tenant_line}
        default_actions = {default_actions}

    __tablename__ = "{table}"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
{field_lines}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
{rel_lines}
'''


def _workflow_template(name, table, fields, signals, relationships, default_actions, tenant_scoped):
    rel_types = set(r["type"] for r in relationships) if relationships else set()
    rel_imports = f"    {', '.join(sorted(rel_types))}," if rel_types else ""

    field_lines = _render_fields(fields)
    rel_lines = _render_relationships(relationships)
    signal_lines = _render_signals(signals)
    handler = _render_workflow_handler(name, signals)

    tenant_line = "        tenant_scoped = True" if tenant_scoped else "        tenant_scoped = False"

    return f'''from datetime import datetime, timedelta, UTC

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String, Boolean, Integer, Float, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, Workflow, Signal, workflow,
    action, ActionKind, default_action,
    policy, guard,
    role_is, same_tenant, system_only,
    in_state, not_deleted,
{rel_imports}
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class {name}(Resource, Workflow):
    """{name} workflow resource.

    TODO: Implement the workflow lifecycle in the on_ handler.
    """

    class Meta:
{tenant_line}
        default_actions = {default_actions}

    __tablename__ = "{table}"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
{field_lines}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    workflow_id: Mapped[str | None] = mapped_column(String, nullable=True)
{rel_lines}

    # Signals
{signal_lines}
{handler}
'''


def _module_template(name, resources):
    snake = _snake(name)
    res_imports = "\n".join(f"from .{_snake(r)} import {r}" for r in resources) if resources else "# from .resource_name import ResourceName"
    res_list = ", ".join(resources) if resources else "# ResourceName"

    return f'''"""
{name} module.
"""
from ironbridge.shared.framework import Module, Providers

{res_imports}


class {name}Module(Module):
    prefix = "/{snake}"
    resources = [{res_list}]

    @classmethod
    def on_init(cls, providers: Providers):
        """Register module-specific dependencies."""
        # db = providers.resolve("db")
        # providers.register("{snake}_service", ...)
        pass

    @classmethod
    def on_ready(cls):
        pass

    @classmethod
    def on_shutdown(cls):
        pass
'''


def _test_template(name, fields):
    snake = _snake(name)
    field_defaults = "\n".join(
        f"    {f['name']}: {f.get('type', 'str')} = {_default_for_type(f.get('type', 'str'))}"
        for f in fields
    )

    return f'''"""Tests for {name}."""

import pytest
from dataclasses import dataclass

from ironbridge.shared.framework import InMemoryRepository


@dataclass
class Fake{name}:
    id: str = "test-1"
{field_defaults}


@pytest.fixture(autouse=True)
def clean():
    InMemoryRepository.clear_all()
    yield
    InMemoryRepository.clear_all()


class Test{name}CRUD:
    def test_create_and_find(self):
        repo = InMemoryRepository(Fake{name})
        item = Fake{name}(id="test-1")
        repo.save(item)
        assert repo.find_by_id("test-1") is not None

    def test_list(self):
        repo = InMemoryRepository(Fake{name})
        repo.save(Fake{name}(id="test-1"))
        repo.save(Fake{name}(id="test-2"))
        assert len(repo.list()) == 2

    def test_delete(self):
        repo = InMemoryRepository(Fake{name})
        repo.save(Fake{name}(id="test-1"))
        repo.delete("test-1")
        assert repo.find_by_id("test-1") is None
'''


def _workflow_test_template(name, fields, signals):
    snake = _snake(name)
    create_signal = next((s for s in signals if s.get("create")), None)
    mid_signals = [s for s in signals if not s.get("create")]

    receive_tests = ""
    if mid_signals:
        for sig in mid_signals:
            receive_tests += f'''
    @pytest.mark.asyncio
    async def test_{sig["name"]}_signal(self):
        """TODO: test {sig["name"]} signal handling."""
        pass
'''

    return f'''"""Tests for {name} workflow."""

import pytest
from dataclasses import dataclass

from ironbridge.shared.framework import InMemoryRepository
from ironbridge.shared.framework.workflow import WorkflowContext, SignalMessage
from ironbridge.shared.framework.actor import from_request


@dataclass
class Fake{name}:
    id: str = "test-1"
    state: str = "opened"


@pytest.fixture(autouse=True)
def clean():
    InMemoryRepository.clear_all()
    yield
    InMemoryRepository.clear_all()


class Test{name}Workflow:
    def test_create(self):
        repo = InMemoryRepository(Fake{name})
        item = Fake{name}(id="test-1")
        repo.save(item)
        assert repo.find_by_id("test-1") is not None
{receive_tests}
'''


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _render_fields(fields):
    lines = []
    for f in fields:
        fname = f["name"]
        ftype = f.get("type", "str")
        nullable = f.get("nullable", False)
        default = f.get("default")

        sa_type = _sa_type(ftype)
        if nullable:
            mapped = f"Mapped[{ftype} | None]"
            parts = f"    {fname}: {mapped} = mapped_column({sa_type}, nullable=True"
        else:
            mapped = f"Mapped[{ftype}]"
            parts = f"    {fname}: {mapped} = mapped_column({sa_type}, nullable=False"

        if default is not None:
            parts += f", default={default!r}"
        parts += ")"
        lines.append(parts)

    return "\n".join(lines)


def _render_relationships(relationships):
    if not relationships:
        return ""
    lines = [""]
    for rel in relationships:
        rname = rel["name"]
        target = rel["target"]
        rtype = rel["type"]
        key = rel.get("key")
        optional = rel.get("optional", False)

        args = [f'"{target}"']
        if key:
            args.append(f'key="{key}"')
        if optional:
            args.append("optional=True")
        lines.append(f"    {rname} = {rtype}({', '.join(args)})")

    return "\n".join(lines)


def _render_signals(signals):
    lines = []
    for sig in signals:
        sname = sig["name"]
        is_create = sig.get("create", False)
        policies = sig.get("policies", "role_is('admin')")

        if is_create:
            lines.append(f"    {sname} = Signal(kind=ActionKind.CREATE, policies=[{policies}])")
        else:
            lines.append(f"    {sname} = Signal(policies=[{policies}])")
    return "\n".join(lines)


def _render_workflow_handler(name, signals):
    create_signal = next((s for s in signals if s.get("create")), None)
    if not create_signal:
        return ""

    sname = create_signal["name"]
    mid_signals = [s for s in signals if not s.get("create")]

    lines = [
        f"",
        f"    @workflow",
        f"    async def on_{sname}(self, ctx):",
        f'        """TODO: implement {name} lifecycle."""',
        f"        self.id = _cuid()",
        f"        # TODO: set initial state",
        f"        ctx.save()",
    ]

    for msig in mid_signals:
        mname = msig["name"]
        lines += [
            f"",
            f'        async with ctx.receive("{mname}") as {mname}:',
            f"            if not {mname}:",
            f"                # Timed out",
            f"                return",
            f"            # TODO: handle {mname}",
            f"            ctx.save()",
        ]

    return "\n".join(lines)


def _update_init(init_path: Path, class_name: str, snake_name: str):
    """Add import to __init__.py if not already there."""
    import_line = f"from .{snake_name} import {class_name}"
    if init_path.exists():
        content = init_path.read_text()
        if import_line not in content:
            content = content.rstrip() + f"\n{import_line}\n"
            init_path.write_text(content)
    else:
        init_path.write_text(f"{import_line}\n")


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _sa_type(py_type: str) -> str:
    return {
        "str": "String",
        "int": "Integer",
        "float": "Float",
        "bool": "Boolean",
        "datetime": "DateTime(timezone=True)",
        "Decimal": "Numeric(12, 2)",
    }.get(py_type, "String")


def _default_for_type(py_type: str) -> str:
    return {
        "str": '""',
        "int": "0",
        "float": "0.0",
        "bool": "False",
        "datetime": "None",
        "Decimal": "None",
    }.get(py_type, "None")


# ---------------------------------------------------------------------------
# Full app generator
# ---------------------------------------------------------------------------

def generate_app(
    name: str,
    modules: list[dict],
    connectors: list[str] | None = None,
    base_path: str = ".",
) -> list[str]:
    """
    Scaffold a full app from a spec.

    modules: [
        {
            "name": "Maintenance",
            "resources": [
                {"name": "Job", "type": "workflow", "fields": [...], "signals": [...], "relationships": [...]},
                {"name": "Invoice", "type": "resource", "fields": [...], "relationships": [...]},
            ],
        },
        ...
    ]
    connectors: ["twilio", "nylas", "anthropic", "alto", "stripe"]
    """
    created = []
    snake_name = _snake(name)
    src = Path(base_path) / "src"
    app_dir = src / snake_name
    web_dir = src / f"{snake_name}_web"
    test_dir = Path(base_path) / "tests"

    # --- Top-level project files ---
    created += _write_if_new(Path(base_path) / "pyproject.toml", _pyproject_template(name, snake_name))
    created += _write_if_new(Path(base_path) / "Dockerfile", _dockerfile_template())
    created += _write_if_new(Path(base_path) / "docker-compose.yml", _docker_compose_template(snake_name))
    created += _write_if_new(Path(base_path) / ".env.example", _env_example_template(connectors or []))

    # --- App package ---
    app_dir.mkdir(parents=True, exist_ok=True)
    created += _write_if_new(app_dir / "__init__.py", "")

    # --- Connectors ---
    if connectors:
        conn_dir = app_dir / "connectors"
        conn_dir.mkdir(exist_ok=True)
        created += _write_if_new(conn_dir / "__init__.py", "")
        for conn in connectors:
            created += _write_if_new(conn_dir / f"{conn}.py", _connector_template(conn))

    # --- Shared services ---
    shared_dir = app_dir / "shared"
    shared_dir.mkdir(exist_ok=True)
    created += _write_if_new(shared_dir / "__init__.py", "")

    # --- Domain modules ---
    module_names = []
    for mod_spec in modules:
        mod_name = mod_spec["name"]
        module_names.append(mod_name)
        mod_resources = mod_spec.get("resources", [])

        for res_spec in mod_resources:
            res_name = res_spec["name"]
            res_type = res_spec.get("type", "resource")
            fields = res_spec.get("fields", [])
            signals = res_spec.get("signals", [])
            relationships = res_spec.get("relationships", [])
            default_actions = res_spec.get("default_actions", ["get", "list"])
            tenant_scoped = res_spec.get("tenant_scoped", True)

            if res_type == "workflow":
                created += generate_workflow(
                    name=res_name, module=mod_name,
                    fields=fields, signals=signals or [{"name": "start", "create": True}],
                    relationships=relationships, default_actions=default_actions,
                    tenant_scoped=tenant_scoped,
                    base_path=str(app_dir), test_path=str(test_dir),
                )
            else:
                created += generate_resource(
                    name=res_name, module=mod_name,
                    fields=fields, relationships=relationships,
                    default_actions=default_actions, tenant_scoped=tenant_scoped,
                    base_path=str(app_dir), test_path=str(test_dir),
                )

    # --- Subscriptions ---
    created += _write_if_new(app_dir / "subscriptions.py", _subscriptions_template(modules))

    # --- App module ---
    created += _write_if_new(app_dir / "app.py", _app_module_template(name, module_names))

    # --- Web layer ---
    web_dir.mkdir(parents=True, exist_ok=True)
    created += _write_if_new(web_dir / "__init__.py", "")
    created += _write_if_new(web_dir / "main.py", _web_main_template(name, snake_name, module_names, connectors or []))

    # --- Alembic ---
    alembic_dir = Path(base_path) / "alembic"
    alembic_dir.mkdir(exist_ok=True)
    (alembic_dir / "versions").mkdir(exist_ok=True)
    created += _write_if_new(Path(base_path) / "alembic.ini", _alembic_ini_template(snake_name))
    created += _write_if_new(alembic_dir / "env.py", _alembic_env_template(snake_name))

    # --- Root test ---
    test_dir.mkdir(parents=True, exist_ok=True)
    created += _write_if_new(test_dir / "__init__.py", "")
    created += _write_if_new(test_dir / "conftest.py", _test_conftest_template())

    return created


# ---------------------------------------------------------------------------
# App-level templates
# ---------------------------------------------------------------------------

def _pyproject_template(name, snake_name):
    return f'''[project]
name = "{snake_name}"
version = "0.1.0"
description = "{name} - built on Ironbridge"
requires-python = ">=3.12"
dependencies = [
    "ironbridge",
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "sqlalchemy>=2.0.0",
    "psycopg2-binary>=2.9.0",
    "alembic>=1.13.0",
    "dbos>=2.0.0",
    "pydantic>=2.0.0",
    "cuid2>=2.0.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "ruff>=0.4.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{snake_name}", "src/{snake_name}_web"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
'''


def _dockerfile_template():
    return '''FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install -e .
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

CMD ["uvicorn", "lightwork_web.main:app", "--host", "0.0.0.0", "--port", "8000"]
'''


def _docker_compose_template(snake_name):
    return f'''services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: {snake_name}
      POSTGRES_PASSWORD: {snake_name}
      POSTGRES_DB: {snake_name}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U {snake_name}"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://{snake_name}:{snake_name}@postgres:5432/{snake_name}
    depends_on:
      postgres:
        condition: service_healthy
    command: >
      sh -c "alembic upgrade head && uvicorn {snake_name}_web.main:app --host 0.0.0.0 --port 8000"

volumes:
  postgres_data:
'''


def _env_example_template(connectors):
    lines = [
        f'DATABASE_URL=postgresql://app:app@localhost:5432/app',
        '',
    ]
    connector_vars = {
        "twilio": ["TWILIO_ACCOUNT_SID=", "TWILIO_AUTH_TOKEN=", "TWILIO_PHONE_NUMBER="],
        "nylas": ["NYLAS_API_KEY=", "NYLAS_API_URI=https://api.nylas.com"],
        "anthropic": ["ANTHROPIC_API_KEY="],
        "alto": ["ALTO_CLIENT_ID=", "ALTO_CLIENT_SECRET="],
        "stripe": ["STRIPE_SECRET_KEY=", "STRIPE_WEBHOOK_SECRET="],
        "elevenlabs": ["ELEVENLABS_API_KEY="],
        "google_maps": ["GOOGLE_MAPS_API_KEY="],
        "resend": ["RESEND_API_KEY="],
    }
    for conn in connectors:
        if conn in connector_vars:
            lines.append(f"# {conn}")
            lines.extend(connector_vars[conn])
            lines.append("")
    return "\n".join(lines)


def _connector_template(name):
    class_name = name.title().replace("_", "")
    return f'''"""
{class_name} connector. Plain client class, injected via providers.
"""


class {class_name}Client:
    """TODO: implement {name} client."""

    def __init__(self, **kwargs):
        self.config = kwargs
        # TODO: initialize client
'''


def _subscriptions_template(modules):
    lines = [
        '"""',
        'Cross-domain subscriptions. All @on wiring lives here.',
        'No domain imports another domain directly.',
        '"""',
        'from ironbridge.shared.framework import on',
        '',
    ]

    for mod in modules:
        mod_name = mod["name"]
        snake_mod = _snake(mod_name)
        resources = mod.get("resources", [])
        for res in resources:
            res_name = res["name"]
            lines.append(f"from .{snake_mod} import {res_name}")

    lines.append("")
    lines.append("")
    lines.append("# --- Cross-domain reactions ---")
    lines.append("")

    for mod in modules:
        for res in mod.get("resources", []):
            res_name = res["name"]
            if res.get("type") == "workflow":
                create_signal = None
                for sig in res.get("signals", []):
                    if sig.get("create"):
                        create_signal = sig["name"]
                        break
                if create_signal:
                    lines.append(f"# @on({res_name}, \"{create_signal}\")")
                    lines.append(f"# async def on_{_snake(res_name)}_{create_signal}(resource, actor):")
                    lines.append(f"#     pass  # TODO: cross-domain side effects")
                    lines.append("")

    return "\n".join(lines)


def _app_module_template(name, module_names):
    imports = "\n".join(
        f"from .{_snake(m)}.module import {m}Module" for m in module_names
    )
    modules_list = ", ".join(f"{m}Module" for m in module_names)

    return f'''"""
{name} app module. Groups all domain modules.
"""
from ironbridge.shared.framework import Module

{imports}


class {name}App(Module):
    prefix = "/api"
    modules = [{modules_list}]
'''


def _web_main_template(name, snake_name, module_names, connectors):
    return f'''"""
{name} web entry point.

    python -m {snake_name}_web.main          # run server
    python -m {snake_name}_web.main migrate  # create tables
    open http://localhost:8000/docs
"""
import os
import sys

os.environ.setdefault("DATABASE_URL", "postgresql://{snake_name}:{snake_name}@localhost:5432/{snake_name}")

from fastapi import FastAPI
from ironbridge_web import Ironbridge
from {snake_name}.app import {name}App
import {snake_name}.subscriptions  # noqa: F401

app = FastAPI(title="{name}")
ib = Ironbridge(app, modules={name}App.modules)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        ib.migrate()
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
'''


def _alembic_ini_template(snake_name):
    return f'''[alembic]
script_location = alembic
sqlalchemy.url = postgresql://{snake_name}:{snake_name}@localhost:5432/{snake_name}

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
'''


def _alembic_env_template(snake_name):
    return f'''from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from ironbridge.shared.framework.resource import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {{}}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

run_migrations_online()
'''


def _test_conftest_template():
    return '''"""Shared test fixtures."""

import pytest
from ironbridge.shared.framework import InMemoryRepository


@pytest.fixture(autouse=True)
def clean_memory_stores():
    InMemoryRepository.clear_all()
    yield
    InMemoryRepository.clear_all()
'''


# ---------------------------------------------------------------------------
# Write helper
# ---------------------------------------------------------------------------

def _write_if_new(path: Path, content: str) -> list[str]:
    """Write file only if it doesn't exist. Returns list of created paths."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content)
        return [str(path)]
    return []
