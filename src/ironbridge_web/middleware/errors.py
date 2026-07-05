"""
Error handling middleware.

Maps framework exceptions to HTTP responses:
    PolicyDenied  -> 403
    GuardFailed   -> 409
    ValueError    -> 400
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ironbridge.shared.framework.enforcement import PolicyDenied, GuardFailed


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PolicyDenied)
    async def policy_denied_handler(request: Request, exc: PolicyDenied) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "message": str(exc),
                "policy": exc.policy_name,
            },
        )

    @app.exception_handler(GuardFailed)
    async def guard_failed_handler(request: Request, exc: GuardFailed) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": "conflict",
                "message": str(exc),
                "guard": exc.guard_name,
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": "bad_request",
                "message": str(exc),
            },
        )
