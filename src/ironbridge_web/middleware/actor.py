"""
Actor resolution middleware.

Builds an Actor from the incoming request (JWT, session, API key, webhook secret).
Stores it on request.state.actor for use by route handlers.

Pluggable: register your own resolver via set_actor_resolver().
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ironbridge.shared.framework.actor import Actor, Origin


# Default resolver: reads from headers (simple, for dev/testing)
async def _default_resolver(request: Request) -> Actor | None:
    tenant_id = request.headers.get("X-Tenant-Id")
    user_id = request.headers.get("X-User-Id")
    role = request.headers.get("X-User-Role", "viewer")

    if not tenant_id:
        return None

    return Actor(
        id=user_id or "anonymous",
        tenant_id=tenant_id,
        role=role,
        origin=Origin(
            channel="web_dashboard",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
        ),
    )


_resolver: Callable = _default_resolver


def set_actor_resolver(fn: Callable) -> None:
    """Register a custom actor resolver (JWT decoder, session lookup, etc.)."""
    global _resolver
    _resolver = fn


async def resolve_actor(request: Request) -> Actor:
    """Resolve actor from request. Raises 401 if no actor."""
    actor = request.state.actor if hasattr(request.state, "actor") else None
    if actor:
        return actor

    actor = await _resolver(request)
    if actor is None:
        raise HTTPException(401, "Authentication required")
    request.state.actor = actor
    return actor


class ActorMiddleware(BaseHTTPMiddleware):
    """Middleware that resolves and attaches Actor to every request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            actor = await _resolver(request)
            request.state.actor = actor
        except Exception:
            request.state.actor = None
        return await call_next(request)
