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
    LLMError,
    LLMProviderError,
    LLMRequestTooLargeError,
    LLMRateLimitError,
    LLMAuthError,
    LLMResponse,
    build_providers,
)
from langchain_core.messages import AIMessage, HumanMessage

QUESTION = [HumanMessage(content="q")]


def _ok(provider_name="fake"):
    return LLMResponse(
        text="answer", provider=provider_name, model="m",
        input_tokens=1, output_tokens=1, latency_ms=1.0,
    )


class RateLimitError(Exception):
    """Raw provider-shaped 429: class name must trip _classify's heuristics."""


class OversizedRequestError(Exception):
    status_code = 413


def _ai(text="ok"):
    return AIMessage(content=text)


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

    # Rate limits are NOT in this list anymore: M6's policy gives them one
    # wait-and-retry first (TestRateLimitWait below). Waiting cannot fix
    # these, so they still fail over immediately, untouched.
    @pytest.mark.parametrize("failure", [
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
        # The first provider 429s twice (wait-once policy: one wait, one
        # retry, then failover) -- the aggregate still reports its cause.
        first = FakeProvider("groq", [
            LLMRateLimitError("quota exceeded", provider="groq"),
            LLMRateLimitError("quota exceeded", provider="groq"),
        ])
        second = FakeProvider("gemini", [LLMAuthError("bad key", provider="gemini")])
        manager = LLMManager([first, second], sleep=lambda _s: None)
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
# M6 hardening: one wait on rate limits (Retry-After honored), then failover
# ---------------------------------------------------------------------------

class TestRateLimitWait:
    def test_wait_uses_provider_hint_then_succeeds(self):
        waits = []
        provider = FakeProvider("groq", [
            RateLimitError(
                "Rate limit reached for model. Please try again in 13.8075s"
            ),
            _ok("groq"),
        ])
        manager = LLMManager([provider], sleep=waits.append)
        resp = manager.generate(QUESTION)
        assert resp.provider == "groq"
        # The provider's own hint is honored, not our default.
        assert waits == [pytest.approx(13.8075)]
        assert provider.calls == 2  # one wait, one retry, same provider

    def test_wait_defaults_when_no_hint_then_fails_over(self):
        waits = []
        first = FakeProvider("groq", [
            RateLimitError("rate limit reached"),
            RateLimitError("rate limit reached"),  # retry is limited too
        ])
        second = FakeProvider("gemini", [_ok("gemini")])
        manager = LLMManager([first, second], sleep=waits.append)
        resp = manager.generate(QUESTION)
        assert resp.provider == "gemini"
        assert waits == [12.0]  # ONE wait (config default), then failover
        assert first.calls == 2 and second.calls == 1

    @pytest.mark.parametrize("failure", [
        LLMAuthError("401", provider="groq"),
        LLMProviderError("boom", provider="groq"),
        ValueError("unexpected adapter bug"),
    ])
    def test_non_rate_errors_fail_over_without_waiting(self, failure):
        waits = []
        first = FakeProvider("groq", [failure])
        second = FakeProvider("gemini", [_ok("gemini")])
        manager = LLMManager([first, second], sleep=waits.append)
        resp = manager.generate(QUESTION)
        assert resp.provider == "gemini"
        assert waits == []  # waiting cannot fix auth/bugs -- fail over now
        assert first.calls == 1  # and no retry either

    def test_wait_is_capped_by_provider_hint(self):
        waits = []
        provider = FakeProvider("groq", [
            RateLimitError("Please try again in 600s"),  # asks for minutes
            _ok("groq"),
        ])
        manager = LLMManager([provider], sleep=waits.append)
        manager.generate(QUESTION)
        assert waits == [60.0]  # capped: a hint is a courtesy, not a schedule


# ---------------------------------------------------------------------------
# M6: the same policy on the tool-bound chain the graph uses
# ---------------------------------------------------------------------------

class FakeRunnable:
    """Scripted tool-bound runnable: pops the next outcome per invoke."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeToolProvider:
    """Provider exposing bind_tools (what LLMManager.bind_tools selects on)."""

    def __init__(self, name, outcomes):
        self.name = name
        self.model_name = "fake-model"
        self._runnable = FakeRunnable(outcomes)

    def bind_tools(self, tools):
        return self._runnable

    def close(self):
        pass


class TestBindToolsRateLimitRetry:
    def test_oversized_groq_request_fails_over_without_rate_limit_wait(self):
        waits = []
        first = FakeToolProvider("groq", [
            OversizedRequestError("TPM limit 8000, requested 8738 tokens"),
        ])
        second = FakeToolProvider("gemini", [_ai("from gemini")])
        chain = LLMManager([first, second], sleep=waits.append).bind_tools([])
        assert chain.invoke(QUESTION).content == "from gemini"
        assert first._runnable.calls == 1
        assert second._runnable.calls == 1
        assert waits == []

    def test_oversized_error_is_classified_when_all_tool_providers_fail(self):
        first = FakeToolProvider("groq", [
            OversizedRequestError("TPM limit 8000, requested 8738 tokens"),
        ])
        second = FakeToolProvider("gemini", [
            LLMProviderError("unavailable", provider="gemini"),
        ])
        chain = LLMManager([first, second], sleep=lambda _seconds: None).bind_tools([])
        with pytest.raises(LLMRequestTooLargeError):
            chain.invoke(QUESTION)

    def test_wait_honors_hint_then_succeeds(self):
        waits = []
        provider = FakeToolProvider("groq", [
            RateLimitError("429: Please try again in 13.8075s"),
            _ai("tool call"),
        ])
        manager = LLMManager([provider], sleep=waits.append)
        chain = manager.bind_tools([])
        result = chain.invoke(QUESTION)
        assert result.content == "tool call"
        assert waits == [pytest.approx(13.8075)]
        assert provider._runnable.calls == 2

    def test_second_rate_limit_fails_over_to_next_provider(self):
        waits = []
        first = FakeToolProvider("groq", [
            RateLimitError("rate limit, no hint"),
            RateLimitError("rate limit, no hint"),
        ])
        second = FakeToolProvider("gemini", [_ai("from gemini")])
        manager = LLMManager([first, second], sleep=waits.append)
        chain = manager.bind_tools([])
        result = chain.invoke(QUESTION)
        assert result.content == "from gemini"
        assert waits == [12.0]  # exactly one wait across the whole chain hop
        assert first._runnable.calls == 2 and second._runnable.calls == 1

    def test_non_rate_error_fails_over_without_waiting(self):
        waits = []
        first = FakeToolProvider("groq", [
            LLMAuthError("401 unauthorized", provider="groq"),
        ])
        second = FakeToolProvider("gemini", [_ai("from gemini")])
        manager = LLMManager([first, second], sleep=waits.append)
        chain = manager.bind_tools([])
        result = chain.invoke(QUESTION)
        assert result.content == "from gemini"
        assert waits == []
        assert first._runnable.calls == 1

    def test_whole_chain_failing_raises_our_taxonomy_not_sdk_error(self):
        # langchain's fallbacks re-raise the FIRST branch's error when every
        # branch fails (observed live in M6): our wrapper guarantees that
        # error is already classified, so the CLI can exit gracefully.
        waits = []
        first = FakeToolProvider("groq", [
            RateLimitError("429 no hint"),
            RateLimitError("429 no hint"),
        ])
        second = FakeToolProvider("gemini", [
            LLMAuthError("401 bad key", provider="gemini"),
        ])
        manager = LLMManager([first, second], sleep=waits.append)
        chain = manager.bind_tools([])
        with pytest.raises(LLMError) as exc_info:
            chain.invoke(QUESTION)
        assert isinstance(exc_info.value, LLMRateLimitError)
        assert waits == [12.0]

    def test_config_error_when_no_tool_capable_provider(self):
        # Ollama-only chains have no bind_tools: fail at BUILD time with an
        # actionable message, not at call time inside the graph.
        ollama_only = LLMManager([FakeProvider("ollama", [_ok()])])
        with pytest.raises(LLMConfigurationError, match="tool-capable"):
            ollama_only.bind_tools([])


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
