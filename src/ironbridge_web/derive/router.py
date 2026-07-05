"""
Derives FastAPI routes from Resources and Workflows.

Routes have typed Pydantic request/response models so FastAPI auto-generates
correct OpenAPI/Swagger documentation.

Input models come from @action / Signal introspection (ActionMeta.input_model).
Response models are built from SQLAlchemy columns at derive time.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError, create_model

from ironbridge.shared.framework.actions import ActionKind, ActionMeta
from ironbridge.shared.framework.enforcement import enforce
from ironbridge.shared.framework.signal import SignalDef
from ironbridge_web.middleware.actor import resolve_actor


def derive_router(
    resource_cls: type,
    prefix: str | None = None,
) -> APIRouter:
    """Build a FastAPI APIRouter from a Resource or Workflow class."""
    resource_name = resource_cls.__name__
    table_name = getattr(resource_cls, "__tablename__", _snake_plural(resource_name))
    route_prefix = prefix or f"/{table_name}"

    # Build response model from resource columns (once)
    response_model = _build_response_model(resource_cls)

    router = APIRouter(prefix=route_prefix, tags=[resource_name])

    for action_name, action_meta in getattr(resource_cls, "__actions__", {}).items():
        _add_action_route(router, resource_cls, action_name, action_meta, response_model)

    for signal_name, signal_def in getattr(resource_cls, "__signals__", {}).items():
        _add_signal_route(router, resource_cls, signal_name, signal_def)

    return router


# ---------------------------------------------------------------------------
# Response model generation
# ---------------------------------------------------------------------------

_response_model_cache: dict[str, type[BaseModel]] = {}


def _build_response_model(cls: type) -> type[BaseModel] | None:
    """Build a Pydantic model from SQLAlchemy columns for response serialization."""
    cls_name = cls.__name__
    if cls_name in _response_model_cache:
        return _response_model_cache[cls_name]

    try:
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(cls)
    except Exception:
        return None

    fields: dict[str, Any] = {}
    for col in mapper.mapper.column_attrs:
        col_obj = mapper.mapper.columns.get(col.key)
        if col_obj is None:
            continue
        py_type = _sa_type_to_python(col_obj.type)
        if col_obj.nullable:
            fields[col.key] = (py_type | None, None)
        else:
            fields[col.key] = (py_type, ...)

    if not fields:
        return None

    model = create_model(f"{cls_name}Response", **fields)
    _response_model_cache[cls_name] = model
    return model


def _sa_type_to_python(sa_type: Any) -> type:
    type_name = type(sa_type).__name__
    return {
        "String": str,
        "Text": str,
        "Integer": int,
        "BigInteger": int,
        "Boolean": bool,
        "DateTime": str,
        "JSON": dict,
        "ARRAY": list,
        "Numeric": float,
        "Float": float,
    }.get(type_name, str)


# ---------------------------------------------------------------------------
# Accepted response for signals
# ---------------------------------------------------------------------------

class AcceptedResponse(BaseModel):
    accepted: bool = True


class DeletedResponse(BaseModel):
    deleted: bool = True


# ---------------------------------------------------------------------------
# Handler factories - create functions with correct type annotations
# ---------------------------------------------------------------------------

def _make_create_handler(cls: type, meta: ActionMeta, input_model: type | None, response_model: type | None):
    if input_model and meta.input_style == "fields":
        async def handler(request: Request, body: BaseModel) -> JSONResponse:
            actor = await resolve_actor(request)
            instance = cls()
            enforce(actor, instance, meta.fn)
            result = meta.fn(instance, **body.model_dump())
            await _save(request, result)
            return JSONResponse(_serialize(result, meta, response_model), status_code=201)
        handler.__annotations__["body"] = input_model
    elif input_model and meta.input_style == "model":
        async def handler(request: Request, body: BaseModel) -> JSONResponse:
            actor = await resolve_actor(request)
            instance = cls()
            enforce(actor, instance, meta.fn)
            result = meta.fn(instance, input=body)
            await _save(request, result)
            return JSONResponse(_serialize(result, meta, response_model), status_code=201)
        handler.__annotations__["body"] = input_model
    else:
        async def handler(request: Request) -> JSONResponse:
            actor = await resolve_actor(request)
            body = await request.json() if await request.body() else {}
            instance = cls()
            enforce(actor, instance, meta.fn)
            result = meta.fn(instance, **body)
            await _save(request, result)
            return JSONResponse(_serialize(result, meta, response_model), status_code=201)
    return handler


def _make_body_action_handler(cls: type, meta: ActionMeta, input_model: type | None, response_model: type | None):
    """Factory for POST actions on existing resources (custom actions, signals)."""
    if input_model and meta.input_style == "fields":
        async def handler(id: str, request: Request, body: BaseModel) -> JSONResponse:
            actor = await resolve_actor(request)
            instance = await _load(request, cls, id)
            data = body.model_dump()
            enforce(actor, instance, meta.fn, **data)
            result = meta.fn(instance, **data)
            if _is_resource(result):
                await _save(request, result)
            return JSONResponse(_serialize(result, meta, response_model))
        handler.__annotations__["body"] = input_model
    elif input_model and meta.input_style == "model":
        async def handler(id: str, request: Request, body: BaseModel) -> JSONResponse:
            actor = await resolve_actor(request)
            instance = await _load(request, cls, id)
            enforce(actor, instance, meta.fn)
            result = meta.fn(instance, input=body)
            if _is_resource(result):
                await _save(request, result)
            return JSONResponse(_serialize(result, meta, response_model))
        handler.__annotations__["body"] = input_model
    else:
        async def handler(id: str, request: Request) -> JSONResponse:
            actor = await resolve_actor(request)
            body = await request.json() if await request.body() else {}
            instance = await _load(request, cls, id)
            enforce(actor, instance, meta.fn, **body)
            result = meta.fn(instance, **body)
            if _is_resource(result):
                await _save(request, result)
            return JSONResponse(_serialize(result, meta, response_model))
    return handler


def _make_signal_handler(cls: type, name: str, signal_def: SignalDef, input_model: type | None, is_create: bool):
    """Factory for signal POST handlers."""
    if input_model:
        if is_create:
            async def handler(request: Request, body: BaseModel) -> JSONResponse:
                actor = await resolve_actor(request)
                _enforce_signal(actor, None, signal_def)
                signal_obj = getattr(cls, name, None)
                if signal_obj:
                    signal_obj.send(None, body.model_dump(), actor=actor)
                return JSONResponse({"accepted": True}, status_code=202)
        else:
            async def handler(id: str, request: Request, body: BaseModel) -> JSONResponse:
                actor = await resolve_actor(request)
                instance = await _load(request, cls, id)
                _enforce_signal(actor, instance, signal_def)
                signal_obj = getattr(cls, name, None)
                if signal_obj:
                    signal_obj.send(id, body.model_dump(), actor=actor)
                return JSONResponse({"accepted": True}, status_code=202)
        handler.__annotations__["body"] = input_model
    else:
        if is_create:
            async def handler(request: Request) -> JSONResponse:
                actor = await resolve_actor(request)
                _enforce_signal(actor, None, signal_def)
                body = await request.json() if await request.body() else {}
                signal_obj = getattr(cls, name, None)
                if signal_obj:
                    signal_obj.send(None, body, actor=actor)
                return JSONResponse({"accepted": True}, status_code=202)
        else:
            async def handler(id: str, request: Request) -> JSONResponse:
                actor = await resolve_actor(request)
                instance = await _load(request, cls, id)
                _enforce_signal(actor, instance, signal_def)
                body = await request.json() if await request.body() else {}
                signal_obj = getattr(cls, name, None)
                if signal_obj:
                    signal_obj.send(id, body, actor=actor)
                return JSONResponse({"accepted": True}, status_code=202)
    return handler


# ---------------------------------------------------------------------------
# Action route generation
# ---------------------------------------------------------------------------

def _add_action_route(
    router: APIRouter, cls: type, attr_name: str, meta: ActionMeta, response_model: type[BaseModel] | None,
) -> None:
    kind = meta.kind
    route_name = meta.name

    if kind == ActionKind.CREATE:
        _add_create(router, cls, route_name, meta, response_model)
    elif kind == ActionKind.READ and route_name == "get":
        _add_get(router, cls, meta, response_model)
    elif kind == ActionKind.READ and route_name == "list":
        _add_list(router, cls, meta, response_model)
    elif kind == ActionKind.UPDATE and route_name == "update":
        _add_update(router, cls, route_name, meta, response_model)
    elif kind == ActionKind.DESTROY:
        _add_destroy(router, cls, route_name, meta)
    elif kind == ActionKind.READ:
        _add_read_action(router, cls, route_name, meta, response_model)
    elif kind in (ActionKind.ACTION, ActionKind.UPDATE):
        _add_custom_action(router, cls, route_name, meta, response_model)


def _add_create(router: APIRouter, cls: type, name: str, meta: ActionMeta, response_model: type | None) -> None:
    input_model = meta.input_model
    resp_model = meta.output_model or response_model
    handler = _make_create_handler(cls, meta, input_model, response_model)

    path = f"/{name}" if name != "create" else ""
    router.add_api_route(
        path, handler, methods=["POST"],
        name=f"{cls.__name__}_{name}",
        response_model=resp_model,
        status_code=201,
        summary=f"Create {cls.__name__}" if name == "create" else f"Create {cls.__name__} ({_humanize(name)})",
    )


def _add_get(router: APIRouter, cls: type, meta: ActionMeta, response_model: type | None) -> None:
    resp_model = meta.output_model or response_model

    async def handler(id: str, request: Request) -> JSONResponse:
        actor = await resolve_actor(request)
        instance = await _load(request, cls, id)
        enforce(actor, instance, meta.fn)
        result = meta.fn(instance)
        return JSONResponse(_serialize(result, meta, response_model))

    router.add_api_route(
        "/{id}", handler, methods=["GET"],
        name=f"{cls.__name__}_get",
        response_model=resp_model,
        summary=f"Get {cls.__name__} by ID",
    )


class PaginationMeta(BaseModel):
    page: int
    per_page: int
    total: int
    pages: int
    has_next: bool
    has_prev: bool


def _add_list(router: APIRouter, cls: type, meta: ActionMeta, response_model: type | None) -> None:
    resp_model = meta.output_model or response_model

    # Read allowed filters from Meta
    allowed_filters = getattr(cls, "__meta__", {}).get("filters", None)

    async def handler(
        request: Request,
        page: int = 1,
        per_page: int = 25,
        sort: str | None = None,
        order: str = "asc",
    ) -> JSONResponse:
        actor = await resolve_actor(request)

        # Parse filters from query params (exclude pagination params)
        reserved = {"page", "per_page", "sort", "order"}
        raw_filters = {k: v for k, v in request.query_params.items() if k not in reserved}

        # Whitelist filters if declared
        if allowed_filters is not None:
            raw_filters = {k: v for k, v in raw_filters.items() if k in allowed_filters}

        result = await _paginated_list(
            request, cls, actor.tenant_id,
            page=page, per_page=min(per_page, 100),
            sort=sort, order=order,
            filters=raw_filters if raw_filters else None,
        )

        serialized_data = [_serialize(r, meta, response_model) for r in result["data"]]
        return JSONResponse({"data": serialized_data, "meta": result["meta"]})

    router.add_api_route(
        "", handler, methods=["GET"],
        name=f"{cls.__name__}_list",
        summary=f"List {cls.__name__}s",
    )


def _add_update(router: APIRouter, cls: type, name: str, meta: ActionMeta, response_model: type | None) -> None:
    resp_model = meta.output_model or response_model
    handler = _make_body_action_handler(cls, meta, meta.input_model, response_model)

    router.add_api_route(
        "/{id}", handler, methods=["PATCH"],
        name=f"{cls.__name__}_{name}",
        response_model=resp_model,
        summary=f"Update {cls.__name__}",
    )


def _add_destroy(router: APIRouter, cls: type, name: str, meta: ActionMeta) -> None:
    async def handler(id: str, request: Request) -> JSONResponse:
        actor = await resolve_actor(request)
        instance = await _load(request, cls, id)
        enforce(actor, instance, meta.fn)
        result = meta.fn(instance)
        if hasattr(result, "is_deleted") and result.is_deleted:
            await _save(request, result)
        else:
            await _delete(request, cls, id)
        return JSONResponse({"deleted": True})

    path = f"/{{id}}/{name}" if name != "delete" else "/{id}"
    router.add_api_route(
        path, handler, methods=["DELETE"],
        name=f"{cls.__name__}_{name}",
        response_model=DeletedResponse,
        summary=f"Delete {cls.__name__}" if name == "delete" else _humanize(name),
    )


def _add_custom_action(router: APIRouter, cls: type, name: str, meta: ActionMeta, response_model: type | None) -> None:
    resp_model = meta.output_model or response_model
    doc = getattr(meta.fn, "__doc__", None) or ""
    handler = _make_body_action_handler(cls, meta, meta.input_model, response_model)

    router.add_api_route(
        f"/{{id}}/{name}", handler, methods=["POST"],
        name=f"{cls.__name__}_{name}",
        response_model=resp_model,
        summary=_humanize(name),
        description=doc.strip() if doc else None,
    )


def _add_read_action(router: APIRouter, cls: type, name: str, meta: ActionMeta, response_model: type | None) -> None:
    resp_model = meta.output_model or response_model
    doc = getattr(meta.fn, "__doc__", None) or ""

    async def handler(id: str, request: Request) -> JSONResponse:
        actor = await resolve_actor(request)
        instance = await _load(request, cls, id)
        enforce(actor, instance, meta.fn)
        result = meta.fn(instance)
        return JSONResponse(_serialize(result, meta, response_model))

    router.add_api_route(
        f"/{{id}}/{name}", handler, methods=["GET"],
        name=f"{cls.__name__}_{name}",
        response_model=resp_model,
        summary=_humanize(name),
        description=doc.strip() if doc else None,
    )


# ---------------------------------------------------------------------------
# Signal route generation
# ---------------------------------------------------------------------------

def _add_signal_route(router: APIRouter, cls: type, name: str, signal_def: SignalDef) -> None:
    is_create = signal_def.kind == ActionKind.CREATE
    input_model = signal_def.input_model
    doc = f"{'Creates a new ' + cls.__name__ + ' and starts workflow' if is_create else 'Sends signal to running workflow'}."
    handler = _make_signal_handler(cls, name, signal_def, input_model, is_create)

    path = f"/{name}" if is_create and name != "create" else ("" if is_create else f"/{{id}}/{name}")
    router.add_api_route(
        path, handler, methods=["POST"],
        name=f"{cls.__name__}_{name}",
        response_model=AcceptedResponse,
        status_code=202,
        summary=f"{'Create' if is_create else 'Signal'}: {_humanize(name)}",
        description=doc,
    )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

def _enforce_signal(actor: Any, resource: Any, signal_def: SignalDef) -> None:
    from ironbridge.shared.framework.policies import PolicyVerdict
    for p in signal_def.policies:
        if p.check(actor, resource) == PolicyVerdict.DENY:
            from ironbridge.shared.framework.enforcement import PolicyDenied
            raise PolicyDenied(p, actor)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize(obj: Any, meta: ActionMeta | None = None, response_model: type | None = None) -> Any:
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_serialize(item, meta, response_model) for item in obj]
    if isinstance(obj, dict):
        return obj

    # Use action's output_model first
    out = meta.output_model if meta else None
    if out and isinstance(out, type) and issubclass(out, BaseModel):
        return out.model_validate(obj, from_attributes=True).model_dump(mode="json")

    # Use resource response model
    if response_model and isinstance(response_model, type) and issubclass(response_model, BaseModel):
        try:
            return response_model.model_validate(obj, from_attributes=True).model_dump(mode="json")
        except Exception:
            pass

    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")

    if _is_resource(obj):
        try:
            from sqlalchemy import inspect as sa_inspect
            mapper = sa_inspect(type(obj))
            result = {}
            for col in mapper.mapper.column_attrs:
                val = getattr(obj, col.key)
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                elif hasattr(val, "value"):
                    val = val.value
                result[col.key] = val
            return result
        except Exception:
            pass

    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

async def _load(request: Request, cls: type, id: str) -> Any:
    loader = getattr(request.app.state, "loader", None)
    if loader:
        instance = await loader(cls, id)
    else:
        from ironbridge.shared.db import SessionLocal, tenant_session
        from ironbridge.shared.derive.repository import SqlAlchemyRepository

        meta = getattr(cls, "__meta__", {})
        if meta.get("tenant_scoped", False):
            actor = request.state.actor
            with tenant_session(actor.tenant_id) as db:
                repo = SqlAlchemyRepository(db, cls)
                instance = repo.find_by_id(id)
        else:
            db = SessionLocal()
            try:
                repo = SqlAlchemyRepository(db, cls)
                instance = repo.find_by_id(id)
            finally:
                db.close()
    if not instance:
        raise HTTPException(404, f"{cls.__name__} not found")
    return instance


async def _list(request: Request, cls: type, tenant_id: str, **filters) -> list:
    lister = getattr(request.app.state, "lister", None)
    if lister:
        return await lister(cls, tenant_id, **filters)

    from ironbridge.shared.db import SessionLocal, tenant_session
    from ironbridge.shared.derive.repository import SqlAlchemyRepository

    meta = getattr(cls, "__meta__", {})
    if meta.get("tenant_scoped", False):
        with tenant_session(tenant_id) as db:
            repo = SqlAlchemyRepository(db, cls)
            return repo.list(**filters)
    else:
        db = SessionLocal()
        try:
            repo = SqlAlchemyRepository(db, cls)
            return repo.list(**filters)
        finally:
            db.close()


async def _paginated_list(
    request: Request,
    cls: type,
    tenant_id: str,
    page: int = 1,
    per_page: int = 25,
    sort: str | None = None,
    order: str = "asc",
    filters: dict | None = None,
) -> dict:
    """Paginated list with filtering and sorting."""
    from ironbridge.shared.db import SessionLocal, tenant_session
    from ironbridge.shared.framework.data_layer import get_repo

    meta = getattr(cls, "__meta__", {})
    data_layer = meta.get("data_layer", "postgres")

    if data_layer == "memory":
        repo = get_repo(cls)
        return repo.paginate(page=page, per_page=per_page, sort=sort, order=order, filters=filters)

    if meta.get("tenant_scoped", False):
        with tenant_session(tenant_id) as db:
            from ironbridge.shared.derive.repository import SqlAlchemyRepository
            repo = SqlAlchemyRepository(db, cls)
            result = repo.paginate(page=page, per_page=per_page, sort=sort, order=order, filters=filters)
            return result.to_dict()
    else:
        db = SessionLocal()
        try:
            from ironbridge.shared.derive.repository import SqlAlchemyRepository
            repo = SqlAlchemyRepository(db, cls)
            result = repo.paginate(page=page, per_page=per_page, sort=sort, order=order, filters=filters)
            return result.to_dict()
        finally:
            db.close()


async def _save(request: Request, instance: Any) -> None:
    saver = getattr(request.app.state, "saver", None)
    if saver:
        await saver(instance)
        return
    from ironbridge.shared.db import SessionLocal, tenant_session
    from ironbridge.shared.derive.repository import SqlAlchemyRepository

    meta = getattr(type(instance), "__meta__", {})
    is_tenant_scoped = meta.get("tenant_scoped", False)

    if is_tenant_scoped:
        tenant_id = getattr(instance, "tenant_id", None)
        if not tenant_id:
            actor = getattr(request.state, "actor", None)
            tenant_id = actor.tenant_id if actor else None
        if tenant_id:
            with tenant_session(tenant_id) as db:
                repo = SqlAlchemyRepository(db, type(instance))
                repo.save(instance)
    else:
        db = SessionLocal()
        try:
            repo = SqlAlchemyRepository(db, type(instance))
            repo.save(instance)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


async def _delete(request: Request, cls: type, id: str) -> None:
    deleter = getattr(request.app.state, "deleter", None)
    if deleter:
        await deleter(cls, id)
        return
    from ironbridge.shared.db import tenant_session
    from ironbridge.shared.derive.repository import SqlAlchemyRepository
    actor = request.state.actor
    with tenant_session(actor.tenant_id) as db:
        repo = SqlAlchemyRepository(db, cls)
        repo.delete(id)


def _is_resource(obj: Any) -> bool:
    from ironbridge.shared.framework.resource import Resource
    return isinstance(obj, Resource)


def _humanize(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").title()


def _snake_plural(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return f"{s}s"
