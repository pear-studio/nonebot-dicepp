"""
Structured Trace Model for Roll Expressions

This module defines the trace event model used to capture evaluation
steps for rendering. The trace provides structured data that can be
consumed by different renderers (text, HTML, etc.).

Trace Events:
- DiceRollEvent: Individual dice roll
- ModifierAppliedEvent: Modifier application
- OperationEvent: Arithmetic operation
- ResultEvent: Final result
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Union, Any


class TraceEventType(Enum):
    """Types of trace events."""
    DICE_ROLL = "dice_roll"
    MODIFIER_APPLIED = "modifier_applied"
    OPERATION = "operation"
    RESULT = "result"
    ERROR = "error"


@dataclass
class TraceEvent:
    """Base class for all trace events."""
    event_type: TraceEventType
    timestamp_order: int = 0  # Ordering within evaluation


@dataclass
class DiceRollEvent(TraceEvent):
    """Event for a dice roll."""
    count: int = 0
    sides: int = 0
    values: List[int] = field(default_factory=list)
    
    def __post_init__(self):
        self.event_type = TraceEventType.DICE_ROLL


@dataclass
class ModifierAppliedEvent(TraceEvent):
    """Event for a modifier being applied."""
    modifier_type: str = ""
    original_values: List[int] = field(default_factory=list)
    result_values: List[int] = field(default_factory=list)
    kept_indices: List[int] = field(default_factory=list)
    details: str = ""  # Human-readable details
    # Extra per-modifier metadata (used by renderers)
    # For CS: {"successes": int, "failures": int, "compare": str, "threshold": int}
    # For XO: {"extra_values": List[int]}   (extra dice appended per trigger)
    extra: Any = field(default_factory=dict)
    
    def __post_init__(self):
        self.event_type = TraceEventType.MODIFIER_APPLIED


@dataclass
class OperationEvent(TraceEvent):
    """Event for an arithmetic operation."""
    operator: str = ""
    left_value: Union[int, float] = 0
    right_value: Union[int, float] = 0
    result_value: Union[int, float] = 0
    
    def __post_init__(self):
        self.event_type = TraceEventType.OPERATION


@dataclass
class ResultEvent(TraceEvent):
    """Event for final result."""
    value: Union[int, float] = 0
    expression: str = ""
    
    def __post_init__(self):
        self.event_type = TraceEventType.RESULT


@dataclass
class ErrorEvent(TraceEvent):
    """Event for an error during evaluation."""
    error_code: str = ""
    error_message: str = ""
    
    def __post_init__(self):
        self.event_type = TraceEventType.ERROR


@dataclass
class EvaluationTrace:
    """
    Complete trace of an expression evaluation.
    
    Contains all events in order of occurrence.
    """
    expression: str = ""
    events: List[TraceEvent] = field(default_factory=list)
    final_value: Optional[Union[int, float]] = None
    error: Optional[str] = None
    
    def add_event(self, event: TraceEvent) -> None:
        """Add an event to the trace."""
        event.timestamp_order = len(self.events)
        self.events.append(event)
    
    def get_dice_events(self) -> List[DiceRollEvent]:
        """Get all dice roll events."""
        return [e for e in self.events if isinstance(e, DiceRollEvent)]
    
    def get_modifier_events(self) -> List[ModifierAppliedEvent]:
        """Get all modifier applied events."""
        return [e for e in self.events if isinstance(e, ModifierAppliedEvent)]


class TraceRenderer:
    """
    Base class for trace renderers.
    
    Subclass this to implement different output formats.
    """
    
    def render(self, trace: EvaluationTrace) -> str:
        """Render a trace to string output."""
        raise NotImplementedError
    
    def render_dice_roll(self, event: DiceRollEvent) -> str:
        """Render a dice roll event."""
        raise NotImplementedError
    
    def render_modifier(self, event: ModifierAppliedEvent) -> str:
        """Render a modifier event."""
        raise NotImplementedError


class LegacyTextRenderer(TraceRenderer):
    """
    Renderer that produces legacy-compatible text output.

    Trace events arrive in AST post-order (children before parent), so we use
    a stack to reconstruct the full infix expression with operators:

        1D20+1D6  ->  DiceRoll([15]), DiceRoll([4]), Operation(+)
        stack: ["[15]"] -> ["[15]", "[4]"] -> ["[15]+[4]"]

    Modifier events are inline suffixes to the most recent dice block, so
    they replace (pop-then-push) the top of the stack.
    """

    def render_dice_roll(self, event: DiceRollEvent) -> str:
        """Render dice roll in [val] format."""
        if len(event.values) == 1:
            return f"[{event.values[0]}]"
        else:
            vals = "+".join(str(v) for v in event.values)
            return f"[{vals}]"

    def render_modifier(self, event: ModifierAppliedEvent) -> str:
        """Render modifier application in legacy-compatible text format.

        modifier_type 存储的是 ModifierType 枚举的 .value（即 "K"、"KL" 等短字符串）。

        Legacy format reference (from modifier.py):
          K / KH  → replaces block with  MAX{v1, v2, ...}   (original values shown in braces)
          KL      → replaces block with  MIN{v1, v2, ...}
          R       → [old̶→new]   (per-die: strikethrough old, arrow to new)
          X       → [v1|v2|...]  (per-trigger: pipe-separated chain)
          XO      → [v1]‹v2›    (per-trigger: extra die in angle brackets)
          M       → [old→min]   (per-die: arrow to clamped value, only when changed)
          P       → [=val]      (per-die: equals-prefix forced value)
          CS      → " > N(X次成功Y次失败)"  (success-count summary appended to dice block)
          F       → (no visible change to info; sets float_state only)

        For K/KL the renderer REPLACES the current stack top with a new string
        built from original_values, so the return value is a *replacement* not
        a suffix.  The render() loop handles this via the normal suffix path
        (stack[-1] = stack[-1] + suffix), but we need to signal "replace" vs
        "append".  We solve this by returning the full replacement string
        prefixed with a special sentinel handled in render().

        Simpler approach: K/KL handlers return a special token and render() handles
        them inline.  But to keep render() simple we instead implement K/KL here
        so that the returned string, when *appended* to the raw dice block, still
        reads as the legacy format.

        Actually legacy K/KL *replaces* the info entirely with
        ``MAX{original_values_of_all_dice}``.  The current stack top already has
        the raw ``[v1+v2+...]`` text from DiceRollEvent, so we must return a
        complete replacement.  We use the sentinel prefix ``\x00REPLACE\x00``
        and handle it in render().
        """
        mt = event.modifier_type

        # ── K / KH : Keep Highest ──────────────────────────────────────────
        if mt == "K":
            # Legacy: MAX{styled_dice_info} where styled_dice_info = all original values
            orig_str = ", ".join(f"[{v}]" for v in event.original_values)
            return f"\x00REPLACE\x00MAX{{{orig_str}}}"

        # ── KL : Keep Lowest ───────────────────────────────────────────────
        elif mt == "KL":
            orig_str = ", ".join(f"[{v}]" for v in event.original_values)
            return f"\x00REPLACE\x00MIN{{{orig_str}}}"

        # ── R : Reroll ─────────────────────────────────────────────────────
        # Legacy: per die that was rerolled, replace [old] with [old̶→new]
        # The current stack top already has [v1][v2]... from DiceRollEvent.
        # We return a full replacement where changed dice are annotated.
        elif mt == "R":
            if not event.original_values or not event.result_values:
                return ""
            parts = []
            for orig, new in zip(event.original_values, event.result_values):
                if orig != new:
                    parts.append(f"[{orig}\u0336\u2192{new}]")
                else:
                    parts.append(f"[{orig}]")
            return f"\x00REPLACE\x00{''.join(parts)}"

        # ── X : Explode (unlimited) ────────────────────────────────────────
        # Legacy: triggered die becomes [v1|v2|v3...] where v1 is original trigger value
        # result_values contains all kept values after explosion.
        # extra["exploded_chains"] is a list-of-lists provided by evaluator.
        elif mt == "X":
            chains: List[List[int]] = event.extra.get("exploded_chains", [])
            if chains:
                parts = []
                for chain in chains:
                    parts.append("[" + "|".join(str(v) for v in chain) + "]")
                return f"\x00REPLACE\x00{''.join(parts)}"
            # Fallback: just show result values
            if event.result_values:
                return f"\x00REPLACE\x00" + "".join(f"[{v}]" for v in event.result_values)
            return ""

        # ── XO : Explode Once ──────────────────────────────────────────────
        # Legacy: triggered die stays as [v], extra die shown as ‹v›
        elif mt == "XO":
            chains: List[List[int]] = event.extra.get("exploded_chains", [])
            if chains:
                parts = []
                for chain in chains:
                    if len(chain) >= 2:
                        parts.append(f"[{chain[0]}]\u2039{chain[1]}\u203a")
                    else:
                        parts.append(f"[{chain[0]}]")
                return f"\x00REPLACE\x00{''.join(parts)}"
            if event.result_values:
                return f"\x00REPLACE\x00" + "".join(f"[{v}]" for v in event.result_values)
            return ""

        # ── M : Minimum ────────────────────────────────────────────────────
        # Legacy: [old→min] for each die that was raised to minimum
        elif mt == "M":
            if not event.original_values or not event.result_values:
                return ""
            parts = []
            for orig, new in zip(event.original_values, event.result_values):
                if orig != new:
                    parts.append(f"[{orig}\u2192{new}]")
                else:
                    parts.append(f"[{orig}]")
            return f"\x00REPLACE\x00{''.join(parts)}"

        # ── P : Portent (forced value) ────────────────────────────────────
        # Legacy: [=val] for first die (all dice replaced by portent value)
        elif mt == "P":
            if not event.result_values:
                return ""
            parts = [f"[={v}]" for v in event.result_values]
            return f"\x00REPLACE\x00{''.join(parts)}"

        # ── CS : Count Success ────────────────────────────────────────────
        # Legacy: append " compare threshold(X次成功Y次失败)" after styled dice
        elif mt == "CS":
            extra = event.extra
            successes: int = extra.get("successes", 0)
            failures: int = extra.get("failures", 0)
            compare: str = extra.get("compare", "")
            threshold: int = extra.get("threshold", 0)
            total = successes + failures
            if total == 1:
                result_str = "成功" if successes == 1 else "失败"
            else:
                parts_cs = []
                if successes:
                    parts_cs.append(f"{successes}次成功")
                if failures:
                    parts_cs.append(f"{failures}次失败")
                result_str = "".join(parts_cs)
            return f" {compare} {threshold}({result_str})"

        # ── F : Float flag ────────────────────────────────────────────────
        # No visible change to info text; float_state is handled elsewhere.
        elif mt == "F":
            return ""

        # Fallback: use details if available
        return event.details if event.details else ""

    def render(self, trace: EvaluationTrace) -> str:
        """Render trace to legacy-compatible text using an expression stack."""
        stack: List[str] = []

        for event in trace.events:
            if isinstance(event, DiceRollEvent):
                stack.append(self.render_dice_roll(event))
            elif isinstance(event, ModifierAppliedEvent):
                rendered = self.render_modifier(event)
                if rendered.startswith("\x00REPLACE\x00"):
                    # Full replacement: discard current dice block, push new text
                    replacement = rendered[len("\x00REPLACE\x00"):]
                    if stack:
                        stack[-1] = replacement
                    else:
                        stack.append(replacement)
                elif rendered:
                    # Suffix append
                    if stack:
                        stack[-1] = stack[-1] + rendered
                    else:
                        stack.append(rendered)
                # empty string → no change
            elif isinstance(event, OperationEvent):
                if len(stack) >= 2:
                    right = stack.pop()
                    left = stack.pop()
                elif len(stack) == 1:
                    right = str(event.right_value)
                    left = stack.pop()
                else:
                    left = str(event.left_value)
                    right = str(event.right_value)
                stack.append(f"{left}{event.operator}{right}")

        return stack[0] if stack else ""
