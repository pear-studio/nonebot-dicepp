"""Pure-data built-in Persona provider and model catalog.

This module intentionally has no project or Pydantic imports.  The Runtime and
the frozen Dashboard schema each validate a fresh copy into their own local
``ProviderConfig`` type, avoiding cross-module class identity and import leaks.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_BUILTIN_PROVIDER_DATA: dict[str, dict[str, Any]] = {
    "deepseek": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "models": [
            {
                "name": "deepseek-v4-flash",
                "category": "llm",
                "capabilities": ["text", "tool_calls"],
                "quality": 0.5,
                "cost": 0.5,
            },
            {
                "name": "deepseek-v4-pro",
                "category": "llm",
                "capabilities": ["text", "tool_calls"],
                "quality": 0.6,
                "cost": 0.6,
            },
            {
                "name": "deepseek-v4-pro-t",
                "api_model": "deepseek-v4-pro",
                "category": "llm",
                "capabilities": ["text", "tool_calls"],
                "quality": 0.7,
                "cost": 0.7,
                "thinking": True,
            },
        ],
    },
}


def builtin_provider_catalog_data() -> dict[str, dict[str, Any]]:
    """Return independent JSON-compatible catalog data."""
    return deepcopy(_BUILTIN_PROVIDER_DATA)
