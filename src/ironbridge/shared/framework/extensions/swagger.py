"""
Swagger extension - auto-generates OpenAPI schema metadata from resources.

Enriches derived routes with:
- Operation summaries and descriptions from action docstrings
- Request/response schemas from introspected input/output models
- Tags from resource name
- Parameter descriptions from field annotations
- Enum values for string enum fields
- Relationship links

Usage:
    class MaintenanceJob(Workflow):
        class Meta:
            extensions = [Swagger(tag="Maintenance")]

Or at the module level:
    class MaintenanceModule(Module):
        extensions = [Swagger()]
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, create_model
from sqlalchemy import inspect as sa_inspect

from ironbridge.shared.framework.extension import Extension
from ironbridge.shared.framework.actions import ActionKind, ActionMeta
from ironbridge.shared.framework.signal import SignalDef


class Swagger(Extension):
    """Enriches FastAPI routes with OpenAPI metadata."""

    def __init__(
        self,
        tag: str | None = None,
        description: str | None = None,
        include_schemas: bool = True,
    ):
        self.tag = tag
        self.description = description
        self.include_schemas = include_schemas

    def on_resource(self, cls: type) -> None:
        """Build response schema from resource columns."""
        if not self.include_schemas:
            return

        response_model = _build_response_model(cls)
        if response_model:
            cls.__swagger__ = {
                "tag": self.tag or cls.__name__,
                "description": self.description or cls.__doc__ or "",
                "response_model": response_model,
                "actions": {},
                "signals": {},
            }
        else:
            cls.__swagger__ = {
                "tag": self.tag or cls.__name__,
                "description": self.description or cls.__doc__ or "",
                "actions": {},
                "signals": {},
            }

    def on_action(self, cls: type, action_name: str, action_meta: ActionMeta) -> None:
        """Build per-action OpenAPI metadata."""
        swagger = getattr(cls, "__swagger__", None)
        if not swagger:
            return

        summary = _action_summary(cls.__name__, action_name, action_meta)
        desc = _action_description(action_meta)

        swagger["actions"][action_name] = {
            "summary": summary,
            "description": desc,
            "input_model": action_meta.input_model,
            "output_model": action_meta.output_model,
            "kind": action_meta.kind.value,
            "method": _kind_to_method(action_meta.kind, action_name),
        }

    def on_signal(self, cls: type, signal_name: str, signal_def: SignalDef) -> None:
        """Build per-signal OpenAPI metadata."""
        swagger = getattr(cls, "__swagger__", None)
        if not swagger:
            return

        is_create = signal_def.kind == ActionKind.CREATE

        swagger["signals"][signal_name] = {
            "summary": f"{'Create' if is_create else 'Signal'}: {_humanize(signal_name)}",
            "description": f"{'Creates a new ' + cls.__name__ + ' and starts workflow' if is_create else 'Sends signal to running workflow'}. Returns 202 Accepted.",
            "input_model": signal_def.input_model,
            "is_create": is_create,
            "method": "POST",
        }

    def on_route_derived(self, router: Any, cls: type) -> None:
        """Apply OpenAPI metadata to the derived FastAPI routes."""
        swagger = getattr(cls, "__swagger__", None)
        if not swagger:
            return

        for route in router.routes:
            route_name = getattr(route, "name", "")
            cls_prefix = cls.__name__ + "_"

            if not route_name.startswith(cls_prefix):
                continue

            action_or_signal = route_name[len(cls_prefix):]

            # Apply tag
            if hasattr(route, "tags"):
                route.tags = [swagger["tag"]]

            # Apply action metadata
            if action_or_signal in swagger["actions"]:
                meta = swagger["actions"][action_or_signal]
                if hasattr(route, "summary"):
                    route.summary = meta["summary"]
                if hasattr(route, "description"):
                    route.description = meta["description"]

            # Apply signal metadata
            if action_or_signal in swagger["signals"]:
                meta = swagger["signals"][action_or_signal]
                if hasattr(route, "summary"):
                    route.summary = meta["summary"]
                if hasattr(route, "description"):
                    route.description = meta["description"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_response_model(cls: type) -> type[BaseModel] | None:
    """Build a Pydantic response model from SQLAlchemy columns."""
    try:
        mapper = sa_inspect(cls)
    except Exception:
        return None

    fields: dict[str, Any] = {}
    for col in mapper.mapper.column_attrs:
        col_obj = mapper.mapper.columns.get(col.key)
        if col_obj is None:
            continue

        py_type = _sa_type_to_python(col_obj.type)
        if col_obj.nullable:
            py_type = py_type | None
            fields[col.key] = (py_type, None)
        else:
            fields[col.key] = (py_type, ...)

    if not fields:
        return None

    return create_model(f"{cls.__name__}Response", **fields)


def _sa_type_to_python(sa_type: Any) -> type:
    """Map SQLAlchemy type to Python type for Pydantic."""
    from sqlalchemy import String, Integer, BigInteger, Boolean, DateTime, JSON, Numeric, Float, Text

    type_name = type(sa_type).__name__

    mapping = {
        "String": str,
        "Text": str,
        "Integer": int,
        "BigInteger": int,
        "Boolean": bool,
        "DateTime": str,  # serialize as ISO string
        "JSON": dict,
        "ARRAY": list,
        "Numeric": float,
        "Float": float,
    }

    return mapping.get(type_name, str)


def _action_summary(cls_name: str, action_name: str, meta: ActionMeta) -> str:
    """Generate a human-readable summary."""
    kind = meta.kind

    if kind == ActionKind.CREATE:
        return f"Create {cls_name}" if action_name == "create" else f"Create {cls_name} via {_humanize(action_name)}"
    if kind == ActionKind.READ:
        if action_name == "get":
            return f"Get {cls_name} by ID"
        if action_name == "list":
            return f"List {cls_name}s"
        return f"{_humanize(action_name)} {cls_name}"
    if kind == ActionKind.UPDATE:
        if action_name == "update":
            return f"Update {cls_name}"
        return f"{_humanize(action_name)}"
    if kind == ActionKind.DESTROY:
        if action_name == "delete":
            return f"Delete {cls_name}"
        return f"{_humanize(action_name)}"
    if kind == ActionKind.ACTION:
        return _humanize(action_name)

    return action_name


def _action_description(meta: ActionMeta) -> str:
    """Extract description from the action function's docstring."""
    doc = getattr(meta.fn, "__doc__", None)
    if doc:
        return doc.strip()
    return ""


def _kind_to_method(kind: ActionKind, name: str) -> str:
    if kind == ActionKind.CREATE:
        return "POST"
    if kind == ActionKind.READ:
        return "GET"
    if kind == ActionKind.UPDATE:
        return "PATCH" if name == "update" else "POST"
    if kind == ActionKind.DESTROY:
        return "DELETE"
    return "POST"


def _humanize(name: str) -> str:
    """snake_case -> Title Case."""
    return name.replace("_", " ").title()
