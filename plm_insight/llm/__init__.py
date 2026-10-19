from plm_insight.config import Settings
from plm_insight.llm.base import LLMError, LLMProvider
from plm_insight.llm.replay import ReplayProvider, record


class RecordingProvider:
    """Wraps a live provider and saves each response for later offline replay."""

    def __init__(self, inner: LLMProvider, settings: Settings):
        self.inner = inner
        self.settings = settings
        self.name = f"{inner.name} (recording)"

    def review_duplicates(self, input_text: str):
        result = self.inner.review_duplicates(input_text)
        record(self.settings.replay_dir, "duplicates", input_text, result)
        return result

    def compliance_packet(self, input_text: str):
        result = self.inner.compliance_packet(input_text)
        record(self.settings.replay_dir, "compliance", input_text, result)
        return result

    def return_themes(self, input_text: str):
        result = self.inner.return_themes(input_text)
        record(self.settings.replay_dir, "returns", input_text, result)
        return result


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "replay":
        return ReplayProvider(settings.replay_dir)
    if settings.llm_provider in ("anthropic", "foundry"):
        if settings.llm_provider == "anthropic":
            from plm_insight.llm.anthropic_provider import AnthropicProvider

            provider = AnthropicProvider(settings.model)
        else:
            from plm_insight.llm.foundry import foundry_provider

            provider = foundry_provider(settings.model)
        return RecordingProvider(provider, settings) if settings.record else provider
    raise ValueError(f"Unknown PI_LLM_PROVIDER: {settings.llm_provider!r}")


__all__ = ["LLMError", "LLMProvider", "build_provider"]
