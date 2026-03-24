"""
AST-based Roll Expression Engine

This package provides a new implementation of the roll expression parser
and evaluator using a formal grammar (Lark) and explicit AST representation.

Key components:
- parser: Lark-based parser with EBNF grammar
- ast_nodes: Strongly-typed AST node definitions
- evaluator: AST visitor for expression evaluation
- errors: Unified error model

Design goals:
- 100% compatibility with legacy engine behavior
- Explicit operator precedence and associativity
- Structured trace output for rendering
- Unified error handling
"""

from .parser import parse_expression
from .ast_nodes import ASTNode
from .evaluator import evaluate
from .errors import RollSyntaxError, RollRuntimeError, RollLimitError
from .preprocessor import preprocess
from .adapter import (
    exec_roll_exp_ast,
    exec_roll_exp_unified,
    sample_roll_exp_ast,
    build_sampling_plan,
    sample_from_plan,
    SamplingPlan,
    RollExpressionResult,
    EngineType,
    set_default_engine,
    get_default_engine,
    enable_ast_engine,
    disable_ast_engine,
    is_ast_engine_enabled,
)

__all__ = [
    # Preprocessor
    "preprocess",
    # Parser
    "parse_expression",
    "ASTNode", 
    # Evaluator
    "evaluate",
    # Errors
    "RollSyntaxError",
    "RollRuntimeError",
    "RollLimitError",
    # Adapter (main API)
    "exec_roll_exp_ast",
    "exec_roll_exp_unified",
    "sample_roll_exp_ast",
    "build_sampling_plan",
    "sample_from_plan",
    "SamplingPlan",
    "RollExpressionResult",
    "EngineType",
    "set_default_engine",
    "get_default_engine",
    "enable_ast_engine",
    "disable_ast_engine",
    "is_ast_engine_enabled",
]
