from datetime import UTC, datetime
from enum import StrEnum

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import ActionKind, Resource, action

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)  # noqa: E731


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class Tenant(Resource):
    class Meta:
        tenant_scoped = False
        restate_object = True

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    status: Mapped[TenantStatus] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    @action(kind=ActionKind.CREATE)
    def create(self, name: str, slug: str) -> "Tenant":
        self.id = _cuid()
        self.name = name
        self.slug = slug
        self.status = TenantStatus.ACTIVE
        return self

    @action(kind=ActionKind.UPDATE)
    def suspend(self) -> "Tenant":
        self.status = TenantStatus.SUSPENDED
        return self

    @action(kind=ActionKind.READ)
    def get(self) -> "Tenant":
        return self
