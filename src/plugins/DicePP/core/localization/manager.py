from typing import Dict

from plugins.DicePP.core.localization.localization_text import LocalizationText
from plugins.DicePP.core.localization.common import COMMON_LOCAL_TEXT, COMMON_LOCAL_COMMENT


class LocalizationManager:
    def __init__(self, persona_loader=None):
        """
        Args:
            persona_loader: optional PersonaLoader instance.  When provided,
                persona overrides are applied on top of registered defaults.
        """
        self._persona_loader = persona_loader
        self._persona_name: str = "default"
        self.all_local_texts: Dict[str, LocalizationText] = {}

        for key in COMMON_LOCAL_TEXT:
            self.register_loc_text(key, COMMON_LOCAL_TEXT[key], COMMON_LOCAL_COMMENT[key])

    # ── persona wiring ───────────────────────────────────────────────────────

    def set_persona(self, persona_name: str) -> None:
        """Switch to the named persona and re-apply overrides."""
        self._persona_name = persona_name
        self._apply_persona_overrides()

    def _current_persona(self):
        """Return the active PersonaModel, or None if no loader."""
        if self._persona_loader is None:
            return None
        return self._persona_loader.get(self._persona_name)

    def _apply_persona_overrides(self) -> None:
        """Apply persona localization overrides on top of registered defaults."""
        persona = self._current_persona()
        if persona is None:
            return
        for key, loc_text in self.all_local_texts.items():
            persona_texts = persona.get_loc_texts(key)
            if persona_texts:
                loc_text.loc_texts = persona_texts
            else:
                loc_text.loc_texts = [loc_text.default_text] if loc_text.default_text else []

    # ── registration ─────────────────────────────────────────────────────────

    def register_loc_text(self, key: str, default_text: str, comment: str = "") -> None:
        loc = LocalizationText(key, default_text, comment)
        self.all_local_texts[key] = loc

    # ── public query API ──────────────────────────────────────────────────────

    def get_loc_text(self, key: str) -> LocalizationText:
        return self.all_local_texts[key]

    def format_loc_text(self, key: str, **kwargs) -> str:
        loc_text = self.get_loc_text(key)
        if kwargs:
            return loc_text.get().format(**kwargs)
        return loc_text.get()

    def reset_to_default(self) -> None:
        """Reset all texts to their registered defaults (used in tests)."""
        for loc_text in self.all_local_texts.values():
            loc_text.loc_texts = [loc_text.default_text] if loc_text.default_text else []

    def load_localization(self) -> None:
        """No-op compatibility shim (replaced by persona-based overrides)."""
        self._apply_persona_overrides()

    def save_localization(self) -> None:
        """No-op compatibility shim (files are no longer used)."""
