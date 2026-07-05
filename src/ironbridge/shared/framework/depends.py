"""
Dependency injection for modules.

Each module declares what it needs. The framework resolves dependencies
at startup and injects them into WorkflowContext.

    class MaintenanceModule(Module):
        prefix = "/maintenance"
        resources = [Job]
        depends = {
            "messaging": TwilioMessaging,
            "contractors": ContractorRepo,
        }

    # In the workflow
    @workflow
    async def on_start(self, ctx, description: str):
        contractor = ctx.deps.contractors.find(self.branch_id)
        await ctx.deps.messaging.send(self, contractor)

Dependencies are resolved from a provider registry. Providers are
registered at app startup. Modules declare what they need by name + type.

    # App startup
    providers.register("messaging", TwilioMessaging(twilio_client))
    providers.register("contractors", ContractorRepo(db))

    # Or with a factory
    providers.register("messaging", lambda: TwilioMessaging(os.environ["TWILIO_SID"]))

Modules can also provide their own dependencies for other modules to use.
"""
from __future__ import annotations

from typing import Any, Callable


class Providers:
    """Registry of dependency providers. Singleton per app."""

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, Callable] = {}

    def register(self, name: str, provider: Any) -> None:
        """Register a dependency by name. Can be an instance or a factory."""
        if callable(provider) and not isinstance(provider, type):
            # It's a factory function
            self._factories[name] = provider
        else:
            # It's an instance
            self._instances[name] = provider

    def resolve(self, name: str) -> Any:
        """Resolve a dependency by name. Lazy-initializes factories."""
        if name in self._instances:
            return self._instances[name]
        if name in self._factories:
            instance = self._factories[name]()
            self._instances[name] = instance
            return instance
        raise KeyError(f"No provider registered for '{name}'")

    def has(self, name: str) -> bool:
        return name in self._instances or name in self._factories

    def all(self) -> dict[str, Any]:
        """Resolve and return all registered dependencies."""
        for name in list(self._factories.keys()):
            if name not in self._instances:
                self.resolve(name)
        return dict(self._instances)


class Deps:
    """
    Dependency accessor on WorkflowContext. Attribute-style access.

        ctx.deps.messaging.send(...)
        ctx.deps.contractors.find(...)
    """

    def __init__(self, providers: Providers) -> None:
        self._providers = providers

    def __getattr__(self, name: str) -> Any:
        try:
            return self._providers.resolve(name)
        except KeyError:
            raise AttributeError(f"No dependency '{name}' registered")

    def get(self, name: str, default: Any = None) -> Any:
        try:
            return self._providers.resolve(name)
        except KeyError:
            return default


# Global providers instance
_providers = Providers()


def get_providers() -> Providers:
    return _providers


def set_providers(providers: Providers) -> None:
    global _providers
    _providers = providers
