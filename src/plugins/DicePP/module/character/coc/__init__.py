"""COC compatibility layer.

Historically COC character data objects shared implementations with dnd5e.
This package re-exports those classes so legacy imports keep working.
"""

from .ability import *  # noqa: F401,F403
from .health import *  # noqa: F401,F403
from .money import *  # noqa: F401,F403
