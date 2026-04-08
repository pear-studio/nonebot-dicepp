"""
PersonaLoader: discovers and loads Persona files from config/personas/.
"""
import json
from pathlib import Path
from typing import Dict, Optional

from pydantic import ValidationError

from utils.logger import dice_log
from core.persona.models import PersonaModel
from core.config.basic import Paths

_PERSONAS_DIR = "personas"
_DEFAULT_PERSONA = "default"


class PersonaLoader:
    """
    Loads and caches PersonaModel objects from config/personas/*.json.

    Usage:
        loader = PersonaLoader()            # production: uses Paths.CONFIG_PERSONAS_DIR
        loader = PersonaLoader(data_path)   # tests: looks in data_path/personas/
        persona = loader.get("cute")        # falls back to "default"
        loader.reload()                     # hot-reload all personas
    """

    def __init__(self, data_path: Optional[str] = None):
        if data_path is not None:
            self._dir = Path(data_path) / _PERSONAS_DIR
        else:
            self._dir = Paths.CONFIG_PERSONAS_DIR
        self._cache: Dict[str, PersonaModel] = {}
        self._load_all()

    # ── public API ──────────────────────────────────────────────────────────

    def get(self, name: str) -> PersonaModel:
        """Return persona by name, falling back to 'default' if not found."""
        if name in self._cache:
            return self._cache[name]
        if name != _DEFAULT_PERSONA:
            dice_log(f"[Persona] Persona '{name}' not found, falling back to 'default'")
        return self._cache.get(_DEFAULT_PERSONA, PersonaModel())

    def reload(self) -> None:
        """Reload all persona files from disk (for hot-reload support)."""
        self._cache = {}
        self._load_all()
        dice_log(f"[Persona] Reloaded {len(self._cache)} persona(s)")

    def available_names(self) -> list:
        return list(self._cache.keys())

    # ── internals ───────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        if not self._dir.exists():
            dice_log(f"[Persona] Personas directory not found: {self._dir}")
            self._cache[_DEFAULT_PERSONA] = PersonaModel()
            return

        for path in sorted(self._dir.glob("*.json")):
            name = path.stem
            persona = self._load_one(path, name)
            if persona is not None:
                self._cache[name] = persona

        if _DEFAULT_PERSONA not in self._cache:
            dice_log("[Persona] No 'default' persona found; using empty defaults")
            self._cache[_DEFAULT_PERSONA] = PersonaModel()

    def _load_one(self, path: Path, name: str) -> Optional[PersonaModel]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("name", name)
            return PersonaModel.model_validate(data)
        except json.JSONDecodeError as exc:
            dice_log(f"[Persona] JSON parse error in {path}: {exc}")
        except ValidationError as exc:
            dice_log(f"[Persona] Validation error in {path}: {exc}")
        except OSError as exc:
            dice_log(f"[Persona] Cannot read {path}: {exc}")
        return None
