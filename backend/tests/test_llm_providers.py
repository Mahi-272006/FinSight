"""Tests for LLM provider adapters (M5) -- offline, no keys, no network.

Two seams, both fake-driven:
- LangChain providers take an injectable runnable (`model` param), so error
  CLASSIFICATION -- the logic we own -- is tested by raising crafted
  exceptions from a fake `invoke`.
- Ollama takes an injectable httpx transport, so the wire format (roles,
  options, parsing) is tested with MockTransport, like the SEC client.
One extra test drives a REAL ChatGroq over MockTransport to prove the seam
holds for the actual SDK, not just our fakes.
"""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.config import Settings
from app.services.llm_providers import (
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    LLMAuthError,
    LLMNetworkError,
    LLMTimeoutError,
    GeminiProvider,
    GroqProvider,
    OllamaProvider,
    _key_usable,
    parse_retry_after,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeRunnable:
    """Duck-typed ChatGroq/ChatGoogleGenerativeAI: .invoke only."""

    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return self.result


def _ai(text="answer", usage=None):
    if usage is not None and "total_tokens" not in usage:
        # langchain-core's UsageMetadata requires total_tokens.
        usage = {**usage, "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)}
    return AIMessage(content=text, usage_metadata=usage)


MESSAGES = [SystemMessage(content="policy"), HumanMessage(content="question")]


# ---------------------------------------------------------------------------
# Groq / Gemini adapters (shared runnable seam)
# ---------------------------------------------------------------------------

class TestLangChainAdapters:
    @pytest.mark.parametrize("provider_cls,name", [
        (GroqProvider, "groq"),
        (GeminiProvider, "gemini"),
    ])
    def test_success_normalized_to_response(self, provider_cls, name):
        runnable = FakeRunnable(result=_ai("grounded answer", {"input_tokens": 11, "output_tokens": 7}))
        provider = provider_cls(runnable, "test-model")
        resp = provider.generate(MESSAGES)
        assert resp.text == "grounded answer"
        assert resp.provider == name
        assert resp.model == "test-model"
        assert resp.input_tokens == 11
        assert resp.output_tokens == 7
        assert resp.latency_ms >= 0
        # The runnable received OUR messages unchanged (identity mapping).
        assert runnable.calls[0] is MESSAGES or runnable.calls[0] == list(MESSAGES)

    @pytest.mark.parametrize("provider_cls", [GroqProvider, GeminiProvider])
    def test_list_content_blocks_joined(self, provider_cls):
        runnable = FakeRunnable(result=AIMessage(content=[
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ]))
        provider = provider_cls(runnable, "m")
        assert provider.generate(MESSAGES).text == "part one\npart two"

    @pytest.mark.parametrize("provider_cls,name", [
        (GroqProvider, "groq"),
        (GeminiProvider, "gemini"),
    ])
    def test_empty_completion_rejected(self, provider_cls, name):
        provider = provider_cls(FakeRunnable(result=_ai("   ")), "m")
        with pytest.raises(LLMProviderError, match="empty completion"):
            provider.generate(MESSAGES)

    # --- error classification (the logic we own) ---

    @pytest.mark.parametrize("provider_cls", [GroqProvider, GeminiProvider])
    def test_429_maps_to_rate_limit(self, provider_cls):
        class Exploding429(Exception):
            status_code = 429

        provider = provider_cls(FakeRunnable(raises=Exploding429("quota")), "m")
        with pytest.raises(LLMRateLimitError) as exc_info:
            provider.generate(MESSAGES)
        assert exc_info.value.status_code == 429

    @pytest.mark.parametrize("provider_cls", [GroqProvider, GeminiProvider])
    def test_401_maps_to_auth(self, provider_cls):
        class Exploding401(Exception):
            status_code = 401

        provider = provider_cls(FakeRunnable(raises=Exploding401("bad key")), "m")
        with pytest.raises(LLMAuthError):
            provider.generate(MESSAGES)

    @pytest.mark.parametrize("provider_cls", [GroqProvider, GeminiProvider])
    def test_timeout_and_network_distinct(self, provider_cls):
        p1 = provider_cls(FakeRunnable(raises=httpx.ReadTimeout("slow")), "m")
        with pytest.raises(LLMTimeoutError):
            p1.generate(MESSAGES)
        p2 = provider_cls(FakeRunnable(raises=httpx.ConnectError("refused")), "m")
        with pytest.raises(LLMNetworkError):
            p2.generate(MESSAGES)

    @pytest.mark.parametrize("provider_cls", [GroqProvider, GeminiProvider])
    def test_name_heuristic_without_status(self, provider_cls):
        # langchain-core / google exceptions don't all carry status codes;
        # class-name heuristics must still classify (RateLimitError, etc.).
        class RateLimitError(Exception):
            pass

        provider = provider_cls(FakeRunnable(raises=RateLimitError("429 slow down")), "m")
        with pytest.raises(LLMRateLimitError):
            provider.generate(MESSAGES)

    @pytest.mark.parametrize("provider_cls", [GroqProvider, GeminiProvider])
    def test_unknown_error_becomes_provider_error_with_text(self, provider_cls):
        provider = provider_cls(FakeRunnable(raises=ValueError("weird internal")), "m")
        with pytest.raises(LLMProviderError, match="weird internal"):
            provider.generate(MESSAGES)

    def test_close_never_raises(self):
        class NoClient:
            pass

        GroqProvider(FakeRunnable(), "m").close()  # no client attribute at all
        class BadClient:
            def close(self):
                raise RuntimeError("already closed")

        class HasClient:
            client = BadClient()

        GroqProvider(HasClient(), "m").close()  # must swallow, not raise


# ---------------------------------------------------------------------------
# Retry-After parsing (M6: the wait-on-429 policy reads the provider's hint)
# ---------------------------------------------------------------------------

class TestParseRetryAfter:
    def test_groq_style_hint_parsed_exactly(self):
        # The exact wording seen live on Groq's free tier.
        message = ("Rate limit reached for model. TPM: Limit 8000. "
                   "Please try again in 13.8075s")
        assert parse_retry_after(message) == pytest.approx(13.8075)

    def test_case_insensitive(self):
        assert parse_retry_after("PLEASE TRY AGAIN IN 5S") == pytest.approx(5.0)

    def test_no_hint_returns_none(self):
        assert parse_retry_after("quota exceeded for the day") is None
        assert parse_retry_after("") is None

    def test_hint_capped_at_sixty_seconds(self):
        # A provider asking for minutes gets a one-minute cap -- waiting
        # cannot fix a broken key, and the chain still fails over after ONE
        # wait. (30s was tried live and did NOT clear Groq's TPM window.)
        assert parse_retry_after("try again in 600s") == 60.0

    def test_hint_floored_at_one_second(self):
        # Sub-second hints are noise; one second is the smallest useful wait.
        assert parse_retry_after("try again in 0.2s") == 1.0

    def test_non_seconds_format_not_misread(self):
        # "Retry-After: 60" (header style, no 'in ...s') is deliberately not
        # parsed: we only trust wording we have observed and tested.
        assert parse_retry_after("Retry-After: 60") is None


# ---------------------------------------------------------------------------
# Real ChatGroq over MockTransport (proves the seam beyond our fakes)
# ---------------------------------------------------------------------------

class TestRealChatGroqSeam:
    def test_request_and_response_roundtrip(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["authorization"] = request.headers.get("authorization", "")
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "from real sdk"}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
            })

        from langchain_groq import ChatGroq

        model = ChatGroq(
            model="test-model", groq_api_key="gsk_test", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        provider = GroqProvider(model, "test-model")
        resp = provider.generate(MESSAGES)
        assert resp.text == "from real sdk"
        assert resp.input_tokens == 9
        assert resp.output_tokens == 4
        assert "test-model" in seen["body"]
        assert seen["authorization"].startswith("Bearer ")  # SDK sets auth from key


# ---------------------------------------------------------------------------
# Ollama (hand-rolled, MockTransport)
# ---------------------------------------------------------------------------

def _ollama(transport, **overrides):
    kwargs = dict(
        model_name="llama3.2",
        base_url="http://localhost:11434",
        temperature=0.1,
        max_tokens=128,
        timeout_seconds=5.0,
        transport=transport,
    )
    kwargs.update(overrides)
    return OllamaProvider(**kwargs)


class TestOllamaProvider:
    def test_success_parses_native_shape(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={
                "model": "llama3.2",
                "message": {"role": "assistant", "content": "local answer"},
                "prompt_eval_count": 21,
                "eval_count": 8,
            })

        provider = _ollama(httpx.MockTransport(handler))
        resp = provider.generate(MESSAGES)
        assert resp.text == "local answer"
        assert resp.provider == "ollama"
        assert resp.input_tokens == 21
        assert resp.output_tokens == 8
        assert seen["path"] == "/api/chat"
        # Wire format: LangChain roles -> Ollama roles, stream off, options.
        assert seen["body"]["stream"] is False
        roles = [m["role"] for m in seen["body"]["messages"]]
        assert roles == ["system", "user"]
        assert seen["body"]["options"]["num_predict"] == 128
        assert seen["body"]["options"]["temperature"] == 0.1

    def test_ai_message_maps_to_assistant_role(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"message": {"content": "ok"}})

        provider = _ollama(httpx.MockTransport(handler))
        provider.generate(MESSAGES + [AIMessage(content="prior turn")])
        roles = [m["role"] for m in captured["body"]["messages"]]
        assert roles == ["system", "user", "assistant"]

    def test_missing_model_is_configuration_error(self):
        with pytest.raises(LLMConfigurationError, match="OLLAMA_MODEL"):
            _ollama(httpx.MockTransport(lambda r: httpx.Response(200)), model_name="")

    def test_connection_refused_maps_to_network_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        provider = _ollama(httpx.MockTransport(handler))
        with pytest.raises(LLMNetworkError):
            provider.generate(MESSAGES)

    def test_http_404_maps_to_provider_error_with_status(self):
        provider = _ollama(httpx.MockTransport(
            lambda r: httpx.Response(404, text='{"error": "model not found"}')
        ))
        with pytest.raises(LLMProviderError) as exc_info:
            provider.generate(MESSAGES)
        assert exc_info.value.status_code == 404

    def test_malformed_body_rejected(self):
        provider = _ollama(httpx.MockTransport(lambda r: httpx.Response(200, text="not json")))
        with pytest.raises(LLMProviderError, match="malformed"):
            provider.generate(MESSAGES)

    def test_empty_completion_rejected(self):
        provider = _ollama(httpx.MockTransport(
            lambda r: httpx.Response(200, json={"message": {"content": ""}})
        ))
        with pytest.raises(LLMProviderError, match="empty"):
            provider.generate(MESSAGES)

    def test_close_idempotent(self):
        provider = _ollama(httpx.MockTransport(lambda r: httpx.Response(200)))
        provider.close()
        provider.close()


# ---------------------------------------------------------------------------
# Key / chain hygiene
# ---------------------------------------------------------------------------

class TestKeyUsable:
    @pytest.mark.parametrize("key", ["", "   ", "your-grok-key-here", "<KEY>", "placeholder", "xxx-gsk"])
    def test_unusable(self, key):
        assert not _key_usable(key)

    @pytest.mark.parametrize("key", ["gsk_live_abc123", "AIzaSyAbC123RealLookingKeyValue"])
    def test_usable(self, key):
        assert _key_usable(key)

    def test_example_com_marker_unusable(self):
        # `.local` test addresses are fine for SEC (no request is made), but a
        # KEY containing "example" is treated as a template value.
        assert not _key_usable("key-example-123")
