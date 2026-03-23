#!/usr/bin/env python
"""Capture baseline values for the compatibility corpus."""

import sys
import json
import random
import os

# Add paths - need to handle module imports carefully
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
dicepp_path = os.path.join(project_root, 'src', 'plugins', 'DicePP')
sys.path.insert(0, dicepp_path)

# Direct imports to avoid module/__init__.py which has too many dependencies
# We manually import the needed roll submodules

# First, setup minimal mocks if needed for any transitive deps
import importlib.util

def load_module_directly(name, filepath):
    """Load a module directly from its file path."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

# Load roll submodules in dependency order
roll_path = os.path.join(dicepp_path, 'module', 'roll')
utils_path = os.path.join(dicepp_path, 'utils')

# Load utils.string first (needed by expression)
load_module_directly('utils', os.path.join(utils_path, '__init__.py'))
load_module_directly('utils.string', os.path.join(utils_path, 'string.py'))

# Load roll submodules
load_module_directly('module.roll.karma_runtime', os.path.join(roll_path, 'karma_runtime.py'))
load_module_directly('module.roll.roll_config', os.path.join(roll_path, 'roll_config.py'))
load_module_directly('module.roll.result', os.path.join(roll_path, 'result.py'))
load_module_directly('module.roll.roll_utils', os.path.join(roll_path, 'roll_utils.py'))
load_module_directly('module.roll.formula', os.path.join(roll_path, 'formula.py'))
load_module_directly('module.roll.modifier', os.path.join(roll_path, 'modifier.py'))
load_module_directly('module.roll.connector', os.path.join(roll_path, 'connector.py'))
load_module_directly('module.roll.expression', os.path.join(roll_path, 'expression.py'))

from module.roll.expression import exec_roll_exp
from module.roll.roll_utils import RollDiceError
from module.roll.karma_runtime import set_runtime, reset_runtime


class SeededDiceRuntime:
    def __init__(self, seed=42):
        self._rng = random.Random(seed)
    
    def roll(self, dice_type):
        return self._rng.randint(1, dice_type)


# Corpus entries
EXPRESSIONS = {
    "arithmetic": [
        "1", "42", "1+1", "5-3", "3*4", "8/2", "10/3",
        "1-1-1", "1+1-1", "1-1+1", "5/2+3/2",
        "1+2*2", "1*2+2", "(1+2)*3", "((1+2))", "2*(3+4)",
        "+1", "-1",
    ],
    "dice": [
        "1D20", "D20", "D", "3D6", "1D4", "1D100",
        "1D20+5", "1D20-3", "2D6*2", "1D20/2", "1+1D20", "2*3D6",
        "1D20+1D6", "(1D20+5)*2", "D20+D20",
    ],
    "modifier": [
        "2D20K1", "2D20KH1", "2D20KL1", "4D6K3", "4D20K2KL1",
        "4D20R<10", "4D20R>15", "4D20R=1", "4D20R<=5", "4D20R>=18",
        "4D20X>18", "4D20XO>18",
        "D20CS>10", "10D20CS>10", "10D20CS>=15", "10D20CS<=5", "10D20CS==10",
        "1D20M5", "1D20P10",
        "5+10D20CS>10+5", "10D20KL5CS>10",
    ],
    "localization": [
        "D20优势", "D20劣势+1", "D20+2抗性", "5抗性", "2D4+D20易伤",
    ],
    "errors": [
        "1D(20)", "(1)D20", "(D20)+(1", "((D20)+1))))", "(10D20+5)CS>10",
        "1D1000001", "1001D20",
    ],
}


def main():
    runtime = SeededDiceRuntime(seed=42)
    token = set_runtime(runtime)
    
    results = {}
    try:
        for category, expressions in EXPRESSIONS.items():
            results[category] = {}
            # Reset seed for each category for reproducibility
            runtime._rng = random.Random(42)
            
            for expr in expressions:
                try:
                    result = exec_roll_exp(expr)
                    results[category][expr] = {
                        "value": result.get_val(),
                        "info": result.get_info(),
                        "exp": result.get_exp(),
                    }
                except RollDiceError as e:
                    results[category][expr] = {
                        "error": e.info,
                        "error_type": "RollDiceError",
                    }
    finally:
        reset_runtime(token)
    
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
