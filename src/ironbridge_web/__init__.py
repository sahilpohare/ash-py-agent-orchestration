"""
ironbridge_web - Web layer for ironbridge.

    from fastapi import FastAPI
    from ironbridge_web import Ironbridge

    app = FastAPI(title="MyApp")
    ib = Ironbridge(app, modules=[MyModule])
"""
from .app import Ironbridge

__all__ = ["Ironbridge"]
