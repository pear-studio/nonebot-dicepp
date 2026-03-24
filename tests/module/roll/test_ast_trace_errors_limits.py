"""
Tests for Trace, Errors, and Safety Limits

This module tests:
1. Trace event model and rendering
2. Error code mapping
3. Safety limit enforcement
"""

import pytest
from module.roll.ast_engine.trace import (
    EvaluationTrace,
    DiceRollEvent,
    ModifierAppliedEvent,
    OperationEvent,
    ResultEvent,
    TraceEventType,
    LegacyTextRenderer,
)
from module.roll.ast_engine.errors import (
    RollSyntaxError,
    RollRuntimeError,
    RollLimitError,
    RollErrorCode,
)
from module.roll.ast_engine.limits import (
    SafetyLimits,
    check_expression_length,
    check_dice_count,
    check_dice_sides,
    check_explosion_limit,
    LimitChecker,
)


@pytest.mark.unit
class TestTraceEvents:
    """Test trace event creation and types."""
    
    def test_dice_roll_event(self):
        event = DiceRollEvent(
            event_type=TraceEventType.DICE_ROLL,
            count=2,
            sides=20,
            values=[15, 8]
        )
        assert event.count == 2
        assert event.sides == 20
        assert event.values == [15, 8]
    
    def test_modifier_applied_event(self):
        event = ModifierAppliedEvent(
            event_type=TraceEventType.MODIFIER_APPLIED,
            modifier_type="K",  # ModifierType.KEEP_HIGHEST.value == "K"
            original_values=[3, 15, 8],
            result_values=[15, 8],
            kept_indices=[1, 2]
        )
        assert event.modifier_type == "K"
        assert event.result_values == [15, 8]
    
    def test_operation_event(self):
        event = OperationEvent(
            event_type=TraceEventType.OPERATION,
            operator="+",
            left_value=10,
            right_value=5,
            result_value=15
        )
        assert event.operator == "+"
        assert event.result_value == 15
    
    def test_result_event(self):
        event = ResultEvent(
            event_type=TraceEventType.RESULT,
            value=42,
            expression="2D20+5"
        )
        assert event.value == 42


@pytest.mark.unit
class TestEvaluationTrace:
    """Test evaluation trace container."""
    
    def test_add_event(self):
        trace = EvaluationTrace(expression="1D20")
        event = DiceRollEvent(
            event_type=TraceEventType.DICE_ROLL,
            count=1,
            sides=20,
            values=[15]
        )
        trace.add_event(event)
        assert len(trace.events) == 1
        assert event.timestamp_order == 0
    
    def test_event_ordering(self):
        trace = EvaluationTrace(expression="2D20K1")
        trace.add_event(DiceRollEvent(
            event_type=TraceEventType.DICE_ROLL,
            count=2, sides=20, values=[15, 8]
        ))
        trace.add_event(ModifierAppliedEvent(
            event_type=TraceEventType.MODIFIER_APPLIED,
            modifier_type="K"  # ModifierType.KEEP_HIGHEST.value == "K"
        ))
        
        assert trace.events[0].timestamp_order == 0
        assert trace.events[1].timestamp_order == 1
    
    def test_get_dice_events(self):
        trace = EvaluationTrace()
        trace.add_event(DiceRollEvent(
            event_type=TraceEventType.DICE_ROLL,
            count=1, sides=20, values=[15]
        ))
        trace.add_event(ModifierAppliedEvent(
            event_type=TraceEventType.MODIFIER_APPLIED,
            modifier_type="K"
        ))
        
        dice_events = trace.get_dice_events()
        assert len(dice_events) == 1


