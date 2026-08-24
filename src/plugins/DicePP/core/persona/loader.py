"""
PersonaLoader: discovers and loads Persona files from content/characters/*/skin.yaml.
"""
from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import ValidationError

from plugins.DicePP.utils.logger import logger
from plugins.DicePP.core.persona.models import PersonaModel
from plugins.DicePP.core.config.basic import Paths

_DEFAULT_PERSONA = "default"


class PersonaLoader:
    """
    Loads and caches PersonaModel objects from content/characters/*/skin.yaml.

    Usage:
        loader = PersonaLoader()                  # production: uses Paths.CONTENT_CHARACTERS_DIR
        loader = PersonaLoader(character_path)    # custom path (tests)
        persona = loader.get("cute")              # falls back to "default"
        loader.reload()                           # hot-reload all personas
    """

    def __init__(self, character_path: Optional[str] = None):
        if character_path is not None:
            self._dir = Path(character_path)
        else:
            self._dir = Paths.CONTENT_CHARACTERS_DIR
        self._cache: Dict[str, PersonaModel] = {}
        self._load_all()

    # ── public API ──────────────────────────────────────────────────────────

    def get(self, name: str) -> PersonaModel:
        """Return persona by name, falling back to 'default' if not found."""
        if name in self._cache:
            return self._cache[name]
        if name != _DEFAULT_PERSONA:
            logger.warning(f"[Persona] Persona '{name}' not found, falling back to 'default'")
        return self._cache.get(_DEFAULT_PERSONA, PersonaModel())

    def reload(self) -> None:
        """Reload all persona files from disk (for hot-reload support)."""
        self._cache = {}
        self._load_all()
        logger.info(f"[Persona] Reloaded {len(self._cache)} persona(s)")

    def set_character_path(self, path: str) -> None:
        """Update the character directory and reload all personas.

        Available to explicit Persona lifecycle code; general configuration
        changes are applied by restarting the Bot.
        """
        self._dir = Path(path)
        self.reload()

    def available_names(self) -> list[str]:
        return list(self._cache.keys())

    # ── internals ───────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        if not self._dir.exists():
            logger.warning(f"[Persona] Characters directory not found: {self._dir}")
            self._cache[_DEFAULT_PERSONA] = PersonaModel()
            return

        for skin_path in sorted(self._dir.glob("*/skin.yaml")):
            name = skin_path.parent.name
            persona = self._load_one(skin_path, name)
            if persona is not None:
                self._cache[name] = persona

        if _DEFAULT_PERSONA not in self._cache:
            logger.warning("[Persona] No 'default' persona found; using empty defaults")
            self._cache[_DEFAULT_PERSONA] = PersonaModel()

    def _load_one(self, path: Path, name: str) -> Optional[PersonaModel]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning(f"[Persona] Invalid YAML structure in {path}: expected dict")
                return None
            yaml_name = data.get("name")
            if yaml_name is not None and yaml_name != name:
                logger.warning(
                    f"[Persona] skin.yaml name {yaml_name!r} differs from "
                    f"directory name {name!r}; using directory name"
                )
            data["name"] = name
            return PersonaModel.model_validate(data)
        except yaml.YAMLError as exc:
            logger.error(f"[Persona] YAML parse error in {path}: {exc}")
        except ValidationError as exc:
            logger.error(f"[Persona] Validation error in {path}: {exc}")
        except OSError as exc:
            logger.error(f"[Persona] Cannot read {path}: {exc}")
        return None
