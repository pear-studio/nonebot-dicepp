"""
Unified Error Model for AST Roll Engine

This module defines the error types used throughout the AST-based roll engine.
All errors are mapped to stable error codes for consistent handling.

Error Categories:
- SYNTAX_ERROR: Grammar/parsing errors
- RUNTIME_ERROR: Evaluation errors (e.g., invalid modifier application)
- LIMIT_EXCEEDED: Safety limit violations
- UNKNOWN_VAR: Variable reference errors (future)
"""

from enum import Enum
from typing import Optional


class RollErrorCode(Enum):
    """Stable error codes for roll expression errors."""
    
    # Syntax errors (1xx)
    SYNTAX_ERROR = 100
    UNEXPECTED_TOKEN = 101
    UNMATCHED_PAREN = 102
    INVALID_DICE_FORMAT = 103
    INVALID_MODIFIER = 104
    
    # Runtime errors (2xx)
    RUNTIME_ERROR = 200
    INVALID_MODIFIER_TARGET = 201
    DIVISION_BY_ZERO = 202  # Note: legacy returns 0, kept for documentation
    
    # Limit exceeded (3xx)
    LIMIT_EXCEEDED = 300
    EXPRESSION_TOO_LONG = 301
    PARSE_DEPTH_EXCEEDED = 302
    DICE_COUNT_EXCEEDED = 303
    DICE_SIDES_EXCEEDED = 304
    EXPLOSION_LIMIT_EXCEEDED = 305
    
    # Variable errors (4xx) - future
    UNKNOWN_VAR = 400


class RollEngineError(Exception):
    """Base exception for all roll engine errors."""
    
    def __init__(
        self,
        message: str,
        code: RollErrorCode,
        expression: Optional[str] = None,
        position: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.expression = expression
        self.position = position
    
    @property
    def info(self) -> str:
        """Return user-friendly error message (compatible with legacy RollDiceError.info)."""
        return self.message


class RollSyntaxError(RollEngineError):
    """Syntax/parsing error in roll expression."""
    
    def __init__(
        self,
        message: str,
        expression: Optional[str] = None,
        position: Optional[int] = None,
        code: RollErrorCode = RollErrorCode.SYNTAX_ERROR,
    ):
        super().__init__(message, code, expression, position)


class RollRuntimeError(RollEngineError):
    """Runtime evaluation error."""
    
    def __init__(
        self,
        message: str,
        expression: Optional[str] = None,
        code: RollErrorCode = RollErrorCode.RUNTIME_ERROR,
    ):
        super().__init__(message, code, expression)


class RollLimitError(RollEngineError):
    """Safety limit exceeded error."""
    
    def __init__(
        self,
        message: str,
        code: RollErrorCode = RollErrorCode.LIMIT_EXCEEDED,
        limit_name: Optional[str] = None,
        limit_value: Optional[int] = None,
        actual_value: Optional[int] = None,
    ):
        super().__init__(message, code)
        self.limit_name = limit_name
        self.limit_value = limit_value
        self.actual_value = actual_value
