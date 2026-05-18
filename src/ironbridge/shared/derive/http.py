"""
Derives Starlette routes from a Resource's @actions.
Exclusive/shared actions → POST /{resource}/{id}/{action}
Stream actions           → GET  /{resource}/{id}/{action}  (SSE via Restate attach)
"""

from __future__ import annotations

import os

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ironbridge.shared.framework.actions import ActionKind
from ironbridge.shared.framework.resource import Resource

RESTATE_BASE = os.environ.get("RESTATE_URL", "http://localhost:8080")


def derive_routes(resource_cls: type[Resource]) -> list[Route]:
    routes: list[Route] = []
    resource_name = resource_cls.__name__
    # e.g. Thread → /threads
    prefix = f"/{_snake(resource_name)}s"

    for action_name, action_meta in resource_cls.__actions__.items():
        if action_meta.kind == ActionKind.STREAM:
            routes.append(
                Route(
                    f"{prefix}/{{id}}/{action_name}",
                    endpoint=_make_sse_handler(resource_name, action_name),
                    methods=["GET"],
                )
            )
        else:
            routes.append(
                Route(
                    f"{prefix}/{{id}}/{action_name}",
                    endpoint=_make_post_handler(resource_name, action_name),
                    methods=["POST"],
                )
            )

    return routes


def _make_post_handler(resource_name: str, action_name: str):
    async def handler(request: Request) -> JSONResponse:
        id_ = request.path_params["id"]
        request.headers.get("X-Tenant-Id", "")
        idempotency_key = request.headers.get("Idempotency-Key")
        body = await request.json()

        headers = {"Content-Type": "application/json"}
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key

        url = f"{RESTATE_BASE}/{resource_name}/{id_}/{action_name}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=body, headers=headers)

        return JSONResponse(resp.json(), status_code=resp.status_code)

    return handler


def _make_sse_handler(resource_name: str, action_name: str):
    async def handler(request: Request) -> StreamingResponse:
        id_ = request.path_params["id"]
        invocation_id = request.query_params.get("invocation_id")

        if not invocation_id:
            # Fire the action first, get invocation ID
            url = f"{RESTATE_BASE}/{resource_name}/{id_}/{action_name}/send"
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={})
            invocation_id = resp.json().get("invocationId")

        attach_url = f"{RESTATE_BASE}/restate/invocation/{invocation_id}/attach"

        async def stream():
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", attach_url) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(stream(), media_type="text/event-stream")

    return handler


def _snake(name: str) -> str:
    import re

    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