@pytest.mark.unit
class TestLegacyTextRenderer:
    """Test legacy-compatible text rendering."""
    
    def test_render_single_dice(self):
        renderer = LegacyTextRenderer()
        event = DiceRollEvent(
            event_type=TraceEventType.DICE_ROLL,
            count=1, sides=20, values=[15]
        )
        text = renderer.render_dice_roll(event)
        assert text == "[15]"
    
    def test_render_multiple_dice(self):
        renderer = LegacyTextRenderer()
        event = DiceRollEvent(
            event_type=TraceEventType.DICE_ROLL,
            count=3, sides=6, values=[3, 4, 5]
        )
        text = renderer.render_dice_roll(event)
        assert text == "[3+4+5]"
    
    def test_render_keep_highest(self):
        renderer = LegacyTextRenderer()
        event = ModifierAppliedEvent(
            event_type=TraceEventType.MODIFIER_APPLIED,
            modifier_type="K",  # ModifierType.KEEP_HIGHEST.value == "K"
            original_values=[15, 12, 3],  # K renders from original_values (legacy style)
            result_values=[15],
        )
        text = renderer.render_modifier(event)
        assert "MAX" in text
        assert "15" in text


@pytest.mark.unit
class TestErrorCodes:
    """Test error code mapping."""
    
    def test_syntax_error_code(self):
        error = RollSyntaxError("Test error")
        assert error.code == RollErrorCode.SYNTAX_ERROR
    
    def test_syntax_error_with_position(self):
        error = RollSyntaxError(
            "Unexpected token",
            expression="1+@2",
            position=3,
            code=RollErrorCode.UNEXPECTED_TOKEN
        )
        assert error.position == 3
        assert error.expression == "1+@2"
    
    def test_runtime_error_code(self):
        error = RollRuntimeError("Division error")
        assert error.code == RollErrorCode.RUNTIME_ERROR
    
    def test_limit_error_details(self):
        error = RollLimitError(
            "Too many dice",
            code=RollErrorCode.DICE_COUNT_EXCEEDED,
            limit_name="dice_count",
            limit_value=1000,
            actual_value=1500
        )
        assert error.limit_name == "dice_count"
        assert error.limit_value == 1000
        assert error.actual_value == 1500
    
    def test_error_info_property(self):
        """Error.info should return message for legacy compatibility."""
        error = RollSyntaxError("Test message")
        assert error.info == "Test message"


@pytest.mark.unit
class TestSafetyLimits:
    """Test safety limit enforcement."""
    
    def test_default_limits(self):
        limits = SafetyLimits()
        assert limits.max_expression_length == 1000
        assert limits.max_dice_count == 100   # 对齐 DICE_NUM_MAX
        assert limits.max_dice_sides == 1000  # 对齐 DICE_TYPE_MAX
    
    def test_custom_limits(self):
        limits = SafetyLimits(
            max_expression_length=500,
            max_dice_count=100
        )
        assert limits.max_expression_length == 500
        assert limits.max_dice_count == 100
    
    def test_expression_length_ok(self):
        # Should not raise
        check_expression_length("1D20+5")
    
    def test_expression_length_exceeded(self):
        limits = SafetyLimits(max_expression_length=10)
        with pytest.raises(RollLimitError) as exc_info:
            check_expression_length("1D20+5+1D20+5+1D20", limits)
        assert exc_info.value.code == RollErrorCode.EXPRESSION_TOO_LONG
    
    def test_dice_count_ok(self):
        check_dice_count(100)
    
    def test_dice_count_exceeded(self):
        limits = SafetyLimits(max_dice_count=100)
        with pytest.raises(RollLimitError) as exc_info:
            check_dice_count(150, limits)
        assert exc_info.value.code == RollErrorCode.DICE_COUNT_EXCEEDED
    
    def test_dice_sides_ok(self):
        check_dice_sides(100)
    
    def test_dice_sides_exceeded(self):
        limits = SafetyLimits(max_dice_sides=100)
        with pytest.raises(RollLimitError) as exc_info:
            check_dice_sides(1000, limits)
        assert exc_info.value.code == RollErrorCode.DICE_SIDES_EXCEEDED
    
    def test_explosion_limit_ok(self):
        check_explosion_limit(50)
    
    def test_explosion_limit_exceeded(self):
        limits = SafetyLimits(max_explosion_iterations=10)
        with pytest.raises(RollLimitError) as exc_info:
            check_explosion_limit(15, limits)
        assert exc_info.value.code == RollErrorCode.EXPLOSION_LIMIT_EXCEEDED


