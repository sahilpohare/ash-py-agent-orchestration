"""
ironbridge new <name> - create a new project with a working app.

Like `phoenix new` or `django-admin startproject`. Creates a runnable app
with one example domain, health check, error handlers, DBOS, tests.

    ironbridge new lightwork
    cd lightwork
    docker compose up -d postgres
    python -m lightwork_web.main migrate
    python -m lightwork_web.main
    open http://localhost:8000/docs
"""
from __future__ import annotations

import re
from pathlib import Path


def new_project(name: str, output: str = ".") -> list[str]:
    """Create a new ironbridge project."""
    snake = _snake(name)
    title = name
    root = Path(output)

    created = []

    def write(rel_path: str, content: str):
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        created.append(str(path))

    # --- Project config ---
    write("pyproject.toml", f'''[project]
name = "{snake}"
version = "0.1.0"
description = "{title}"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "sqlalchemy>=2.0.0",
    "psycopg2-binary>=2.9.0",
    "alembic>=1.13.0",
    "dbos>=2.0.0",
    "pydantic>=2.0.0",
    "cuid2>=2.0.0",
    "httpx>=0.27.0",
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
packages = ["src/ironbridge", "src/ironbridge_web", "src/{snake}", "src/{snake}_web"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py313"
''')

    write("docker-compose.yml", f'''services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: {snake}
      POSTGRES_PASSWORD: {snake}
      POSTGRES_DB: {snake}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U {snake}"]
      interval: 5s
      retries: 5

volumes:
  pgdata:
''')

    write(".env", f'DATABASE_URL=postgresql://{snake}:{snake}@localhost:5432/{snake}\n')

    write(".gitignore", '''__pycache__/
*.pyc
.env
.venv/
*.egg-info/
dist/
build/
.pytest_cache/
.ruff_cache/
''')

    # --- Alembic ---
    write("alembic.ini", f'''[alembic]
script_location = alembic
sqlalchemy.url = postgresql://{snake}:{snake}@localhost:5432/{snake}

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
''')

    write("alembic/env.py", f'''from logging.config import fileConfig
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
''')

    write("alembic/versions/.gitkeep", "")

    # --- App package ---
    write(f"src/{snake}/__init__.py", "")

    write(f"src/{snake}/app.py", f'''"""
{title} application. Add your modules here.
"""
from ironbridge.shared.framework import Module


class {title}App(Module):
    prefix = "/api"
    modules = [
        # Add your modules:
        # from .my_domain.module import MyDomainModule
        # MyDomainModule,
    ]
''')

    write(f"src/{snake}/subscriptions.py", f'''"""
Cross-domain subscriptions. All @on wiring lives here.
"""
from ironbridge.shared.framework import on

# Example:
# from .my_domain import MyResource
#
# @on(MyResource, "create")
# async def on_my_resource_created(resource, actor):
#     pass
''')

    # --- Web entry point ---
    write(f"src/{snake}_web/__init__.py", "")

    write(f"src/{snake}_web/main.py", f'''"""
{title} web entry point.

    python -m {snake}_web.main          # run server
    python -m {snake}_web.main migrate  # create tables
"""
import os
import sys

os.environ.setdefault("DATABASE_URL", "postgresql://{snake}:{snake}@localhost:5432/{snake}")

from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


def create_app():
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from dbos import DBOS

    from ironbridge.shared.framework import (
        Providers, set_providers, ResourceGraph,
        init_modules, ready_modules, shutdown_modules,
    )
    from ironbridge.shared.framework.enforcement import PolicyDenied, GuardFailed
    from ironbridge.shared.framework.actor import Actor, Origin
    from ironbridge_web.derive.router import derive_router

    from {snake}.app import {title}App
    import {snake}.subscriptions  # noqa: F401

    app = FastAPI(title="{title}", version="0.1.0")

    # --- Error handlers ---
    @app.exception_handler(PolicyDenied)
    async def _(request: Request, exc: PolicyDenied):
        return JSONResponse(403, {{"error": "forbidden", "message": str(exc), "policy": exc.policy_name}})

    @app.exception_handler(GuardFailed)
    async def _(request: Request, exc: GuardFailed):
        return JSONResponse(409, {{"error": "conflict", "message": str(exc), "guard": exc.guard_name}})

    @app.exception_handler(ValueError)
    async def _(request: Request, exc: ValueError):
        return JSONResponse(400, {{"error": "bad_request", "message": str(exc)}})

    # --- Actor middleware (replace with JWT in production) ---
    @app.middleware("http")
    async def actor_middleware(request: Request, call_next):
        request.state.actor = Actor(
            id=request.headers.get("X-User-Id", "anonymous"),
            tenant_id=request.headers.get("X-Tenant-Id", "default"),
            role=request.headers.get("X-User-Role", "admin"),
            origin=Origin(channel="web"),
        )
        return await call_next(request)

    # --- Health ---
    @app.get("/health")
    def health():
        return {{"status": "ok"}}

    # --- DBOS ---
    DBOS(config={{"name": "{snake}", "system_database_url": DATABASE_URL}})
    DBOS.launch()

    # --- Providers ---
    providers = Providers()
    set_providers(providers)

    # --- Modules ---
    modules = {title}App.modules
    init_modules(modules, providers)

    graph = ResourceGraph()
    graph.build()
    errors = graph.validate()
    if errors:
        for e in errors:
            print(f"  graph error: {{e}}")

    for mod_cls in modules:
        for resource in mod_cls.all_resources():
            prefix = f"/api{{mod_cls.prefix}}" if mod_cls.prefix else "/api"
            app.include_router(derive_router(resource), prefix=prefix)

    ready_modules(modules)

    @app.on_event("shutdown")
    def on_shutdown():
        shutdown_modules(modules)

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
''')

    # --- Tests ---
    write("tests/__init__.py", "")

    write("tests/conftest.py", '''import pytest
from ironbridge.shared.framework import InMemoryRepository


@pytest.fixture(autouse=True)
def clean():
    InMemoryRepository.clear_all()
    yield
    InMemoryRepository.clear_all()
''')

    # --- README ---
    write("README.md", f'''# {title}

Built on [Ironbridge](https://github.com/sahilpohare/ironbridge).

## Quick start

```bash
# Start Postgres
docker compose up -d postgres

# Create tables
PYTHONPATH=src python -m {snake}_web.main migrate

# Run
PYTHONPATH=src python -m {snake}_web.main

# Open
open http://localhost:8000/docs
```

## Add a domain

```bash
PYTHONPATH=src python -m ironbridge.cli.main generate module MyDomain
PYTHONPATH=src python -m ironbridge.cli.main generate resource MyThing -m MyDomain --fields "name:str status:str=active"
```

Then add it to `src/{snake}/app.py`:

```python
from .my_domain.module import MyDomainModule

class {title}App(Module):
    prefix = "/api"
    modules = [MyDomainModule]
```

## Add components

```bash
PYTHONPATH=src python -m ironbridge.cli.main add --list
PYTHONPATH=src python -m ironbridge.cli.main add tenancy --app-name {snake}
PYTHONPATH=src python -m ironbridge.cli.main add threads
```
''')

    return created


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
