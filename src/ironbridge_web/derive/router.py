"""
Derives FastAPI routes from Resources and Workflows.

One generic handler pipeline: resolve actor -> parse input -> load/create resource
-> enforce -> execute -> save -> effects -> serialize -> respond.

Handler differences (create vs get vs update vs signal) are configuration,
not separate code paths.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, create_model

from ironbridge.shared.framework.actions import ActionKind, ActionMeta
from ironbridge.shared.framework.effects import run_effects
from ironbridge.shared.framework.enforcement import enforce
from ironbridge.shared.framework.signal import SignalDef
from ironbridge_web.middleware.actor import resolve_actor


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def derive_router(resource_cls: type, prefix: str | None = None) -> APIRouter:
    """Build a FastAPI APIRouter from a Resource or Workflow class."""
    table_name = getattr(resource_cls, "__tablename__", _snake_plural(resource_cls.__name__))
    route_prefix = prefix or f"/{table_name}"
    response_model = _build_response_model(resource_cls)
    router = APIRouter(prefix=route_prefix, tags=[resource_cls.__name__])

    for action_name, meta in getattr(resource_cls, "__actions__", {}).items():
        _mount_action(router, resource_cls, meta, response_model)

    for signal_name, signal_def in getattr(resource_cls, "__signals__", {}).items():
        _mount_signal(router, resource_cls, signal_name, signal_def)

    return router


# ---------------------------------------------------------------------------
# Generic action handler
# ---------------------------------------------------------------------------

def _mount_action(router: APIRouter, cls: type, meta: ActionMeta, response_model: type | None) -> None:
    kind = meta.kind
    name = meta.name

    # Resolve input model for default create
    input_model = meta.input_model
    if input_model is None and kind == ActionKind.CREATE and getattr(meta.fn, "_is_default_action", False):
        input_model = _build_create_input_model(cls)
        meta.input_style = "fields" if input_model else "none"

    # Route config based on kind
    if kind == ActionKind.CREATE:
        method, path, status = "POST", ("" if name == "create" else f"/{name}"), 201
        needs_id = False
    elif kind == ActionKind.READ and name == "list":
        _mount_list(router, cls, meta, response_model)
        return
    elif kind == ActionKind.READ:
        method, path, status = "GET", ("/{id}" if name == "get" else f"/{{id}}/{name}"), 200
        needs_id = True
    elif kind == ActionKind.UPDATE and name == "update":
        method, path, status = "PATCH", "/{id}", 200
        needs_id = True
    elif kind == ActionKind.DESTROY:
        method, path, status = "DELETE", ("/{id}" if name == "delete" else f"/{{id}}/{name}"), 200
        needs_id = True
    else:
        method, path, status = "POST", f"/{{id}}/{name}", 200
        needs_id = True

    # Build handler
    handler = _make_handler(cls, meta, input_model, response_model, needs_id=needs_id, status_code=status)

    router.add_api_route(
        path, handler, methods=[method],
        name=f"{cls.__name__}_{name}",
        status_code=status,
        summary=_action_summary(cls.__name__, name, kind),
    )


def _make_handler(
    cls: type, meta: ActionMeta, input_model: type | None, response_model: type | None,
    needs_id: bool = False, status_code: int = 200,
):
    """One handler factory for all action types."""

    if needs_id and input_model:
        async def handler(id: str, request: Request, body: BaseModel) -> JSONResponse:
            actor = await resolve_actor(request)
            instance = await _load(request, cls, id)
            data = body.model_dump() if meta.input_style == "fields" else {}
            enforce(actor, instance, meta.fn, **data)
            result = meta.fn(instance, **data) if meta.input_style == "fields" else meta.fn(instance, input=body)
            if _is_resource(result):
                await _save(request, result)
            run_effects(meta.fn, result, actor)
            if meta.kind == ActionKind.DESTROY:
                return JSONResponse({"deleted": True})
            return JSONResponse(_serialize(result, response_model), status_code=status_code)
        handler.__annotations__["body"] = input_model

    elif needs_id:
        async def handler(id: str, request: Request) -> JSONResponse:
            actor = await resolve_actor(request)
            instance = await _load(request, cls, id)
            body = await _parse_body(request)
            enforce(actor, instance, meta.fn, **body)
            result = meta.fn(instance, **body) if body else meta.fn(instance)
            if _is_resource(result) and meta.kind in (ActionKind.UPDATE, ActionKind.DESTROY, ActionKind.ACTION):
                await _save(request, result)
            run_effects(meta.fn, result, actor)
            if meta.kind == ActionKind.DESTROY:
                return JSONResponse({"deleted": True})
            return JSONResponse(_serialize(result, response_model), status_code=status_code)

    elif input_model:
        async def handler(request: Request, body: BaseModel) -> JSONResponse:
            actor = await resolve_actor(request)
            instance = cls()
            data = body.model_dump() if meta.input_style == "fields" else {}
            enforce(actor, instance, meta.fn, **data)
            result = meta.fn(instance, **data) if meta.input_style == "fields" else meta.fn(instance, input=body)
            await _save(request, result)
            run_effects(meta.fn, result, actor)
            return JSONResponse(_serialize(result, response_model), status_code=status_code)
        handler.__annotations__["body"] = input_model

    else:
        async def handler(request: Request) -> JSONResponse:
            actor = await resolve_actor(request)
            body = await _parse_body(request)
            instance = cls()
            enforce(actor, instance, meta.fn, **body)
            result = meta.fn(instance, **body) if body else meta.fn(instance)
            await _save(request, result)
            run_effects(meta.fn, result, actor)
            return JSONResponse(_serialize(result, response_model), status_code=status_code)

    return handler


# ---------------------------------------------------------------------------
# List handler (special: pagination, filters, includes)
# ---------------------------------------------------------------------------

class PaginationMeta(BaseModel):
    page: int
    per_page: int
    total: int
    pages: int
    has_next: bool
    has_prev: bool


def _mount_list(router: APIRouter, cls: type, meta: ActionMeta, response_model: type | None) -> None:
    allowed_filters = getattr(cls, "__meta__", {}).get("filters", None)

    async def handler(
        request: Request,
        page: int = 1,
        per_page: int = 25,
        sort: str | None = None,
        order: str = "asc",
        include: str | None = None,
    ) -> JSONResponse:
        actor = await resolve_actor(request)

        reserved = {"page", "per_page", "sort", "order", "include"}
        raw_filters = {k: v for k, v in request.query_params.items() if k not in reserved}
        if allowed_filters is not None:
            raw_filters = {k: v for k, v in raw_filters.items() if k in allowed_filters}

        result = await _paginated_list(
            request, cls, actor.tenant_id,
            page=page, per_page=min(per_page, 100),
            sort=sort, order=order,
            filters=raw_filters or None,
        )

        data = [_serialize(r, response_model) for r in result["data"]]
        if include:
            data = [_attach_includes(d, r, include) for d, r in zip(data, result["data"])]
        return JSONResponse({"data": data, "meta": result["meta"]})

    router.add_api_route(
        "", handler, methods=["GET"],
        name=f"{cls.__name__}_list",
        summary=f"List {cls.__name__}s",
    )


# ---------------------------------------------------------------------------
# Signal handler
# ---------------------------------------------------------------------------

def _mount_signal(router: APIRouter, cls: type, name: str, signal_def: SignalDef) -> None:
    is_create = signal_def.kind == ActionKind.CREATE
    input_model = signal_def.input_model

    if is_create:
        if input_model:
            async def handler(request: Request, body: BaseModel) -> JSONResponse:
                actor = await resolve_actor(request)
                _enforce_signal(actor, None, signal_def)
                getattr(cls, name).send(None, body.model_dump(), actor=actor)
                return JSONResponse({"accepted": True}, status_code=202)
            handler.__annotations__["body"] = input_model
        else:
            async def handler(request: Request) -> JSONResponse:
                actor = await resolve_actor(request)
                _enforce_signal(actor, None, signal_def)
                body = await _parse_body(request)
                getattr(cls, name).send(None, body, actor=actor)
                return JSONResponse({"accepted": True}, status_code=202)
    else:
        if input_model:
            async def handler(id: str, request: Request, body: BaseModel) -> JSONResponse:
                actor = await resolve_actor(request)
                instance = await _load(request, cls, id)
                _enforce_signal(actor, instance, signal_def)
                getattr(cls, name).send(id, body.model_dump(), actor=actor)
                return JSONResponse({"accepted": True}, status_code=202)
            handler.__annotations__["body"] = input_model
        else:
            async def handler(id: str, request: Request) -> JSONResponse:
                actor = await resolve_actor(request)
                instance = await _load(request, cls, id)
                _enforce_signal(actor, instance, signal_def)
                body = await _parse_body(request)
                getattr(cls, name).send(id, body, actor=actor)
                return JSONResponse({"accepted": True}, status_code=202)

    path = f"/{name}" if is_create else f"/{{id}}/{name}"
    router.add_api_route(
        path, handler, methods=["POST"],
        name=f"{cls.__name__}_{name}",
        status_code=202,
        summary=f"{'Create' if is_create else 'Signal'}: {_humanize(name)}",
    )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

def _enforce_signal(actor: Any, resource: Any, signal_def: SignalDef) -> None:
    from ironbridge.shared.framework.policies import PolicyVerdict
    from ironbridge.shared.framework.enforcement import PolicyDenied, GuardFailed
    for p in signal_def.policies:
        if p.check(actor, resource) == PolicyVerdict.DENY:
            raise PolicyDenied(p, actor)
    if resource is not None:
        for g in getattr(signal_def, "guards", []):
            if not g.check(resource):
                raise GuardFailed(g)


# ---------------------------------------------------------------------------
# Model generation
# ---------------------------------------------------------------------------

_response_cache: dict[str, type[BaseModel]] = {}
_create_input_cache: dict[str, type[BaseModel]] = {}


def _build_response_model(cls: type) -> type[BaseModel] | None:
    name = cls.__name__
    if name in _response_cache:
        return _response_cache[name]
    try:
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(cls)
    except Exception:
        return None
    fields = {}
    for col in mapper.mapper.column_attrs:
        col_obj = mapper.mapper.columns.get(col.key)
        if col_obj is None:
            continue
        py = _sa_to_py(col_obj.type)
        fields[col.key] = (py | None, None) if col_obj.nullable else (py, ...)
    if not fields:
        return None
    model = create_model(f"{name}Response", **fields)
    _response_cache[name] = model
    return model


def _build_create_input_model(cls: type) -> type[BaseModel] | None:
    name = cls.__name__
    if name in _create_input_cache:
        return _create_input_cache[name]
    try:
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(cls)
    except Exception:
        return None
    skip = {"id", "created_at", "updated_at", "tenant_id", "thread_id", "workflow_id"}
    fields = {}
    for col in mapper.mapper.column_attrs:
        if col.key in skip:
            continue
        col_obj = mapper.mapper.columns.get(col.key)
        if col_obj is None or col_obj.server_default is not None:
            continue
        py = _sa_to_py(col_obj.type)
        if col_obj.nullable or col_obj.default is not None:
            default = None if col_obj.nullable else (col_obj.default.arg if col_obj.default else None)
            fields[col.key] = (py | None, default)
        else:
            fields[col.key] = (py, ...)
    if not fields:
        return None
    model = create_model(f"{name}CreateInput", **fields)
    _create_input_cache[name] = model
    return model


def _sa_to_py(sa_type: Any) -> type:
    return {"String": str, "Text": str, "Integer": int, "BigInteger": int, "Boolean": bool,
            "DateTime": str, "JSON": dict, "ARRAY": list, "Numeric": float, "Float": float,
            }.get(type(sa_type).__name__, str)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize(obj: Any, response_model: type | None = None) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (dict, list)):
        return obj
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
            return {col.key: _to_json(getattr(obj, col.key)) for col in sa_inspect(type(obj)).mapper.column_attrs}
        except Exception:
            pass
    return obj


def _to_json(val: Any) -> Any:
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if hasattr(val, "value"):
        return val.value
    return val


def _attach_includes(data: dict, instance: Any, include: str) -> dict:
    rels = getattr(type(instance), "__relationships__", {})
    for name in (n.strip() for n in include.split(",")):
        if name not in rels:
            continue
        related = getattr(instance, name, None)
        if related is None:
            data[name] = None
        elif isinstance(related, list):
            data[name] = [_serialize(r) for r in related]
        else:
            data[name] = _serialize(related)
    return data


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _parse_body(request: Request) -> dict:
    if await request.body():
        try:
            return await request.json()
        except Exception:
            return {}
    return {}


async def _load(request: Request, cls: type, id: str) -> Any:
    from ironbridge.shared.db import SessionLocal, tenant_session
    from ironbridge.shared.derive.repository import SqlAlchemyRepository
    meta = getattr(cls, "__meta__", {})
    if meta.get("tenant_scoped", False):
        actor = request.state.actor
        with tenant_session(actor.tenant_id) as db:
            instance = SqlAlchemyRepository(db, cls).find_by_id(id)
    else:
        db = SessionLocal()
        try:
            instance = SqlAlchemyRepository(db, cls).find_by_id(id)
        finally:
            db.close()
    if not instance:
        raise HTTPException(404, f"{cls.__name__} not found")
    return instance


async def _paginated_list(request: Request, cls: type, tenant_id: str, **kwargs) -> dict:
    from ironbridge.shared.db import SessionLocal, tenant_session
    from ironbridge.shared.derive.repository import SqlAlchemyRepository
    from ironbridge.shared.framework.read_policy import apply_read_policy

    meta = getattr(cls, "__meta__", {})
    actor = getattr(request.state, "actor", None)

    def _read_filter(q):
        return apply_read_policy(actor, q, cls) if actor else q

    if meta.get("tenant_scoped", False):
        with tenant_session(tenant_id) as db:
            return SqlAlchemyRepository(db, cls).paginate(query_modifier=_read_filter, **kwargs).to_dict()
    else:
        db = SessionLocal()
        try:
            return SqlAlchemyRepository(db, cls).paginate(query_modifier=_read_filter, **kwargs).to_dict()
        finally:
            db.close()


async def _save(request: Request, instance: Any) -> None:
    from ironbridge.shared.db import SessionLocal, tenant_session
    from ironbridge.shared.derive.repository import SqlAlchemyRepository
    meta = getattr(type(instance), "__meta__", {})
    if meta.get("tenant_scoped", False):
        tenant_id = instance.__dict__.get("tenant_id") or instance.__dict__.get("branch_id")
        if not tenant_id:
            actor = getattr(request.state, "actor", None)
            tenant_id = actor.tenant_id if actor else None
        if tenant_id:
            with tenant_session(tenant_id) as db:
                SqlAlchemyRepository(db, type(instance)).save(instance)
    else:
        db = SessionLocal()
        try:
            SqlAlchemyRepository(db, type(instance)).save(instance)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_resource(obj: Any) -> bool:
    from ironbridge.shared.framework.resource import Resource
    return isinstance(obj, Resource)


def _humanize(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").title()


def _snake_plural(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower() + "s"


def _action_summary(cls_name: str, action_name: str, kind: ActionKind) -> str:
    if kind == ActionKind.CREATE:
        return f"Create {cls_name}" if action_name == "create" else f"Create {cls_name} ({_humanize(action_name)})"
    if kind == ActionKind.READ:
        return f"Get {cls_name} by ID" if action_name == "get" else _humanize(action_name)
    if kind == ActionKind.UPDATE:
        return f"Update {cls_name}" if action_name == "update" else _humanize(action_name)
    if kind == ActionKind.DESTROY:
        return f"Delete {cls_name}" if action_name == "delete" else _humanize(action_name)
    return _humanize(action_name)