@pytest.mark.unit
class TestLimitChecker:
    """Test stateful limit checker."""
    
    def test_increment_rolls(self):
        checker = LimitChecker()
        checker.check_and_increment_rolls(10)
        assert checker.total_rolls == 10
    
    def test_rolls_accumulate(self):
        checker = LimitChecker()
        checker.check_and_increment_rolls(10)
        checker.check_and_increment_rolls(20)
        assert checker.total_rolls == 30
    
    def test_rolls_exceeded(self):
        limits = SafetyLimits(max_total_rolls=100)
        checker = LimitChecker(limits)
        checker.check_and_increment_rolls(50)
        checker.check_and_increment_rolls(40)
        with pytest.raises(RollLimitError):
            checker.check_and_increment_rolls(20)
    
    def test_explosion_increment(self):
        checker = LimitChecker()
        checker.check_and_increment_explosion()
        assert checker.explosion_count == 1
    
    def test_explosion_exceeded(self):
        limits = SafetyLimits(max_explosion_iterations=5)
        checker = LimitChecker(limits)
        for _ in range(5):
            checker.check_and_increment_explosion()
        with pytest.raises(RollLimitError):
            checker.check_and_increment_explosion()
    
    def test_reset(self):
        checker = LimitChecker()
        checker.check_and_increment_rolls(50)
        checker.check_and_increment_explosion()
        checker.reset()
        assert checker.total_rolls == 0
        assert checker.explosion_count == 0


@pytest.mark.unit
class TestModifierRendering:
    """Test that every modifier type produces non-empty render output."""

    def _make_event(self, modifier_type: str, original=None, result=None, extra=None):
        return ModifierAppliedEvent(
            event_type=TraceEventType.MODIFIER_APPLIED,
            modifier_type=modifier_type,
            original_values=original or [],
            result_values=result or [],
            kept_indices=list(range(len(result or []))),
            extra=extra or {},
        )

    def test_render_keep_highest_nonempty(self):
        renderer = LegacyTextRenderer()
        event = self._make_event("K", original=[15, 8, 3], result=[15])
        rendered = renderer.render_modifier(event)
        assert rendered  # non-empty
        assert "MAX" in rendered
        assert "15" in rendered

    def test_render_keep_lowest_nonempty(self):
        renderer = LegacyTextRenderer()
        event = self._make_event("KL", original=[15, 8, 3], result=[3])
        rendered = renderer.render_modifier(event)
        assert rendered
        assert "MIN" in rendered
        assert "3" in rendered

    def test_render_reroll_changed_nonempty(self):
        renderer = LegacyTextRenderer()
        # Die with value 2 was rerolled to 10
        event = self._make_event("R", original=[2], result=[10])
        rendered = renderer.render_modifier(event)
        assert rendered
        assert "10" in rendered

    def test_render_explode_nonempty(self):
        renderer = LegacyTextRenderer()
        # Die 6 exploded → chain [6, 4]
        event = self._make_event(
            "X",
            original=[6],
            result=[6, 4],
            extra={"exploded_chains": [[6, 4]]},
        )
        rendered = renderer.render_modifier(event)
        assert rendered
        assert "6" in rendered
        assert "4" in rendered

    def test_render_explode_once_nonempty(self):
        renderer = LegacyTextRenderer()
        # Die 6 exploded once → chain [6, 3]
        event = self._make_event(
            "XO",
            original=[6],
            result=[6, 3],
            extra={"exploded_chains": [[6, 3]]},
        )
        rendered = renderer.render_modifier(event)
        assert rendered
        assert "6" in rendered
        assert "3" in rendered

    def test_render_minimum_changed_nonempty(self):
        renderer = LegacyTextRenderer()
        # Die 1 raised to minimum 3
        event = self._make_event("M", original=[1], result=[3])
        rendered = renderer.render_modifier(event)
        assert rendered
        assert "3" in rendered

    def test_render_portent_nonempty(self):
        renderer = LegacyTextRenderer()
        event = self._make_event("P", original=[7], result=[10])
        rendered = renderer.render_modifier(event)
        assert rendered
        assert "=10" in rendered

    def test_render_count_success_nonempty(self):
        renderer = LegacyTextRenderer()
        event = self._make_event(
            "CS",
            original=[8, 12, 5],
            result=[8, 12, 5],
            extra={
                "successes": 2,
                "failures": 1,
                "compare": ">=",
                "threshold": 7,
            },
        )
        rendered = renderer.render_modifier(event)
        assert rendered  # non-empty
        assert "成功" in rendered
        assert "7" in rendered

    def test_render_fortune_empty(self):
        """Fortune modifier does not change visible info."""
        renderer = LegacyTextRenderer()
        event = self._make_event("F", original=[5], result=[5])
        rendered = renderer.render_modifier(event)
        assert rendered == ""

    def test_render_modifier_replace_sentinel_handled(self):
        """render() must handle REPLACE sentinel so info text is correct."""
        renderer = LegacyTextRenderer()
        from module.roll.ast_engine.trace import EvaluationTrace, DiceRollEvent

        trace = EvaluationTrace(expression="2D20K1")
        trace.add_event(DiceRollEvent(
            event_type=None, count=2, sides=20, values=[15, 8]
        ))
        trace.add_event(ModifierAppliedEvent(
            event_type=None,
            modifier_type="K",
            original_values=[15, 8],
            result_values=[15],
            kept_indices=[0],
            extra={},
        ))
        result = renderer.render(trace)
        # After K modifier the block should be replaced, not "[15+8]MAX{...}"
        assert result.startswith("MAX")
        assert "[15]" in result
        assert "[8]" in result


