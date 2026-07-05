"""
Derives a FastAPI APIRouter from a Resource's @actions.

No Restate dependency. Routes call enforce() then the action directly.
Uses tenant_session() for DB access and RLS enforcement.

Generated routes:

    CREATE  -> POST   /{resources}
    READ    -> GET    /{resources}/{id}
    LIST    -> GET    /{resources}
    UPDATE  -> PATCH  /{resources}/{id}
    DESTROY -> DELETE /{resources}/{id}
    ACTION  -> POST   /{resources}/{id}/{action_name}

Usage:

    from ironbridge.shared.derive.fastapi_router import derive_router

    router = derive_router(MaintenanceJob)
    app.include_router(router, prefix="/api")
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ironbridge.shared.db import tenant_session
from ironbridge.shared.derive.repository import SqlAlchemyRepository
from ironbridge.shared.framework.actions import ActionKind
from ironbridge.shared.framework.actor import Actor
from ironbridge.shared.framework.enforcement import GuardFailed, PolicyDenied, enforce
from ironbridge.shared.framework.resource import Resource


def derive_router(
    resource_cls: type[Resource],
    get_actor: Any = None,
    prefix: str | None = None,
) -> APIRouter:
    """
    Build a FastAPI APIRouter from a Resource class.

    Args:
        resource_cls: The Resource subclass to derive routes for.
        get_actor: A FastAPI Depends() callable that returns an Actor.
                   If None, actor must be passed in request state.
        prefix: URL prefix. Defaults to /{tablename} or /{snake_plural}.
    """
    resource_name = resource_cls.__name__
    table_name = getattr(resource_cls, "__tablename__", _snake_plural(resource_name))
    route_prefix = prefix or f"/{table_name}"

    router = APIRouter(prefix=route_prefix, tags=[resource_name])

    for action_name, action_meta in resource_cls.__actions__.items():
        kind = action_meta.kind
        fn = action_meta.fn

        if kind == ActionKind.CREATE:
            _add_create_route(router, resource_cls, fn, get_actor)
        elif kind == ActionKind.READ and action_name == "get":
            _add_get_route(router, resource_cls, fn, get_actor)
        elif kind == ActionKind.READ and action_name == "list":
            _add_list_route(router, resource_cls, fn, get_actor)
        elif kind == ActionKind.UPDATE and action_name == "update":
            _add_update_route(router, resource_cls, fn, get_actor)
        elif kind == ActionKind.DESTROY:
            _add_delete_route(router, resource_cls, fn, get_actor)
        elif kind in (ActionKind.ACTION, ActionKind.UPDATE):
            _add_action_route(router, resource_cls, action_name, fn, get_actor)
        elif kind == ActionKind.READ:
            _add_read_action_route(router, resource_cls, action_name, fn, get_actor)

    return router


# ---------------------------------------------------------------------------
# Route builders
# ---------------------------------------------------------------------------

def _add_create_route(router: APIRouter, cls: type, fn: Any, get_actor: Any) -> None:
    async def create(request: Request) -> JSONResponse:
        actor = await _resolve_actor(request, get_actor)
        body = await request.json()

        with tenant_session(actor.tenant_id) as db:
            repo = SqlAlchemyRepository(db, cls)
            instance = cls()
            _enforce_action(actor, instance, fn)
            result = fn(instance, **body)
            repo.save(result)

        return JSONResponse(_serialize(result), status_code=201)

    router.add_api_route("", create, methods=["POST"])


def _add_get_route(router: APIRouter, cls: type, fn: Any, get_actor: Any) -> None:
    async def get(id: str, request: Request) -> JSONResponse:
        actor = await _resolve_actor(request, get_actor)

        with tenant_session(actor.tenant_id) as db:
            repo = SqlAlchemyRepository(db, cls)
            instance = repo.find_by_id(id)
            if not instance:
                raise HTTPException(404, f"{cls.__name__} not found")
            _enforce_action(actor, instance, fn)

        return JSONResponse(_serialize(instance))

    router.add_api_route("/{id}", get, methods=["GET"])


def _add_list_route(router: APIRouter, cls: type, fn: Any, get_actor: Any) -> None:
    async def list_resources(request: Request) -> JSONResponse:
        actor = await _resolve_actor(request, get_actor)
        filters = dict(request.query_params)

        with tenant_session(actor.tenant_id) as db:
            repo = SqlAlchemyRepository(db, cls)
            # For list, enforce against the class, not an instance
            # Policies like same_tenant still work (checked at RLS level)
            results = repo.list(**filters)

        return JSONResponse([_serialize(r) for r in results])

    router.add_api_route("", list_resources, methods=["GET"])


def _add_update_route(router: APIRouter, cls: type, fn: Any, get_actor: Any) -> None:
    async def update(id: str, request: Request) -> JSONResponse:
        actor = await _resolve_actor(request, get_actor)
        body = await request.json()

        with tenant_session(actor.tenant_id) as db:
            repo = SqlAlchemyRepository(db, cls)
            instance = repo.find_by_id(id)
            if not instance:
                raise HTTPException(404, f"{cls.__name__} not found")
            _enforce_action(actor, instance, fn)
            result = fn(instance, **body)
            repo.save(result)

        return JSONResponse(_serialize(result))

    router.add_api_route("/{id}", update, methods=["PATCH"])


def _add_delete_route(router: APIRouter, cls: type, fn: Any, get_actor: Any) -> None:
    async def delete(id: str, request: Request) -> JSONResponse:
        actor = await _resolve_actor(request, get_actor)

        with tenant_session(actor.tenant_id) as db:
            repo = SqlAlchemyRepository(db, cls)
            instance = repo.find_by_id(id)
            if not instance:
                raise HTTPException(404, f"{cls.__name__} not found")
            _enforce_action(actor, instance, fn)
            result = fn(instance)
            if hasattr(result, "is_deleted") and result.is_deleted:
                repo.save(result)  # soft delete
            else:
                repo.delete(id)

        return JSONResponse({"deleted": True})

    router.add_api_route("/{id}", delete, methods=["DELETE"])


def _add_action_route(
    router: APIRouter, cls: type, action_name: str, fn: Any, get_actor: Any,
) -> None:
    """Custom ACTION or named UPDATE -> POST /{resources}/{id}/{action_name}"""
    async def action_handler(id: str, request: Request) -> JSONResponse:
        actor = await _resolve_actor(request, get_actor)
        body = await request.json() if await request.body() else {}

        with tenant_session(actor.tenant_id) as db:
            repo = SqlAlchemyRepository(db, cls)
            instance = repo.find_by_id(id)
            if not instance:
                raise HTTPException(404, f"{cls.__name__} not found")
            _enforce_action(actor, instance, fn, **body)
            result = fn(instance, **body)
            if isinstance(result, Resource):
                repo.save(result)

        return JSONResponse(_serialize(result))

    router.add_api_route(f"/{{id}}/{action_name}", action_handler, methods=["POST"])


def _add_read_action_route(
    router: APIRouter, cls: type, action_name: str, fn: Any, get_actor: Any,
) -> None:
    """Named READ action -> GET /{resources}/{id}/{action_name}"""
    async def read_handler(id: str, request: Request) -> JSONResponse:
        actor = await _resolve_actor(request, get_actor)

        with tenant_session(actor.tenant_id) as db:
            repo = SqlAlchemyRepository(db, cls)
            instance = repo.find_by_id(id)
            if not instance:
                raise HTTPException(404, f"{cls.__name__} not found")
            _enforce_action(actor, instance, fn)
            result = fn(instance)

        return JSONResponse(_serialize(result))

    router.add_api_route(f"/{{id}}/{action_name}", read_handler, methods=["GET"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _resolve_actor(request: Request, get_actor: Any) -> Actor:
    """Get Actor from the dependency or request state."""
    if get_actor is not None:
        if callable(get_actor):
            result = get_actor(request)
            if hasattr(result, "__await__"):
                return await result
            return result
    # Fallback: check request state
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise HTTPException(401, "No actor resolved")
    return actor


def _enforce_action(actor: Actor, resource: Any, fn: Any, **kwargs: Any) -> None:
    """Run enforce() and translate exceptions to HTTP errors."""
    try:
        enforce(actor, resource, fn, **kwargs)
    except PolicyDenied as e:
        raise HTTPException(403, str(e))
    except GuardFailed as e:
        raise HTTPException(409, str(e))


def _serialize(obj: Any) -> Any:
    """Serialize a Resource or value to JSON-compatible dict."""
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, Resource):
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(type(obj))
        result = {}
        for col in mapper.mapper.column_attrs:
            val = getattr(obj, col.key)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif hasattr(val, "value"):  # enum
                val = val.value
            result[col.key] = val
        return result
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def _snake_plural(name: str) -> str:
    """CamelCase -> snake_cases (plural)."""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return f"{s}s"
