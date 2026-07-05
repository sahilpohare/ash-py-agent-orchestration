"""
Generators - scaffold files and directory structure for resources, workflows, modules.

Usage:
    ironbridge generate resource Job --module maintenance --fields "state:str description:str branch_id:str"
    ironbridge generate workflow Job --module maintenance --fields "state:str" --signals "start:create quote_received approval"
    ironbridge generate module maintenance --resources "Job Invoice"

Creates:
    lightwork/maintenance/
    lightwork/maintenance/__init__.py
    lightwork/maintenance/job.py
    lightwork/maintenance/module.py
    tests/maintenance/
    tests/maintenance/__init__.py
    tests/maintenance/test_job.py
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
