"""
Component registry. Maps component names to their file templates.

Each component defines:
    - files: dict of {target_path: content}
    - depends: list of other components this requires
    - description: human-readable description
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Component:
    name: str
    description: str
    files: dict[str, str]  # {relative_path: content}
    depends: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)  # {var_name: default_value}

    def render(self, variables: dict[str, str] | None = None) -> dict[str, str]:
        """Render all files with variable substitution."""
        vars = {**self.variables, **(variables or {})}
        rendered = {}
        for path, content in self.files.items():
            rendered_path = _substitute(path, vars)
            rendered_content = _substitute(content, vars)
            rendered[rendered_path] = rendered_content
        return rendered


def _substitute(template: str, variables: dict[str, str]) -> str:
    """Replace {{var_name}} with values."""
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", value)
    return result


# ---------------------------------------------------------------------------
# Built-in components
# ---------------------------------------------------------------------------

_COMPONENTS: dict[str, Component] = {}


def register(component: Component) -> None:
    _COMPONENTS[component.name] = component


def get(name: str) -> Component | None:
    return _COMPONENTS.get(name)


def list_all() -> list[Component]:
    return list(_COMPONENTS.values())


# ---------------------------------------------------------------------------
# Load built-in components
# ---------------------------------------------------------------------------

def _register_builtins() -> None:
    register(_tenancy_component())
    register(_threads_component())
    register(_soft_delete_component())
    register(_timestamps_component())
    register(_auth_component())


def _tenancy_component() -> Component:
    return Component(
        name="tenancy",
        description="Multi-tenant identity: Branch, PlatformUser, BranchMember with JWT actor resolver",
        depends=["auth"],
        variables={"app_name": "lightwork"},
        files={
            "src/{{app_name}}/identity/__init__.py": """from .branch import Branch
from .platform_user import PlatformUser
from .branch_member import BranchMember
""",

            "src/{{app_name}}/identity/branch.py": """from datetime import datetime, UTC

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, role_is, has_many,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class Branch(Resource):
    \\"\\"\\"A tenant branch. Not tenant-scoped - it IS the tenant.\\"\\"\\"

    class Meta:
        tenant_scoped = False
        default_actions = ["get", "list"]

    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    members = has_many("BranchMember", key="branch_id")

    @action(kind=ActionKind.CREATE)
    @policy(role_is("system", "admin"))
    def create(self, name: str, slug: str) -> "Branch":
        self.id = _cuid()
        self.name = name
        self.slug = slug
        self.active = True
        return self
""",

            "src/{{app_name}}/identity/platform_user.py": """from datetime import datetime, UTC

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, role_is, has_many,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class PlatformUser(Resource):
    \\"\\"\\"Platform user. Linked to external auth via auth_user_id.\\"\\"\\"

    class Meta:
        tenant_scoped = False
        default_actions = ["get", "list"]

    __tablename__ = "platform_users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    auth_user_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    memberships = has_many("BranchMember", key="platform_user_id")

    @action(kind=ActionKind.CREATE)
    @policy(role_is("system"))
    def create(self, auth_user_id: str, name: str, email: str) -> "PlatformUser":
        self.id = _cuid()
        self.auth_user_id = auth_user_id
        self.name = name
        self.email = email.lower().strip()
        return self
