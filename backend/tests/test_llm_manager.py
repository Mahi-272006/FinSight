"""Tests for LLMManager failover policy + provider chain construction (M5).

Failover is tested against fake providers behind the LLMProvider protocol --
no keys, no network, fully deterministic. Chain construction runs offline:
ChatGroq/ChatGoogleGenerativeAI construct without contacting anything.
"""

import pytest

from app.config import Settings
from app.services.llm_manager import LLMManager, build_manager
from app.services.llm_providers import (
    LLMAllProvidersFailed,
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    LLMAuthError,
    LLMResponse,
    build_providers,
)
from langchain_core.messages import HumanMessage

QUESTION = [HumanMessage(content="q")]


def _ok(provider_name="fake"):
    return LLMResponse(
        text="answer", provider=provider_name, model="m",
        input_tokens=1, output_tokens=1, latency_ms=1.0,
    )


class FakeProvider:
    """Scripted provider: pops the next outcome; records every call."""

    def __init__(self, name, outcomes):
        self.name = name
        self.model_name = "fake-model"
        self._outcomes = list(outcomes)
        self.calls = 0
        self.closed = False

    def generate(self, messages):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


class TestFailover:
    def test_first_success_skips_rest(self):
        first, second = FakeProvider("groq", [_ok("groq")]), FakeProvider("gemini", [_ok("gemini")])
        manager = LLMManager([first, second])
        resp = manager.generate(QUESTION)
        assert resp.provider == "groq"
        assert first.calls == 1
        assert second.calls == 0  # never touched after success

    @pytest.mark.parametrize("failure", [
        LLMRateLimitError("429", provider="groq"),
        LLMAuthError("401", provider="groq"),
        LLMProviderError("boom", provider="groq"),
        ValueError("unexpected adapter bug"),  # even unknown errors fail over
    ])
    def test_failure_fails_over(self, failure):
        first = FakeProvider("groq", [failure])
        second = FakeProvider("gemini", [_ok("gemini")])
        manager = LLMManager([first, second])
        resp = manager.generate(QUESTION)
        assert resp.provider == "gemini"
        assert first.calls == 1 and second.calls == 1

    def test_all_fail_aggregates_every_cause(self):
        first = FakeProvider("groq", [LLMRateLimitError("quota exceeded", provider="groq")])
        second = FakeProvider("gemini", [LLMAuthError("bad key", provider="gemini")])
        manager = LLMManager([first, second])
        with pytest.raises(LLMAllProvidersFailed) as exc_info:
            manager.generate(QUESTION)
        error = exc_info.value
        assert [name for name, _ in error.failures] == ["groq", "gemini"]
        assert "quota exceeded" in error.failures[0][1]
        assert "bad key" in error.failures[1][1]
        # One readable message naming every failure -- not just the last one.
        assert "groq" in str(error) and "gemini" in str(error)

    def test_empty_chain_rejected_at_construction(self):
        with pytest.raises(LLMConfigurationError, match="at least one"):
            LLMManager([])

    def test_empty_messages_rejected(self):
        manager = LLMManager([FakeProvider("groq", [_ok()])])
        with pytest.raises(LLMConfigurationError, match="no messages"):
            manager.generate([])

    def test_provider_names_reflect_chain_order(self):
        names = LLMManager([FakeProvider("a", [_ok()]), FakeProvider("b", [_ok()])]).provider_names
        assert names == ["a", "b"]

    def test_context_manager_closes_all(self):
        first, second = FakeProvider("a", [_ok()]), FakeProvider("b", [_ok()])
        with LLMManager([first, second]):
            pass
        assert first.closed and second.closed

    def test_close_swallows_provider_close_errors(self):
        class BadClose(FakeProvider):
            def close(self):
                super().close()
                raise RuntimeError("sdk weirdness")

        LLMManager([BadClose("a", [_ok()])]).close()  # must not raise


# ---------------------------------------------------------------------------
# Chain construction (the zero-cost guard)
# ---------------------------------------------------------------------------

def make_settings(**overrides) -> Settings:
    kwargs = dict(
        sec_user_agent="FinSight Test Suite finsight-tests@noreply.local",
        groq_api_key="",
        gemini_api_key="",
        llm_provider_chain="groq,gemini",
        ollama_model="",
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


class TestBuildProviders:
    def test_no_keys_raises_with_setup_instructions(self):
        with pytest.raises(LLMConfigurationError) as exc_info:
            build_providers(make_settings())
        message = str(exc_info.value)
        # Actionable + explicitly zero-cost.
        assert "GROQ_API_KEY" in message
        assert "GEMINI_API_KEY" in message
        assert "free" in message.lower()
        assert "paid" in message.lower()

    def test_groq_key_only_builds_one_provider(self):
        providers = build_providers(make_settings(groq_api_key="gsk_real_key_123"))
        assert [p.name for p in providers] == ["groq"]

    def test_both_keys_build_chain_in_configured_order(self):
        providers = build_providers(make_settings(
            groq_api_key="gsk_real_key_123", gemini_api_key="AIzaRealKeyValue123",
        ))
        assert [p.name for p in providers] == ["groq", "gemini"]

    def test_custom_chain_order_respected(self):
        providers = build_providers(make_settings(
            llm_provider_chain="gemini, groq",
            groq_api_key="gsk_real_key_123", gemini_api_key="AIzaRealKeyValue123",
        ))
        assert [p.name for p in providers] == ["gemini", "groq"]

    def test_unconfigured_providers_skipped_not_fatal(self):
        # Only Gemini configured, chain still lists groq first: skip, not error.
        providers = build_providers(make_settings(gemini_api_key="AIzaRealKeyValue123"))
        assert [p.name for p in providers] == ["gemini"]

    def test_placeholder_keys_not_treated_as_configured(self):
        with pytest.raises(LLMConfigurationError):
            build_providers(make_settings(groq_api_key="your-groq-key-here"))

    def test_unknown_provider_name_rejected(self):
        with pytest.raises(LLMConfigurationError, match="Unknown provider"):
            build_providers(make_settings(
                llm_provider_chain="groq,openai",
                groq_api_key="gsk_real_key_123",
            ))

    def test_empty_chain_string_rejected(self):
        with pytest.raises(LLMConfigurationError, match="empty"):
            build_providers(make_settings(llm_provider_chain="   "))

    def test_ollama_in_chain_requires_model(self):
        with pytest.raises(LLMConfigurationError, match="OLLAMA_MODEL"):
            build_providers(make_settings(llm_provider_chain="ollama", ollama_model=""))

    def test_ollama_with_model_builds_without_key(self):
        providers = build_providers(make_settings(
            llm_provider_chain="ollama", ollama_model="llama3.2",
        ))
        assert [p.name for p in providers] == ["ollama"]

    def test_groq_model_from_settings(self):
        providers = build_providers(make_settings(
            groq_api_key="gsk_real_key_123", llm_model="some/other-model",
        ))
        assert providers[0].model_name == "some/other-model"

    def test_gemini_empty_model_gets_code_default(self):
        providers = build_providers(make_settings(
            gemini_api_key="AIzaRealKeyValue123", fallback_llm_model="",
        ))
        from app.services.llm_providers import GEMINI_DEFAULT_MODEL
        assert providers[0].model_name == GEMINI_DEFAULT_MODEL

    def test_build_manager_wraps_providers(self):
        manager = build_manager(make_settings(groq_api_key="gsk_real_key_123"))
        assert manager.provider_names == ["groq"]
        manager.close()
