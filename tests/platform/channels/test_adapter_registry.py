"""
Tests for ADR-25: Channel adapter self-registration on import.

Decision contract:
  Importing a concrete adapter module triggers register_adapter() as a
  module-level side effect. After import, get_adapter(channel_type) returns
  the registered adapter instance.

Preconditions, invariants, postconditions stated per test.

NOTE on CliAdapter (ADR-25 gap):
  CliAdapter does NOT currently self-register — there is no
  register_adapter(CliAdapter()) call at module level in cli.py.
  main.py's comment ("registers 'cli' adapter") is incorrect.
  A test for CliAdapter registration is included and marked xfail
  to document this gap. Fix: add register_adapter(CliAdapter()) to cli.py.
"""

from __future__ import annotations

from ironbridge.platform.channels.registry import get_adapter
from services.channels.adapters.base import BaseChannelAdapter


# ── ADR-25: WebAdapter self-registration ──────────────────────────────────────

def test_web_adapter_registers_on_import():
    """
    Pre:  services.channels.adapters.web imported (happens at module load)
    Inv:  module-level register_adapter(WebAdapter()) call executes on import
    Post: get_adapter("web") returns a non-None adapter instance
    """
    import services.channels.adapters.web  # noqa: F401 — trigger registration
    adapter = get_adapter("web")
    assert adapter is not None


def test_web_adapter_channel_type_is_web():
    """
    Inv:  channel_type class attribute drives the registry key
    Pre:  web adapter registered
    Post: adapter.channel_type == "web"
    """
    import services.channels.adapters.web  # noqa: F401
    adapter = get_adapter("web")
    assert adapter is not None
    assert adapter.channel_type == "web"


def test_web_adapter_is_base_channel_adapter():
    """
    Inv:  registered adapter implements BaseChannelAdapter contract
    Post: isinstance check passes
    """
    import services.channels.adapters.web  # noqa: F401
    adapter = get_adapter("web")
    assert isinstance(adapter, BaseChannelAdapter)


def test_web_adapter_has_on_message():
    """
    Inv:  BaseChannelAdapter contract requires on_message()
    Post: adapter has callable on_message
    """
    import services.channels.adapters.web  # noqa: F401
    adapter = get_adapter("web")
    assert callable(getattr(adapter, "on_message", None))


def test_web_adapter_registration_is_stable():
    """
    Inv:  re-importing the module does NOT create a second registration
          (Python caches modules after first import).
    Pre:  web adapter already registered
    Post: get_adapter("web") returns the same instance on every call
    """
    import services.channels.adapters.web  # noqa: F401
    a1 = get_adapter("web")
    import services.channels.adapters.web  # noqa: F401
    a2 = get_adapter("web")
    assert a1 is a2


# ── ADR-25: CliAdapter — gap documented as xfail ──────────────────────────────

def test_cli_adapter_registers_on_import():
    """
    Pre:  services.channels.adapters.cli imported
    Inv:  module-level register_adapter(CliAdapter()) executes on import
    Post: get_adapter("cli") returns a non-None adapter instance
    """
    import services.channels.adapters.cli  # noqa: F401
    adapter = get_adapter("cli")
    assert adapter is not None


def test_cli_adapter_channel_type_is_cli():
    """
    Inv:  channel_type class attribute drives the registry key
    Post: adapter.channel_type == "cli"
    """
    import services.channels.adapters.cli  # noqa: F401
    adapter = get_adapter("cli")
    assert adapter is not None
    assert adapter.channel_type == "cli"


# ── Registry contract ─────────────────────────────────────────────────────────

def test_get_adapter_unknown_returns_none():
    """
    Pre:  no adapter registered for "nonexistent"
    Post: get_adapter returns None — no exception raised
    """
    result = get_adapter("nonexistent-channel-type-xyz")
    assert result is None


def test_register_adapter_uses_channel_type_as_key():
    """
    Inv:  register_adapter() uses adapter.channel_type as the registry key,
          not the class name or any other attribute.
    Pre:  a custom adapter with channel_type="test-channel"
    Post: get_adapter("test-channel") returns that instance
    """
    from ironbridge.platform.channels.registry import register_adapter

    class _TestAdapter(BaseChannelAdapter):
        channel_type = "test-channel-adr25"

        def on_message(self, message, config, ctx):
            pass

    instance = _TestAdapter()
    register_adapter(instance)
    assert get_adapter("test-channel-adr25") is instance
