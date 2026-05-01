from dataclasses import dataclass
from typing import Literal


@dataclass(eq=False)
class ShareTarget:
    user_id: str
    group_id: str = ""
    is_group: bool = False
    priority: int = 0
    score: float = 0.0
    policy: Literal["force", "normal"] = "normal"
