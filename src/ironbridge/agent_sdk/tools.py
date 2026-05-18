"""
BaseTool for the agent SDK.

External agents subclass this to declare tools. The AgentServer introspects
tool metadata to build the /tools endpoint and to surface approval hints
to the platform (requires_approval, approval_prompt).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    name: str
    description: str = ""
    requires_approval: bool = False
    approval_prompt: str = ""   # may reference arg names: "Fetch data for {location}?"

    @abstractmethod
    def run(self, **kwargs: Any) -> Any:
        ...

    def to_llm_schema(self) -> dict:
        """OpenAI-compatible function schema. Override for custom param types."""
        import inspect
        sig = inspect.signature(self.run)
        props = {}
        required = []
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            props[pname] = {"type": "string"}
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }
