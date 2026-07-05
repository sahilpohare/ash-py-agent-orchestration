"""Unit tests for action/signal input introspection."""

import pytest
from decimal import Decimal

from pydantic import BaseModel, Field

from ironbridge.shared.framework.actions import ActionKind, ActionMeta, action, _inspect_input, _inspect_output
from ironbridge.shared.framework.signal import Signal
from ironbridge.shared.framework.workflow import Workflow


# ---------------------------------------------------------------------------
# _inspect_input
# ---------------------------------------------------------------------------

class TestInspectInput:
    def test_no_params(self):
        def fn(self):
            pass

        model, style = _inspect_input(fn, "test")
        assert model is None
        assert style == "none"

    def test_self_and_ctx_skipped(self):
        def fn(self, ctx):
            pass

        model, style = _inspect_input(fn, "test")
        assert model is None
        assert style == "none"

    def test_plain_str_params(self):
        def fn(self, name: str, age: int):
            pass

        model, style = _inspect_input(fn, "test")
        assert style == "fields"
        assert model is not None

        # Validate through the generated model
        validated = model(name="Alice", age=30)
        assert validated.name == "Alice"
        assert validated.age == 30

    def test_plain_params_with_defaults(self):
        def fn(self, name: str, count: int = 5):
            pass

        model, style = _inspect_input(fn, "test")
        assert style == "fields"

        # Default works
        validated = model(name="test")
        assert validated.count == 5

        # Override works
        validated = model(name="test", count=10)
        assert validated.count == 10

    def test_pydantic_model_param(self):
        class MyInput(BaseModel):
            amount: Decimal
            vat_inclusive: bool = False

        def fn(self, input: MyInput):
            pass

        model, style = _inspect_input(fn, "test")
        assert style == "model"
        assert model is MyInput

    def test_pydantic_model_validates(self):
        class QuoteInput(BaseModel):
            amount: Decimal = Field(gt=0)
            vat_inclusive: bool = False

        def fn(self, input: QuoteInput):
            pass

        model, _ = _inspect_input(fn, "test")
        validated = model(amount=Decimal("200.50"))
        assert validated.amount == Decimal("200.50")
        assert validated.vat_inclusive is False

        with pytest.raises(Exception):
            model(amount=Decimal("-1"))

    def test_single_non_model_param(self):
        def fn(self, name: str):
            pass

        model, style = _inspect_input(fn, "test")
        assert style == "fields"
        validated = model(name="test")
        assert validated.name == "test"

    def test_mixed_types(self):
        def fn(self, description: str, amount: float, urgent: bool = False):
            pass

        model, style = _inspect_input(fn, "test")
        assert style == "fields"

        validated = model(description="fix boiler", amount=200.0)
        assert validated.description == "fix boiler"
        assert validated.amount == 200.0
        assert validated.urgent is False

    def test_no_annotation_defaults_to_any(self):
        def fn(self, data):
            pass

        model, style = _inspect_input(fn, "test")
        assert style == "fields"
        validated = model(data={"anything": True})
        assert validated.data == {"anything": True}

    def test_ctx_param_skipped(self):
        async def fn(self, ctx, description: str, urgency: str):
            pass

        model, style = _inspect_input(fn, "test")
        assert style == "fields"

        # ctx should not be in the model
        validated = model(description="test", urgency="routine")
        assert validated.description == "test"
        assert not hasattr(validated, "ctx")


# ---------------------------------------------------------------------------
# _inspect_output
# ---------------------------------------------------------------------------

class TestInspectOutput:
    def test_no_annotation(self):
        def fn(self):
            pass

        assert _inspect_output(fn) is None

    def test_string_annotation(self):
        def fn(self) -> "MaintenanceJob":
            pass

        assert _inspect_output(fn) is None

    def test_pydantic_model(self):
        class Summary(BaseModel):
            id: str
            state: str

        def fn(self) -> Summary:
            pass

        assert _inspect_output(fn) is Summary

    def test_plain_type(self):
        def fn(self) -> dict:
            pass

        assert _inspect_output(fn) is None

    def test_float_return(self):
        def fn(self) -> float:
            pass

        assert _inspect_output(fn) is None


# ---------------------------------------------------------------------------
# @action decorator introspection
# ---------------------------------------------------------------------------

class TestActionDecorator:
    def test_no_input(self):
        @action(kind=ActionKind.ACTION)
        def approve(self):
            pass

        assert approve.__action__.input_model is None
        assert approve.__action__.input_style == "none"

    def test_plain_params(self):
        @action(kind=ActionKind.CREATE)
        def open(self, description: str, urgency: str):
            pass

        meta = open.__action__
        assert meta.input_style == "fields"
        assert meta.input_model is not None

        validated = meta.input_model(description="broken boiler", urgency="emergency")
        assert validated.description == "broken boiler"

    def test_pydantic_input(self):
        class CreateInput(BaseModel):
            name: str
            email: str

        @action(kind=ActionKind.CREATE)
        def register(self, input: CreateInput):
            pass

        meta = register.__action__
        assert meta.input_style == "model"
        assert meta.input_model is CreateInput

    def test_output_model(self):
        class JobSummary(BaseModel):
            id: str
            state: str

        @action(kind=ActionKind.READ)
        def summary(self) -> JobSummary:
            pass

        assert summary.__action__.output_model is JobSummary

    def test_no_output_for_forward_ref(self):
        @action(kind=ActionKind.CREATE)
        def create(self) -> "MaintenanceJob":
            pass

        assert create.__action__.output_model is None

    def test_preserves_function_behavior(self):
        @action(kind=ActionKind.ACTION)
        def add(self, a: int, b: int) -> int:
            return a + b

        assert add(None, 2, 3) == 5  # self=None for test


# ---------------------------------------------------------------------------
# Signal handler introspection
# ---------------------------------------------------------------------------

class TestSignalIntrospection:
    def test_signal_introspects_handler(self):
        class TestWf(Workflow):
            my_signal = Signal()

            async def on_my_signal(self, ctx, name: str, count: int = 1):
                pass

        sdef = TestWf.__signals__["my_signal"]
        assert sdef.input_model is not None
        assert sdef.input_style == "fields"

        validated = sdef.input_model(name="test")
        assert validated.name == "test"
        assert validated.count == 1

    def test_signal_introspects_pydantic_handler(self):
        class ApprovalInput(BaseModel):
            action: str
            reason: str | None = None

        class TestWf2(Workflow):
            approval = Signal()

            async def on_approval(self, ctx, input: ApprovalInput):
                pass

        sdef = TestWf2.__signals__["approval"]
        assert sdef.input_model is ApprovalInput
        assert sdef.input_style == "model"

    def test_signal_explicit_input_overrides_handler(self):
        class ExplicitInput(BaseModel):
            data: str

        class TestWf3(Workflow):
            my_signal = Signal(input=ExplicitInput)

            async def on_my_signal(self, ctx, something_else: int):
                pass

        sdef = TestWf3.__signals__["my_signal"]
        assert sdef.input_model is ExplicitInput
        assert sdef.input_style == "model"

    def test_signal_no_handler_no_input(self):
        class TestWf4(Workflow):
            orphan = Signal()
            # No on_orphan handler

        sdef = TestWf4.__signals__["orphan"]
        assert sdef.input_model is None
        assert sdef.input_style == "none"

    def test_signal_handler_no_params(self):
        class TestWf5(Workflow):
            ping = Signal()

            async def on_ping(self, ctx):
                pass

        sdef = TestWf5.__signals__["ping"]
        assert sdef.input_model is None
        assert sdef.input_style == "none"

    def test_signal_with_create_kind(self):
        class TestWf6(Workflow):
            start = Signal(kind=ActionKind.CREATE)

            async def on_start(self, ctx, description: str, urgency: str):
                pass

        sdef = TestWf6.__signals__["start"]
        assert sdef.kind == ActionKind.CREATE
        assert sdef.input_model is not None

        validated = sdef.input_model(description="test", urgency="routine")
        assert validated.description == "test"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_missing_required_field(self):
        @action(kind=ActionKind.CREATE)
        def open(self, description: str, urgency: str):
            pass

        with pytest.raises(Exception):
            open.__action__.input_model(description="test")  # missing urgency

    def test_wrong_type(self):
        @action(kind=ActionKind.ACTION)
        def set_count(self, count: int):
            pass

        # Pydantic coerces string "5" to int 5, so this passes
        validated = set_count.__action__.input_model(count="5")
        assert validated.count == 5

        # But a non-numeric string fails
        with pytest.raises(Exception):
            set_count.__action__.input_model(count="not_a_number")

    def test_pydantic_field_constraints(self):
        class StrictInput(BaseModel):
            amount: Decimal = Field(gt=0, max_digits=12)
            name: str = Field(min_length=1, max_length=100)

        @action(kind=ActionKind.ACTION)
        def strict(self, input: StrictInput):
            pass

        # Valid
        validated = strict.__action__.input_model(amount=Decimal("100"), name="test")
        assert validated.amount == Decimal("100")

        # Invalid: amount <= 0
        with pytest.raises(Exception):
            strict.__action__.input_model(amount=Decimal("0"), name="test")

        # Invalid: empty name
        with pytest.raises(Exception):
            strict.__action__.input_model(amount=Decimal("100"), name="")
