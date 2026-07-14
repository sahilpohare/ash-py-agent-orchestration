# Modules

A Module groups resources under a URL prefix with lifecycle hooks and dependency injection.

## Basic module

```python
from ironbridge.shared.framework import Module, Providers
from .job import Job
from .invoice import Invoice

class MaintenanceModule(Module):
    prefix = "/maintenance"
    resources = [Job, Invoice]
```

Resources in this module get routes under `/api/maintenance/`:
- `POST /api/maintenance/jobs/start`
- `GET /api/maintenance/jobs`
- `GET /api/maintenance/invoices`

## Lifecycle hooks

```python
class MaintenanceModule(Module):
    prefix = "/maintenance"
    resources = [Job, Invoice]

    @classmethod
    def on_init(cls, providers: Providers):
        """Called at startup. Register services."""
        providers.register("contractor_service", ContractorService())

    @classmethod
    def on_ready(cls):
        """Called after all modules initialized, graph built, routes mounted."""
        pass

    @classmethod
    def on_shutdown(cls):
        """Called on app shutdown."""
        pass
```

Order: `on_init` (all modules) -> graph build -> routes mount -> `on_ready` (all modules)

## Nesting modules

```python
class CatalogModule(Module):
    prefix = "/catalog"
    resources = [Property]

class OpsModule(Module):
    prefix = "/ops"
    resources = [Job, Invoice]

class MyApp(Module):
    prefix = ""
    resources = [Branch]        # top-level resources
    modules = [CatalogModule, OpsModule]  # sub-modules
```

Routes:
- `GET /api/branches` (from MyApp.resources)
- `GET /api/catalog/properties` (from CatalogModule)
- `GET /api/ops/jobs` (from OpsModule)

## Dependency injection

Register services in `on_init`, access them via `ctx.deps` in workflows:

```python
class MaintenanceModule(Module):
    @classmethod
    def on_init(cls, providers: Providers):
        providers.register("twilio", TwilioClient(os.environ["TWILIO_TOKEN"]))
        providers.register("messaging", MessagingService(providers.resolve("twilio")))
```

In a workflow:

```python
@workflow
async def on_start(self, ctx, description: str):
    await ctx.deps.messaging.send_sms(self.phone, "Job opened")
```

## Extensions on modules

Extensions declared on a module apply to all its resources:

```python
from ironbridge.shared.framework.extensions.swagger import Swagger

class MaintenanceModule(Module):
    prefix = "/maintenance"
    resources = [Job, Invoice]
    extensions = [Swagger(tag="Maintenance")]
```

## The app module

Your top-level module is declared in `app.py` and referenced in `main.py`:

```python
# src/my_app/app.py
from ironbridge.shared.framework import Module
from .catalog.module import CatalogModule
from .maintenance.module import MaintenanceModule

class MyApp(Module):
    prefix = ""
    modules = [CatalogModule, MaintenanceModule]
```

```python
# src/my_app_web/main.py
from fastapi import FastAPI
from ironbridge_web import Ironbridge
from my_app.app import MyApp

app = FastAPI(title="MyApp")
ib = Ironbridge(app, modules=MyApp.modules)
```
