import warnings
warnings.warn(
    "proactive.llm_call_coordinator 已迁移到 llm.coordinator，请更新 import",
    DeprecationWarning, stacklevel=2,
)

from ..llm.coordinator import LLMCallCoordinator, SubmitResult  # noqa: F401