@pytest.mark.unit
class TestEvaluationDepthLimit:
    """Test that Evaluator enforces max_parse_depth via _enter_node / _exit_node."""

    def test_normal_depth_no_error(self):
        """A simple nested expression well within depth limit should evaluate fine."""
        from module.roll.ast_engine.parser import parse_expression
        from module.roll.ast_engine.evaluator import evaluate
        # (((1+2)+3)+4) has depth ~4, well below default 50
        ast = parse_expression("(((1+2)+3)+4)")
        result = evaluate(ast)
        assert result.value == 10

    def test_depth_limit_exceeded_raises_limit_error(self):
        """Evaluating an AST that exceeds max_parse_depth should raise RollLimitError."""
        from module.roll.ast_engine.parser import parse_expression
        from module.roll.ast_engine.evaluator import Evaluator
        from module.roll.ast_engine.errors import RollLimitError
        from module.roll.ast_engine.limits import SafetyLimits

        # max_parse_depth=2 means only depth 1 (NumberNode) is allowed
        limits = SafetyLimits(max_parse_depth=2)
        # "1+2" has BinaryOpNode(depth=1) → NumberNode(depth=2) → ok at depth=2,
        # but another level "(1+2)+3" → BinaryOp(1)→BinaryOp(2)→Number(3) exceeds 2
        ast = parse_expression("(1+2)+3")

        evaluator = Evaluator(limits=limits)
        with pytest.raises(RollLimitError) as exc_info:
            ast.accept(evaluator)
        assert exc_info.value.code.name == "PARSE_DEPTH_EXCEEDED"

    def test_depth_counter_resets_between_branches(self):
        """After evaluating left branch the counter must decrement so right branch works."""
        from module.roll.ast_engine.parser import parse_expression
        from module.roll.ast_engine.evaluator import Evaluator
        from module.roll.ast_engine.limits import SafetyLimits

        # depth=3 allows: BinaryOp(1) + Number(2) on each side
        limits = SafetyLimits(max_parse_depth=3)
        ast = parse_expression("1+2+3")
        evaluator = Evaluator(limits=limits)
        result = ast.accept(evaluator)
        assert result.value == 6

    def test_depth_error_code_is_parse_depth_exceeded(self):
        """Verify the error code enum value is PARSE_DEPTH_EXCEEDED."""
        from module.roll.ast_engine.errors import RollErrorCode
        assert RollErrorCode.PARSE_DEPTH_EXCEEDED.value == 302
