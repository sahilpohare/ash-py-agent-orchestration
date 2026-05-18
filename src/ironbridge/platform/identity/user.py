from datetime import UTC, datetime
from enum import StrEnum

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import ActionKind, Resource, action

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)  # noqa: E731


class UserRole(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DEACTIVATED = "DEACTIVATED"


class User(Resource):
    class Meta:
        tenant_scoped = True
        restate_object = True

    __tablename__ = "users"

    id         : Mapped[str]        = mapped_column(String, primary_key=True, default=_cuid)
    email      : Mapped[str]        = mapped_column(String, nullable=False, index=True)
    name       : Mapped[str]        = mapped_column(String, nullable=False)
    role       : Mapped[UserRole]   = mapped_column(String, nullable=False)
    status     : Mapped[UserStatus] = mapped_column(String, nullable=False)
    created_at : Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at : Mapped[datetime]   = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @action(kind=ActionKind.CREATE)
    def create(self, email: str, name: str, role: str = "MEMBER") -> "User":
        self.id = _cuid()
        self.email = email.lower().strip()
        self.name = name
        self.role = UserRole(role)
        self.status = UserStatus.ACTIVE
        return self

    @action(kind=ActionKind.UPDATE)
    def change_role(self, new_role: str) -> "User":
        self.role = UserRole(new_role)
        return self

    @action(kind=ActionKind.UPDATE)
    def deactivate(self) -> "User":
        self.status = UserStatus.DEACTIVATED
        return self

    @action(kind=ActionKind.READ)
    def get(self) -> "User":
        return self
