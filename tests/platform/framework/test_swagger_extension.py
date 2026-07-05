"""Unit tests for Swagger extension."""

import pytest
from unittest.mock import MagicMock

from ironbridge.shared.framework.actions import ActionKind, ActionMeta
from ironbridge.shared.framework.signal import SignalDef
from ironbridge.shared.framework.extensions.swagger import Swagger, _humanize, _action_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_resource(name, doc=None):
    cls = type(name, (), {
        "__name__": name,
        "__doc__": doc,
        "__meta__": {"table": name.lower() + "s"},
        "__actions__": {},
        "__signals__": {},
        "__relationships__": {},
    })
    return cls


def make_action_meta(name, kind, fn=None, input_model=None, output_model=None):
    if fn is None:
        fn = lambda self: self
    return ActionMeta(
        name=name,
        kind=kind,
        fn=fn,
        input_model=input_model,
        output_model=output_model,
    )


def make_signal_def(name, kind=None, input_model=None):
    return SignalDef(
        name=name,
        kind=kind,
        policies=[],
        input_model=input_model,
        input_style="none" if input_model is None else "model",
    )


# ---------------------------------------------------------------------------
# Tests: on_resource
# ---------------------------------------------------------------------------

class TestOnResource:
    def test_creates_swagger_metadata(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        assert hasattr(cls, "__swagger__")
        assert cls.__swagger__["tag"] == "Job"

    def test_custom_tag(self):
        ext = Swagger(tag="Maintenance")
        cls = make_resource("Job")
        ext.on_resource(cls)

        assert cls.__swagger__["tag"] == "Maintenance"

    def test_custom_description(self):
        ext = Swagger(description="Maintenance jobs API")
        cls = make_resource("Job")
        ext.on_resource(cls)

        assert cls.__swagger__["description"] == "Maintenance jobs API"

    def test_description_from_docstring(self):
        ext = Swagger()
        cls = make_resource("Job", doc="A maintenance job resource.")
        ext.on_resource(cls)

        assert cls.__swagger__["description"] == "A maintenance job resource."


# ---------------------------------------------------------------------------
# Tests: on_action
# ---------------------------------------------------------------------------

class TestOnAction:
    def test_creates_action_metadata(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        meta = make_action_meta("create", ActionKind.CREATE)
        ext.on_action(cls, "create", meta)

        assert "create" in cls.__swagger__["actions"]
        assert cls.__swagger__["actions"]["create"]["summary"] == "Create Job"
        assert cls.__swagger__["actions"]["create"]["method"] == "POST"

    def test_get_action(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        meta = make_action_meta("get", ActionKind.READ)
        ext.on_action(cls, "get", meta)

        assert cls.__swagger__["actions"]["get"]["summary"] == "Get Job by ID"
        assert cls.__swagger__["actions"]["get"]["method"] == "GET"

    def test_list_action(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        meta = make_action_meta("list", ActionKind.READ)
        ext.on_action(cls, "list", meta)

        assert cls.__swagger__["actions"]["list"]["summary"] == "List Jobs"

    def test_custom_action(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        meta = make_action_meta("approve_quote", ActionKind.ACTION)
        ext.on_action(cls, "approve_quote", meta)

        assert cls.__swagger__["actions"]["approve_quote"]["summary"] == "Approve Quote"
        assert cls.__swagger__["actions"]["approve_quote"]["method"] == "POST"

    def test_update_action(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        meta = make_action_meta("update", ActionKind.UPDATE)
        ext.on_action(cls, "update", meta)

        assert cls.__swagger__["actions"]["update"]["method"] == "PATCH"

    def test_destroy_action(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        meta = make_action_meta("delete", ActionKind.DESTROY)
        ext.on_action(cls, "delete", meta)

        assert cls.__swagger__["actions"]["delete"]["method"] == "DELETE"

    def test_custom_create_action(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        meta = make_action_meta("open_from_call", ActionKind.CREATE)
        ext.on_action(cls, "open_from_call", meta)

        assert cls.__swagger__["actions"]["open_from_call"]["summary"] == "Create Job via Open From Call"

    def test_description_from_docstring(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        def approve_quote(self):
            """Approve the contractor's quote and move to booking."""
            pass

        meta = make_action_meta("approve_quote", ActionKind.ACTION, fn=approve_quote)
        ext.on_action(cls, "approve_quote", meta)

        assert cls.__swagger__["actions"]["approve_quote"]["description"] == "Approve the contractor's quote and move to booking."

    def test_input_model_stored(self):
        from pydantic import BaseModel

        class QuoteInput(BaseModel):
            amount: float

        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        meta = make_action_meta("record_quote", ActionKind.ACTION, input_model=QuoteInput)
        ext.on_action(cls, "record_quote", meta)

        assert cls.__swagger__["actions"]["record_quote"]["input_model"] is QuoteInput

    def test_no_swagger_without_on_resource(self):
        ext = Swagger()
        cls = make_resource("Job")
        # Skip on_resource

        meta = make_action_meta("create", ActionKind.CREATE)
        ext.on_action(cls, "create", meta)  # should not raise

        assert not hasattr(cls, "__swagger__")


# ---------------------------------------------------------------------------
# Tests: on_signal
# ---------------------------------------------------------------------------

class TestOnSignal:
    def test_create_signal(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        sdef = make_signal_def("open", kind=ActionKind.CREATE)
        ext.on_signal(cls, "open", sdef)

        sig = cls.__swagger__["signals"]["open"]
        assert "Create" in sig["summary"]
        assert sig["is_create"] is True
        assert sig["method"] == "POST"

    def test_regular_signal(self):
        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        sdef = make_signal_def("approval")
        ext.on_signal(cls, "approval", sdef)

        sig = cls.__swagger__["signals"]["approval"]
        assert "Signal" in sig["summary"]
        assert sig["is_create"] is False
        assert "202" in sig["description"]

    def test_signal_with_input(self):
        from pydantic import BaseModel

        class ApprovalInput(BaseModel):
            action: str

        ext = Swagger()
        cls = make_resource("Job")
        ext.on_resource(cls)

        sdef = make_signal_def("approval", input_model=ApprovalInput)
        ext.on_signal(cls, "approval", sdef)

        assert cls.__swagger__["signals"]["approval"]["input_model"] is ApprovalInput


# ---------------------------------------------------------------------------
# Tests: helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_humanize(self):
        assert _humanize("approve_quote") == "Approve Quote"
        assert _humanize("open_from_call") == "Open From Call"
        assert _humanize("get") == "Get"

    def test_action_summary_create(self):
        meta = make_action_meta("create", ActionKind.CREATE)
        assert _action_summary("Job", "create", meta) == "Create Job"

    def test_action_summary_custom_create(self):
        meta = make_action_meta("register", ActionKind.CREATE)
        assert _action_summary("User", "register", meta) == "Create User via Register"

    def test_action_summary_list(self):
        meta = make_action_meta("list", ActionKind.READ)
        assert _action_summary("Job", "list", meta) == "List Jobs"

    def test_action_summary_custom_read(self):
        meta = make_action_meta("analytics", ActionKind.READ)
        assert _action_summary("Call", "analytics", meta) == "Analytics Call"


# ---------------------------------------------------------------------------
# Tests: extension_type
# ---------------------------------------------------------------------------

class TestExtensionType:
    def test_type_name(self):
        ext = Swagger()
        assert ext.extension_type == "Swagger"

    def test_different_configs_same_type(self):
        a = Swagger(tag="A")
        b = Swagger(tag="B")
        assert a.extension_type == b.extension_type
