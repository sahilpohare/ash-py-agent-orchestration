"""Unit tests for Signal and SignalDef."""

import pytest
from pydantic import BaseModel

from ironbridge.shared.framework.actions import ActionKind
from ironbridge.shared.framework.policies import role_is, system_only, anyone
from ironbridge.shared.framework.signal import Signal, SignalDef, register_signal_transport


# ---------------------------------------------------------------------------
# Signal descriptor
# ---------------------------------------------------------------------------

class TestSignal:
    def test_basic_signal(self):
        s = Signal()
        assert s.kind is None
        assert s.policies == []
        assert s._explicit_input is None

    def test_signal_with_policies(self):
        s = Signal(policies=[role_is("admin")])
        assert len(s.policies) == 1

    def test_signal_with_kind(self):
        s = Signal(kind=ActionKind.CREATE)
        assert s.kind == ActionKind.CREATE

    def test_signal_with_input(self):
        class MyInput(BaseModel):
            amount: float

        s = Signal(input=MyInput)
        assert s._explicit_input is MyInput

    def test_to_def(self):
        s = Signal(kind=ActionKind.CREATE, policies=[system_only()])
        sdef = s.to_def("open", owner_cls=object)

        assert sdef.name == "open"
        assert sdef.kind == ActionKind.CREATE
        assert len(sdef.policies) == 1
        assert sdef.owner_cls is object

    def test_to_def_sets_name_on_signal(self):
        s = Signal()
        s.to_def("my_signal", owner_cls=object)
        assert s.name == "my_signal"

    def test_send_without_transport_raises(self):
        s = Signal()
        s.to_def("test", owner_cls=object)
        with pytest.raises(RuntimeError, match="no send function"):
            s.send("resource-1", {"data": True})

    def test_send_without_def_raises(self):
        s = Signal()
        with pytest.raises(RuntimeError, match="not yet bound"):
            s.send("resource-1", {"data": True})


# ---------------------------------------------------------------------------
# SignalDef
# ---------------------------------------------------------------------------

class TestSignalDef:
    def test_fields(self):
        sdef = SignalDef(
            name="approval",
            kind=None,
            policies=[role_is("admin")],
            input_model=None,
            input_style="none",
            owner_cls=object,
        )
        assert sdef.name == "approval"
        assert sdef.kind is None
        assert len(sdef.policies) == 1

    def test_send_without_transport(self):
        sdef = SignalDef(name="test", kind=None, policies=[], input_model=None, input_style="none")
        with pytest.raises(RuntimeError, match="no send function"):
            sdef.send("resource-1", {})


# ---------------------------------------------------------------------------
# Signal transport
# ---------------------------------------------------------------------------

class TestSignalTransport:
    def test_register_transport(self):
        sent = []

        def mock_transport(signal_def, resource_id, payload, actor=None):
            sent.append((signal_def.name, resource_id, payload))

        register_signal_transport(mock_transport)

        s = Signal(policies=[anyone()])
        sdef = s.to_def("test_signal", owner_cls=object)
        sdef._send_fn = mock_transport  # manually wire since no metaclass

        s.send("res-1", {"key": "value"})

        assert len(sent) == 1
        assert sent[0] == ("test_signal", "res-1", {"key": "value"})

    def test_transport_receives_actor(self):
        from ironbridge.shared.framework.actor import from_request

        received_actor = []

        def mock_transport(signal_def, resource_id, payload, actor=None):
            received_actor.append(actor)

        s = Signal()
        sdef = s.to_def("test", owner_cls=object)
        sdef._send_fn = mock_transport

        actor = from_request("u-1", "t-1", "admin")
        s.send("res-1", {}, actor=actor)

        assert received_actor[0] is actor
