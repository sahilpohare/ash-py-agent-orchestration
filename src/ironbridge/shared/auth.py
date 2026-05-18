"""
Auth primitives.

AuthContext is extracted from the request body for every Restate handler.
Both tenant_id and user_name are required — missing either raises TerminalError(401).

For FastAPI routes, extract_headers() pulls from HTTP headers instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from restate.exceptions import TerminalError


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    user_name: str


def extract_auth(req: dict) -> AuthContext:
    """Extract and validate auth from a Restate handler request body."""
    tenant_id: str | None = req.get("tenant_id") or None
    user_name: str | None = req.get("user_name") or None

    if not tenant_id:
        raise TerminalError("tenant_id required", status_code=401)
    if not user_name:
        raise TerminalError("user_name required", status_code=401)

    return AuthContext(tenant_id=tenant_id, user_name=user_name)
