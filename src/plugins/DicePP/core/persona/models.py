"""
Pydantic models for the Persona system.

A Persona bundles together:
- localization overrides (key → one or more response strings)
- chat patterns (regex → response list)
- llm_personality (system prompt override)

All fields are optional — a Persona only needs to specify what it wants
to override from the hard-coded defaults.
"""
from typing import Dict, List, Union
from pydantic import BaseModel, Field


class PersonaModel(BaseModel):
    """
    Represents a single persona definition loaded from
    config/personas/{name}.json
    """
    name: str = "default"

    # Key → list of response strings (random selection on access).
    # A single string is also accepted and normalised to a one-element list.
    localization: Dict[str, Union[str, List[str]]] = Field(default_factory=dict)

    # Regex pattern → list of response strings.
    # Completely replaces the built-in chat patterns when non-empty.
    chat: Dict[str, Union[str, List[str]]] = Field(default_factory=dict)

    # LLM system prompt override.
    llm_personality: str = ""

    def get_loc_texts(self, key: str) -> List[str]:
        """Return the list of response strings for a localization key, or []."""
        val = self.localization.get(key)
        if val is None:
            return []
        if isinstance(val, str):
            return [val]
        return val

    def get_chat_responses(self, pattern: str) -> List[str]:
        """Return the list of response strings for a chat pattern, or []."""
        val = self.chat.get(pattern)
        if val is None:
            return []
        if isinstance(val, str):
            return [val]
        return val
