from .openai import OpenAIProvider

_PROVIDER_CLASSES: dict[str, type] = {
    "llm": OpenAIProvider,
}
