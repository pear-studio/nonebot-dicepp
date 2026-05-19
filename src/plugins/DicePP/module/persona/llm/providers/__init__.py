from .openai import OpenAIProvider
from .minimax_image import MiniMaxImageProvider

_PROVIDER_CLASSES: dict[str, type] = {
    "llm": OpenAIProvider,
    "gen": MiniMaxImageProvider,
}
