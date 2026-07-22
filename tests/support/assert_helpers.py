"""
Assertion helper functions for test validation.

Provides utilities for extracting and asserting on message content
without requiring full-text matching.
"""

import re
from typing import List, Sequence


def extract_numbers(text: str) -> List[int]:
    """
    Extract all integers from text.

    Args:
        text: Input text to search

    Returns:
        List of integers found in text
    """
    return [int(m) for m in re.findall(r'-?\d+', text)]


def assert_contains_number(text: str, expected: int) -> bool:
    """
    Assert that text contains the expected number.

    Args:
        text: Text to search
        expected: Number to look for

    Returns:
        True if found

    Raises:
        AssertionError: if number not found
    """
    numbers = extract_numbers(text)
    if expected not in numbers:
        raise AssertionError(
            f"Expected number {expected} not found in text. "
            f"Found numbers: {numbers}"
        )
    return True


def assert_name_order(text: str, names: Sequence[str]) -> bool:
    """
    Assert that names appear in text in the specified order.

    Args:
        text: Text to search
        names: Expected order of names

    Returns:
        True if names appear in correct order

    Raises:
        AssertionError: if order doesn't match
    """
    positions = []
    for name in names:
        pos = text.find(name)
        if pos == -1:
            raise AssertionError(
                f"Name '{name}' not found in text. "
                f"Text: {text[:200]}..."
            )
        positions.append(pos)

    # Check ascending order
    for i in range(len(positions) - 1):
        if positions[i] > positions[i + 1]:
            raise AssertionError(
                f"Names not in expected order. "
                f"Expected: {list(names)}, "
                f"but '{names[i]}' (pos {positions[i]}) appears after "
                f"'{names[i+1]}' (pos {positions[i+1]})"
            )
    return True


def assert_contains_any(text: str, options: Sequence[str]) -> str:
    """
    Assert that text contains at least one of the options.

    Args:
        text: Text to search
        options: Possible strings to look for

    Returns:
        The first matching option found

    Raises:
        AssertionError: if none found
    """
    for opt in options:
        if opt in text:
            return opt
    raise AssertionError(
        f"None of the options {list(options)} found in text. "
        f"Text: {text[:200]}..."
    )
