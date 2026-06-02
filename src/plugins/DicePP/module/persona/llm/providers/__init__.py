from .openai import OpenAIProvider
from .minimax_llm import MiniMaxProvider
from .minimax_image import MiniMaxImageProvider

_PROVIDER_CLASSES: dict[str, type] = {
    "llm": OpenAIProvider,
    "gen": MiniMaxImageProvider,
}

# Provider 级覆盖：key 为 (provider_name, category)，value 为 provider class
_PROVIDER_OVERRIDES: dict[tuple[str, str], type] = {
    ("minimax", "llm"): MiniMaxProvider,
}
