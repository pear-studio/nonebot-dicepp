from typing import Dict, Optional
import re
import random

from utils.logger import dice_log
from core.communication import preprocess_msg
from core.localization.localization_text import LocalizationText
from core.localization.common import COMMON_LOCAL_TEXT, COMMON_LOCAL_COMMENT


DEFAULT_CHAT_KEY = "^你好$"
DEFAULT_CHAT_TEXT = ["你好啊", "你好呀"]
DEFAULT_CHAT_COMMENT = "可以使用正则表达式匹配, 大小写不敏感; 后面接着想要的回复, 有多个回复则会随机选择一个"


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
        self.all_chat_texts: Dict[str, LocalizationText] = {}

        for key in COMMON_LOCAL_TEXT:
            self.register_loc_text(key, COMMON_LOCAL_TEXT[key], COMMON_LOCAL_COMMENT[key])

    # ── persona wiring ───────────────────────────────────────────────────────

    def set_persona(self, persona_name: str) -> None:
        """Switch to the named persona and re-apply overrides."""
        self._persona_name = persona_name
        self._apply_persona_overrides()
        self._apply_persona_chat()

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

    def _apply_persona_chat(self) -> None:
        """Replace chat patterns with persona's chat section if non-empty."""
        persona = self._current_persona()
        if persona is None or not persona.chat:
            return
        self.all_chat_texts = {}
        for pattern, responses in persona.chat.items():
            processed_key = preprocess_msg(pattern)
            loc = LocalizationText(processed_key, comment="persona chat")
            texts = responses if isinstance(responses, list) else [responses]
            for t in texts:
                loc.add(t)
            self.all_chat_texts[processed_key] = loc

    # ── registration ─────────────────────────────────────────────────────────

    def register_loc_text(self, key: str, default_text: str, comment: str = "") -> None:
        loc = LocalizationText(key, default_text, comment)
        self.all_local_texts[key] = loc

    def _ensure_default_chat(self) -> None:
        if not self.all_chat_texts:
            self.all_chat_texts[DEFAULT_CHAT_KEY] = LocalizationText(
                DEFAULT_CHAT_KEY, comment=DEFAULT_CHAT_COMMENT
            )
            for t in DEFAULT_CHAT_TEXT:
                self.all_chat_texts[DEFAULT_CHAT_KEY].add(t)

    # ── public query API (signatures unchanged) ───────────────────────────────

    def get_loc_text(self, key: str) -> LocalizationText:
        return self.all_local_texts[key]

    def format_loc_text(self, key: str, **kwargs) -> str:
        loc_text = self.get_loc_text(key)
        if kwargs:
            return loc_text.get().format(**kwargs)
        return loc_text.get()

    def process_chat(self, msg: str, **kwargs) -> str:
        self._ensure_default_chat()
        valid: list = []
        for key, loc_text in self.all_chat_texts.items():
            if re.match(key, msg):
                valid.append(loc_text)
        if not valid:
            return ""
        chosen: LocalizationText = random.choice(valid)
        if kwargs:
            return chosen.get().format(**kwargs)
        return chosen.get()

    def reset_to_default(self) -> None:
        """Reset all texts to their registered defaults (used in tests)."""
        for loc_text in self.all_local_texts.values():
            loc_text.loc_texts = [loc_text.default_text] if loc_text.default_text else []
        for loc_text in self.all_chat_texts.values():
            loc_text.loc_texts = [loc_text.default_text] if loc_text.default_text else []

    def load_localization(self) -> None:
        """No-op compatibility shim (replaced by persona-based overrides)."""
        self._apply_persona_overrides()

    def save_localization(self) -> None:
        """No-op compatibility shim (files are no longer used)."""

    def load_chat(self) -> None:
        """No-op compatibility shim (replaced by persona-based chat overrides)."""
        self._apply_persona_chat()

    def save_chat(self) -> None:
        """No-op compatibility shim (files are no longer used)."""
