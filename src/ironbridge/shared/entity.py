from dataclasses import dataclass
from datetime import UTC, datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(eq=False)
class Entity:
    """Base for all domain entities. Identity by id, not value."""

    id: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, type(self)):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass(eq=False)
class AggregateRoot(Entity):
    """Aggregate roots own their consistency boundary."""
    pass
