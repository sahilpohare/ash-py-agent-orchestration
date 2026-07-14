# Getting Started

## Create a project

```bash
ironbridge new MyApp
cd my_app
```

This creates:

```
my_app/
  src/
    ironbridge/          # framework (bundled, don't edit)
    ironbridge_web/      # web layer (bundled)
    my_app/              # your domain code
      app.py             # top-level module
      subscriptions.py   # cross-domain @on wiring
    my_app_web/
      main.py            # entry point
  docker-compose.yml
  pyproject.toml
  alembic/
  tests/
```

## Install and run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d postgres
PYTHONPATH=src python -m my_app_web.main migrate
PYTHONPATH=src python -m my_app_web.main
```

Open `http://localhost:8000/docs` for Swagger.

## The entry point

`src/my_app_web/main.py`:

```python
import os
os.environ.setdefault("DATABASE_URL", "postgresql://my_app:my_app@localhost:5432/my_app")

from fastapi import FastAPI
from ironbridge_web import Ironbridge
from my_app.app import MyAppApp
import my_app.subscriptions  # noqa: F401

app = FastAPI(title="MyApp")
ib = Ironbridge(app, modules=MyAppApp.modules)
```

You own the FastAPI app. `Ironbridge` mounts onto it. Add your own middleware, routes, lifespan handlers as needed.

## Add a resource

```bash
ironbridge generate resource Property --module catalog \
  --fields "address:str postcode:str? bedrooms:int=0"
```

This creates `src/my_app/catalog/property.py` with a Resource scaffold. Edit it, add it to the module, restart.

## Add a workflow

```bash
ironbridge generate workflow Job --module maintenance \
  --fields "state:str=opened description:str urgency:str=routine" \
  --signals "start:create quote_received approval"
```

This creates `src/my_app/maintenance/job.py` with a Workflow scaffold including signals and a handler with `ctx.receive()` pause points.

## Project structure

```
src/my_app/
  app.py                 # top-level Module (lists sub-modules)
  subscriptions.py       # cross-domain @on wiring
  catalog/               # domain module
    module.py            # CatalogModule
    property.py          # Property resource
  maintenance/           # domain module
    module.py            # MaintenanceModule
    job.py               # Job workflow
    invoice.py           # Invoice resource
  connectors/            # external service clients (plain Python)
    twilio.py
    nylas.py
```

Each domain module is self-contained. Modules don't import from other modules. Cross-domain communication goes through `@on` subscriptions in `subscriptions.py`.