""",

            "src/{{app_name}}/identity/branch_member.py": """from datetime import datetime, UTC

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, guard, role_is, same_tenant, in_state,
    belongs_to,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class BranchMember(Resource):
    \\"\\"\\"User's membership and role within a branch.\\"\\"\\"

    class Meta:
        tenant_scoped = True
        default_actions = ["get", "list"]

    __tablename__ = "branch_members"
    __table_args__ = (
        UniqueConstraint("platform_user_id", "branch_id", name="uq_branch_member"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    platform_user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    branch_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="viewer")
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user = belongs_to("PlatformUser", key="platform_user_id")
    branch = belongs_to("Branch", key="branch_id")

    @action(kind=ActionKind.CREATE)
    @policy(role_is("admin", "system"))
    @policy(same_tenant())
    def invite(self, platform_user_id: str, role: str = "viewer") -> "BranchMember":
        self.id = _cuid()
        self.platform_user_id = platform_user_id
        self.role = role
        return self

    @action(kind=ActionKind.UPDATE)
    @policy(role_is("admin"))
    @policy(same_tenant())
    def change_role(self, role: str) -> "BranchMember":
        self.role = role
        return self
""",

            "src/{{app_name}}/identity/module.py": """from ironbridge.shared.framework import Module, Providers

from .branch import Branch
from .platform_user import PlatformUser
from .branch_member import BranchMember


class IdentityModule(Module):
    prefix = "/identity"
    resources = [Branch, PlatformUser, BranchMember]

    @classmethod
    def on_init(cls, providers: Providers):
        pass

    @classmethod
    def on_ready(cls):
        pass
""",

            "tests/identity/__init__.py": "",

            "tests/identity/test_branch_member.py": """import pytest
from dataclasses import dataclass
from ironbridge.shared.framework import InMemoryRepository


@dataclass
class FakeBranchMember:
    id: str = "bm-1"
    platform_user_id: str = "pu-1"
    branch_id: str = "b-1"
    role: str = "viewer"
    status: str = "active"


@pytest.fixture(autouse=True)
def clean():
    InMemoryRepository.clear_all()
    yield
    InMemoryRepository.clear_all()


class TestBranchMember:
    def test_create(self):
        repo = InMemoryRepository(FakeBranchMember)
        repo.save(FakeBranchMember(id="bm-1"))
        assert repo.find_by_id("bm-1") is not None

    def test_find_by_branch(self):
        repo = InMemoryRepository(FakeBranchMember)
        repo.save(FakeBranchMember(id="bm-1", branch_id="b-1"))
        repo.save(FakeBranchMember(id="bm-2", branch_id="b-2"))
        assert len(repo.list(branch_id="b-1")) == 1
""",
        },
    )


def _auth_component() -> Component:
    return Component(
        name="auth",
        description="JWT actor resolver middleware",
        variables={"app_name": "lightwork"},
        files={
            "src/{{app_name}}_web/auth.py": """from fastapi import Request, HTTPException

from ironbridge.shared.framework.actor import Actor, Origin


async def resolve_actor_from_jwt(request: Request) -> Actor:
    \\"\\"\\"
    Resolve Actor from JWT Bearer token.
    TODO: Replace with your JWT verification logic.
    \\"\\"\\"
    auth = request.headers.get("Authorization", "")

    if auth.startswith("Bearer "):
        token = auth[7:]
        # TODO: verify JWT and extract claims
        # claims = verify_jwt(token)
        claims = _parse_dev_token(token)

        return Actor(
            id=claims.get("platform_user_id", "anonymous"),
            tenant_id=claims.get("branch_id", "default"),
            role=claims.get("role", "viewer"),
            origin=Origin(
                channel="web_dashboard",
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent"),
            ),
            metadata=claims,
        )

    # Fallback: header-based (for dev/testing)
    return Actor(
        id=request.headers.get("X-User-Id", "anonymous"),
        tenant_id=request.headers.get("X-Tenant-Id", "default"),
        role=request.headers.get("X-User-Role", "viewer"),
        origin=Origin(
            channel="web_dashboard",
            ip=request.client.host if request.client else None,
        ),
    )


def _parse_dev_token(token: str) -> dict:
    \\"\\"\\"Dev-only: parse a simple base64 JSON token. Replace with real JWT.\\"\\"\\"
    import base64, json
    try:
        payload = base64.b64decode(token + "==")
        return json.loads(payload)
    except Exception:
        return {}
""",
        },
    )


def _threads_component() -> Component:
    return Component(
        name="threads",
        description="Conversation threads with ordered messages, DBOS-backed queue for serialization",
        variables={"app_name": "lightwork"},
        files={
            "src/{{app_name}}/sessions/__init__.py": """from .thread import Thread
from .message import Message, MessageRole, ParticipantType
from .handle import ThreadHandle, make_thread_handle
""",

            "src/{{app_name}}/sessions/thread.py": """from datetime import datetime, UTC

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    has_many,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class Thread(Resource):
    \\"\\"\\"Ordered conversation log. Infrastructure resource.\\"\\"\\"

    class Meta:
        tenant_scoped = True
        default_actions = ["get"]

    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    messages = has_many("Message", key="thread_id")
""",

            "src/{{app_name}}/sessions/message.py": """from datetime import datetime, UTC
from enum import StrEnum

from cuid2 import cuid_wrapper
from sqlalchemy import BigInteger, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, default_action, ActionKind,
    belongs_to,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class ParticipantType(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class Message(Resource):
    \\"\\"\\"A single message in a thread. Append-only.\\"\\"\\"

    class Meta:
        tenant_scoped = True
        default_actions = ["get", "list"]
        conflict_columns = ("thread_id", "idempotency_key")
        conflict_action = "nothing"

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "idempotency_key", name="uq_messages_thread_idempotency"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    thread_id: Mapped[str] = mapped_column(
        String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    participant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    participant_type: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    thread = belongs_to("Thread")
""",

            "src/{{app_name}}/sessions/handle.py": """from datetime import datetime, UTC
from typing import Any

from cuid2 import cuid_wrapper

_cuid = cuid_wrapper()


class ThreadHandle:
    \\"\\"\\"
    Handle for interacting with a thread. Available on ctx.thread.

    Usage in workflows:
        ctx.thread.add_text("Job opened", role="SYSTEM")
        ctx.thread.add_event("state_changed", old="opened", new="sourcing")
        history = ctx.thread.get_history()
    \\"\\"\\"

    def __init__(self, thread_id: str, save_fn: Any = None, list_fn: Any = None):
        self._thread_id = thread_id
        self._save_fn = save_fn
        self._list_fn = list_fn
        self._position = 0

    @property
    def id(self) -> str:
        return self._thread_id

    def add_message(
        self,
        content: dict,
        participant_id: str = "system",
        participant_type: str = "SYSTEM",
        role: str = "SYSTEM",
        idempotency_key: str | None = None,
    ) -> dict:
        self._position += 1
        ikey = idempotency_key or f"{self._thread_id}:{self._position}"
        msg = {
            "id": _cuid(),
            "thread_id": self._thread_id,
            "participant_id": participant_id,
            "participant_type": participant_type,
            "role": role,
            "content": content,
            "position": self._position,
            "idempotency_key": ikey,
            "created_at": datetime.now(UTC).isoformat(),
        }
        if self._save_fn:
            self._save_fn(msg)
        return msg

    def add_text(self, text: str, participant_id: str = "system",
                 participant_type: str = "SYSTEM", role: str = "SYSTEM") -> dict:
        return self.add_message(
            content={"version": 1, "parts": [{"type": "text", "text": text}]},
            participant_id=participant_id, participant_type=participant_type, role=role,
        )

    def add_event(self, event: str, **data) -> dict:
        return self.add_message(
            content={"version": 1, "parts": [{"type": "event", "event": event, **data}]},
        )

    def get_messages(self, limit: int = 200, exclude_types: list[str] | None = None) -> list[dict]:
        if not self._list_fn:
            return []
        messages = self._list_fn(self._thread_id, limit)
        if exclude_types:
            return [m for m in messages
                    if not any(p.get("type") in exclude_types
                              for p in m.get("content", {}).get("parts", []))]
        return messages

    def get_history(self, limit: int = 200) -> list[dict]:
        return self.get_messages(limit=limit, exclude_types=["response_reply", "event"])


def make_thread_handle(thread_id: str, save_fn: Any = None, list_fn: Any = None) -> ThreadHandle:
    return ThreadHandle(thread_id=thread_id, save_fn=save_fn, list_fn=list_fn)
""",
        },
    )


def _soft_delete_component() -> Component:
    return Component(
        name="soft-delete",
        description="Adds is_deleted + deleted_at fields and not_deleted guard. Copy into your resource.",
        files={
            "SOFT_DELETE_SNIPPET.md": """# Soft Delete

Add these to your resource:

```python
from sqlalchemy import Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from ironbridge.shared.framework import guard, not_deleted, ActionKind, action, policy, role_is

# Fields
is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

# Action
@action(kind=ActionKind.DESTROY)
@policy(role_is("admin"))
@guard(not_deleted())
def archive(self) -> "YourResource":
    self.is_deleted = True
    self.deleted_at = datetime.now(UTC)
    return self
```

Add `@guard(not_deleted())` to UPDATE actions that shouldn't work on deleted resources.
""",
        },
    )


def _timestamps_component() -> Component:
    return Component(
        name="timestamps",
        description="Adds created_at + updated_at fields. Copy into your resource.",
        files={
            "TIMESTAMPS_SNIPPET.md": """# Timestamps

Add these to your resource:

```python
from datetime import datetime, UTC
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

_utcnow = lambda: datetime.now(UTC)

# Fields
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
```

The generator already includes these by default.
""",
        },
    )


# Register on import
_register_builtins()
