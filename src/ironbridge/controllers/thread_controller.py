"""
Thread / Channel API controller.

Thin controller — channel-specific endpoints live in their adapter's get_router().
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api")
