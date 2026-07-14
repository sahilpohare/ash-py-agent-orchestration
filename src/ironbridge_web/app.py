"""
Ironbridge -- mount onto a FastAPI app.

    from fastapi import FastAPI
    from ironbridge_web import Ironbridge

    app = FastAPI(title="MyApp")
    ib = Ironbridge(app, modules=[MyModule])

You own the FastAPI app. Add your own middleware, routes, lifespan.
Ironbridge layers domain resources, workflows, and error handlers on top.
"""
from __future__ import annotations

import os

from fastapi import FastAPI

from ironbridge.shared.framework import (
    Module, Providers, ResourceGraph,
    init_modules, ready_modules, shutdown_modules, set_providers,
    validate_full,
)
from ironbridge_web.middleware.actor import ActorMiddleware
from ironbridge_web.middleware.errors import register_error_handlers


class Ironbridge:
    """
    Mount Ironbridge onto a FastAPI app.

        app = FastAPI(title="MyApp")
        ib = Ironbridge(app, modules=[MyModule])
    """

    def __init__(
        self,
        app: FastAPI,
        modules: list[type[Module]],
        database_url: str | None = None,
        prefix: str = "/api",
    ):
        self.app = app
        self.modules = modules
        self.prefix = prefix
        self.db_url = database_url or os.environ.get(
            "DATABASE_URL", "postgresql://ironbridge:ironbridge@localhost:5432/ironbridge"
        )

        self._bootstrap()

    def _bootstrap(self) -> None:
        app = self.app

        # Error handlers (PolicyDenied -> 403, GuardFailed -> 409)
        register_error_handlers(app)

        # Actor middleware
        app.add_middleware(ActorMiddleware)

        # DBOS
        from dbos import DBOS
        DBOS(config={
            "name": (app.title or "ironbridge").lower().replace(" ", "-"),
            "system_database_url": self.db_url,
        })
        DBOS.launch()

        # Providers
        providers = Providers()
        set_providers(providers)

        # Module lifecycle: on_init
        init_modules(self.modules, providers)

        # Graph
        self.graph = ResourceGraph()
        self.graph.build()
        v = validate_full()
        for e in v.errors:
            print(f"  ERROR: {e}")

        # Derive DBOS wiring (workflows, steps, signal transport)
        from ironbridge.shared.derive.dbos_workflow import derive_all
        derive_all(self.graph)

        # Mount routes (Module.mount handles extensions + nesting)
        for mod in self.modules:
            mod.mount(app, self.prefix, self.graph)

        # Module lifecycle: on_ready
        ready_modules(self.modules)

        # Shutdown hook
        @app.on_event("shutdown")
        def _shutdown():
            shutdown_modules(self.modules)

    def migrate(self) -> None:
        """Create all tables."""
        from sqlalchemy import create_engine
        from ironbridge.shared.framework.resource import Base
        engine = create_engine(self.db_url)
        Base.metadata.create_all(engine)
        print("Tables created.")
