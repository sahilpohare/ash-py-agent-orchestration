from datetime import datetime, UTC
from enum import StrEnum

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, guard,
    role_is, anyone, in_state,
    has_many,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class Tenant(Resource):
    """Not tenant_scoped -- it IS the tenant authority."""

    class Meta:
        tenant_scoped = False

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    status: Mapped[TenantStatus] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    users = has_many("User")

    get = default_action(ActionKind.READ)

    @action(kind=ActionKind.CREATE)
    @policy(role_is("system"))
    def create(self, name: str, slug: str) -> "Tenant":
        self.id = _cuid()
        self.name = name
        self.slug = slug
        self.status = TenantStatus.ACTIVE
        return self

    @action(kind=ActionKind.UPDATE)
    @policy(role_is("system"))
    @guard(in_state("ACTIVE", field="status"))
    def suspend(self) -> "Tenant":
        self.status = TenantStatus.SUSPENDED
        return self
