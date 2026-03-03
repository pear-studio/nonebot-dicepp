import sys
from pathlib import Path

dicepp_path = Path(__file__).parent
if str(dicepp_path) not in sys.path:
    sys.path.insert(0, str(dicepp_path))
