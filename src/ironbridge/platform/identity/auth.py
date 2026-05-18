"""
AuthContext — identity of the caller on every request.

Restate handlers: extracted from request body (tenant_id + user_name fields).
FastAPI routes: extracted from X-Tenant-Id + X-User-Name headers.

Missing either field is an immediate 401.
"""

from __future__ import annotations

from pydantic import BaseModel
from restate.exceptions import TerminalError


class AuthContext(BaseModel):
    tenant_id: str
    user_name: str


def extract_auth(req: dict) -> AuthContext:
    """For Restate handler request bodies."""
    tenant_id: str | None = req.get("tenant_id") or None
    user_name: str | None = req.get("user_name") or None

    if not tenant_id:
        raise TerminalError("tenant_id required", status_code=401)
    if not user_name:
        raise TerminalError("user_name required", status_code=401)

    return AuthContext(tenant_id=tenant_id, user_name=user_name)
