"""LLM provider adapters: one narrow interface over Groq / Gemini / Ollama.

WHY ADAPTERS + A MANAGER instead of using any provider SDK directly:

- **Failover policy is ours.** Free tiers fail in predictable ways (429 on
  quota, 401 on a stale key, timeouts on cold starts). Deciding what happens
  next -- try the next provider, aggregate every failure into one readable
  error -- is product logic, not SDK logic, so it lives in `llm_manager` and
  is tested against fake providers behind the `LLMProvider` protocol.
- **Zero new dependencies.** Groq and Gemini ride the LangChain integrations
  already installed for M6 (langchain-groq / langchain-google-genai), which
  are also what LangGraph will consume as message objects -- so the message
  type in this module IS `langchain_core` messages, no conversion layer.
  Ollama's local `/api/chat` is a trivial stable JSON API, hand-rolled over
  injectable httpx to avoid a dependency for the one provider that may not
  even be installed.
- **`max_retries=0` on both LangChain clients, on purpose.** SDK-internal
  retries would hide rate limits behind silent backoff and make failover
  timing nondeterministic. Failover IS our retry strategy (generation has no
  side effects, so trying the next provider is always safe); waiting out a
  Groq 429 in-place is deliberately deferred -- see IMPLEMENTATION_LOG M5.

ERROR TAXONOMY -- every provider failure is normalized into `LLMProviderError`
subclasses carrying `.provider` (+ `.status_code` when known), because the
manager's only decision is "fail over or give up", and both decisions need
the *cause* to be legible in logs and in the final aggregated error.

Mapping uses duck-typing (`status_code` / `code` attributes) plus exception-
class-name heuristics rather than isinstance chains across four libraries
(groq SDK, langchain-core, httpx, google.api_core) -- each release renames
its exceptions, names + status codes are stable.
"""

import re
import time
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import httpx
from langchain_core.messages import AIMessage, BaseMessage

from app.config import Settings, get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Gemini model used when FALLBACK_LLM_MODEL is left empty (see config.py).
# Live-verification history, 2026-10-31 (why this is config, not a hardcode):
# - "gemini-2.5-flash" -> 404 "no longer available to new users" (retired);
# - Google's suggested replacement "gemini-3.8-flash" (and "gemini-flash-
#   latest") hangs on generateContent from this network until the client
#   deadline (probed at both 30s and 90s -- dies at the deadline each time),
#   while the models-list endpoint answers instantly (endpoint reachable,
#   model listed as authorized -- so a genuinely server-side quirk);
# - "gemini-3.5-flash" answered reliably (7s) and is the verified default.
# Model lists drift AND availability varies by network/region: the adapter
# must never assume; this constant is the one place a default lives.
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash"


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class for all LLM-layer failures."""


class LLMConfigurationError(LLMError):
    """The manager/chain is misconfigured (no usable providers, bad names).

    Raised BEFORE any call is attempted; failover cannot fix configuration,
    so this one always propagates immediately.
    """


class LLMProviderError(LLMError):
    """A provider call failed. Failover-able by design."""

    def __init__(self, message: str, *, provider: str, status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class LLMRequestTooLargeError(LLMProviderError):
    """Provider rejected a request because its input exceeded a hard limit."""


class LLMRateLimitError(LLMProviderError):
    """429 / quota exhausted (free tier: wait or switch provider)."""


class LLMAuthError(LLMProviderError):
    """401/403: missing/invalid key. Usually a configuration fix, not a retry."""


class LLMTimeoutError(LLMProviderError):
    """The provider did not answer within the configured timeout."""


class LLMNetworkError(LLMProviderError):
    """Connection-level failure (Ollama not running, DNS, reset)."""


class LLMAllProvidersFailed(LLMError):
    """Every provider in the chain failed; carries each (provider, cause)."""

    def __init__(self, failures: Sequence[tuple[str, str]]):
        self.failures = list(failures)
        detail = "; ".join(f"{name}: {cause}" for name, cause in self.failures)
        super().__init__(f"All LLM providers failed -> {detail}")


@dataclass(frozen=True)
class LLMResponse:
    """Provider-normalized completion with provenance for the citation layer.

    `provider` + `model` travel with every generated text so the UI (M10) and
    reports can state WHICH model produced an LLM INTERPRETATION -- the same
    provenance discipline applied to numbers in M2, applied to language.
    """

    text: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float


class LLMProvider(Protocol):
    """What LLMManager requires of a provider (the failover seam)."""

    name: str
    model_name: str

    def generate(self, messages: Sequence[BaseMessage]) -> LLMResponse:
        ...

    def close(self) -> None:
        ...


# ---------------------------------------------------------------------------
# Error normalization
# ---------------------------------------------------------------------------

_RATE_LIMIT_NAMES = ("ratelimit", "rate_limit", "resourceexhausted", "quota", "too_many_requests")
_AUTH_NAMES = ("authentication", "unauthenticated", "permissiondenied", "forbidden", "unauthorized")
_TIMEOUT_NAMES = ("timeout", "timedout", "deadline")  # "deadline": google.api_core.DeadlineExceeded
_NETWORK_NAMES = ("connection", "connecterror", "network")


def _classify(exc: Exception, provider: str) -> LLMProviderError:
    """Normalize any provider exception into the taxonomy above.

    Order matters: explicit HTTP status first (most precise), then timeout /
    network detection (transient, must be distinguished from 4xx), then
    class-name heuristics, then a generic provider error carrying the raw
    message -- never swallow the original text.
    """
    status: int | None = None
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            status = value
            break
    if status is None:
        # httpx.HTTPStatusError (what our Ollama adapter raises for non-200)
        # carries the status on .response.status_code, not directly.
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
        if isinstance(value, int) and 100 <= value <= 599:
            status = value
    name = type(exc).__name__.lower()
    text = f"{type(exc).__name__}: {exc}"

    if status in (413, 400) and any(
        marker in str(exc).lower()
        for marker in ("too many tokens", "token limit", "request too large", "context length")
    ):
        return LLMRequestTooLargeError(
            f"{provider} rejected an oversized request: {text}",
            provider=provider,
            status_code=status,
        )
    if status == 413:
        return LLMRequestTooLargeError(
            f"{provider} rejected an oversized request: {text}",
            provider=provider,
            status_code=status,
        )
    if status == 429 or any(k in name for k in _RATE_LIMIT_NAMES):
        return LLMRateLimitError(f"{provider} rate limited: {text}", provider=provider, status_code=status or 429)
    if status in (401, 403) or any(k in name for k in _AUTH_NAMES):
        return LLMAuthError(f"{provider} auth failed: {text}", provider=provider, status_code=status)
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)) or any(k in name for k in _TIMEOUT_NAMES):
        return LLMTimeoutError(f"{provider} timed out: {text}", provider=provider, status_code=status)
    if isinstance(exc, (httpx.NetworkError, ConnectionError)) or any(k in name for k in _NETWORK_NAMES):
        return LLMNetworkError(f"{provider} unreachable: {text}", provider=provider, status_code=status)
    return LLMProviderError(f"{provider} call failed: {text}", provider=provider, status_code=status)


_RETRY_AFTER_RE = re.compile(r"try again in (\d+(?:\.\d+)?)s", re.IGNORECASE)


def parse_retry_after(message: str) -> float | None:
    """Seconds a provider asks us to wait, when it says so (e.g. Groq's
    "Please try again in 13.8075s" on a 429).

    Bounded to [1, 60]: a provider hint is a courtesy, not a schedule -- a
    free tier that asks for minutes gets a one-minute cap (enough to clear
    a per-minute token window, which a capped-30s wait demonstrably failed
    to do live), and the chain still fails over after one wait.
    """
    match = _RETRY_AFTER_RE.search(message or "")
    if not match:
        return None
    return max(1.0, min(float(match.group(1)), 60.0))


def _content_text(message: BaseMessage) -> str:
    """AIMessage.content is `str` for classic chat models but a list of
    content blocks for newer ones; normalize without losing text blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else [content]:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _usage(message: BaseMessage) -> tuple[int | None, int | None]:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return usage.get("input_tokens"), usage.get("output_tokens")
    return None, None


# ---------------------------------------------------------------------------
# Groq (primary) -- LangChain integration
# ---------------------------------------------------------------------------

class GroqProvider:
    """Groq free-tier chat via langchain-groq.

    `model` is injectable so unit tests can pass a fake runnable (same
    duck-typed seam the pipeline tests use for embedders); production builds
    a real ChatGroq in `build_from_settings`.
    """

    name = "groq"

    def __init__(self, model: Any, model_name: str):
        self._model = model
        self.model_name = model_name

    def generate(self, messages: Sequence[BaseMessage]) -> LLMResponse:
        start = time.perf_counter()
        try:
            ai = self._model.invoke(list(messages))
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 -- normalized into the taxonomy
            raise _classify(exc, self.name) from exc
        latency_ms = (time.perf_counter() - start) * 1000.0
        inputs, outputs = _usage(ai)
        text = _content_text(ai)
        if not text.strip():
            raise LLMProviderError(
                f"{self.name} returned an empty completion", provider=self.name
            )
        return LLMResponse(
            text=text, provider=self.name, model=self.model_name,
            input_tokens=inputs, output_tokens=outputs, latency_ms=latency_ms,
        )

    def bind_tools(self, tools):
        """Tools bound on the underlying LangChain runnable (M6 agent)."""
        return self._model.bind_tools(tools)

    def close(self) -> None:
        # langchain-groq owns an underlying groq.Client with an httpx client;
        # close best-effort, attributes differ across SDK versions.
        client = getattr(self._model, "client", None)
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 -- closing must never mask results
                pass


# ---------------------------------------------------------------------------
# Gemini (fallback) -- LangChain integration; same seam as Groq
# ---------------------------------------------------------------------------

class GeminiProvider:
    name = "gemini"

    def __init__(self, model: Any, model_name: str):
        self._model = model
        self.model_name = model_name

    def generate(self, messages: Sequence[BaseMessage]) -> LLMResponse:
        # Identical flow to Groq on purpose: the LangChain runnable interface
        # is the normalization point, so provider differences stop HERE
        # (construction in build_from_settings) and never leak into callers.
        start = time.perf_counter()
        try:
            ai = self._model.invoke(list(messages))
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc, self.name) from exc
        latency_ms = (time.perf_counter() - start) * 1000.0
        inputs, outputs = _usage(ai)
        text = _content_text(ai)
        if not text.strip():
            raise LLMProviderError(
                f"{self.name} returned an empty completion", provider=self.name
            )
        return LLMResponse(
            text=text, provider=self.name, model=self.model_name,
            input_tokens=inputs, output_tokens=outputs, latency_ms=latency_ms,
        )

    def bind_tools(self, tools):
        """Tools bound on the underlying LangChain runnable (M6 agent)."""
        return self._model.bind_tools(tools)

    def close(self) -> None:
        client = getattr(self._model, "client", None)
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Ollama (optional, fully local) -- hand-rolled over injectable httpx
# ---------------------------------------------------------------------------

class OllamaProvider:
    """Local Ollama `/api/chat` (stream=False).

    Hand-rolled because the API is one stable JSON POST, and because an
    injectable transport gives this adapter the same offline MockTransport
    tests the SEC client has. Connection refusal (server not running) maps to
    LLMNetworkError so the chain can fail over to a hosted free tier.
    """

    name = "ollama"

    def __init__(
        self,
        model_name: str,
        base_url: str,
        *,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ):
        if not model_name:
            raise LLMConfigurationError(
                "ollama is in llm_provider_chain but OLLAMA_MODEL is empty. "
                "Set OLLAMA_MODEL (e.g. llama3.2) or remove ollama from the chain."
            )
        self.model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Own client: explicit timeout keeps the failover chain bounded even
        # when a local model is loading.
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            headers={"Content-Type": "application/json"},
        )

    @staticmethod
    def _role(message: BaseMessage) -> str:
        # langchain message types: "system" | "human" | "ai" | ...
        mtype = message.type
        return {"human": "user", "ai": "assistant"}.get(mtype, mtype)

    def generate(self, messages: Sequence[BaseMessage]) -> LLMResponse:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": self._role(m), "content": str(m.content)} for m in messages
            ],
            "stream": False,
            "options": {
                "temperature": self._temperature,
                "num_predict": self._max_tokens,
            },
        }
        start = time.perf_counter()
        try:
            response = self._http.post("/api/chat", json=payload)
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc, self.name) from exc
        latency_ms = (time.perf_counter() - start) * 1000.0

        if response.status_code != 200:
            exc = httpx.HTTPStatusError(
                f"HTTP {response.status_code}: {response.text[:200]}",
                request=response.request,
                response=response,
            )
            raise _classify(exc, self.name)

        try:
            body = response.json()
            text = body["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMProviderError(
                f"{self.name} returned a malformed body: {exc}",
                provider=self.name,
                status_code=200,
            ) from exc
        if not str(text).strip():
            raise LLMProviderError(
                f"{self.name} returned an empty completion", provider=self.name
            )
        return LLMResponse(
            text=str(text), provider=self.name, model=self.model_name,
            input_tokens=body.get("prompt_eval_count"),
            output_tokens=body.get("eval_count"),
            latency_ms=latency_ms,
        )

    def close(self) -> None:
        self._http.close()


# ---------------------------------------------------------------------------
# Construction from settings (the zero-cost guard lives here)
# ---------------------------------------------------------------------------

def _key_usable(key: str) -> bool:
    """True when an env var holds something that looks like a real key.

    .env.example ships empty values; template markers ("your-key", "<...>")
    must never count as configured -- treating them as real would send a
    doomed request instead of printing the setup instructions.
    """
    key = (key or "").strip()
    if not key:
        return False
    lowered = key.lower()
    return not any(marker in lowered for marker in ("your", "xxx", "placeholder", "<", "example"))


def build_providers(settings: Settings | None = None) -> list[LLMProvider]:
    """Build the configured failover chain, skipping unusable providers.

    Zero-cost policy enforcement: only these three implementations exist, a
    provider without credentials is skipped (logged), and if nothing remains
    the caller gets LLMConfigurationError with setup steps -- there is no
    code path from here to any paid API.
    """
    settings = settings or get_settings()
    chain = [part.strip().lower() for part in settings.llm_provider_chain.split(",") if part.strip()]
    if not chain:
        raise LLMConfigurationError("llm_provider_chain is empty.")

    allowed = {"groq", "gemini", "ollama"}
    unknown = [name for name in chain if name not in allowed]
    if unknown:
        raise LLMConfigurationError(
            f"Unknown provider(s) {unknown} in llm_provider_chain; "
            f"allowed: {sorted(allowed)}."
        )

    providers: list[LLMProvider] = []
    for name in chain:
        if name == "groq":
            if not _key_usable(settings.groq_api_key):
                logger.info("groq skipped: GROQ_API_KEY not configured")
                continue
            from langchain_groq import ChatGroq  # local: keeps import cost + google warnings out of tests

            model = ChatGroq(
                model=settings.llm_model,
                groq_api_key=settings.groq_api_key.strip(),
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                request_timeout=settings.llm_timeout_seconds,
                max_retries=0,  # failover is the only retry strategy (see module docstring)
            )
            providers.append(GroqProvider(model, settings.llm_model))
        elif name == "gemini":
            if not _key_usable(settings.gemini_api_key):
                logger.info("gemini skipped: GEMINI_API_KEY not configured")
                continue
            # google.api_core emits a FutureWarning at import on Python 3.10
            # ("not a supported version"). It is real advice (3.10 is EOL) but
            # functional today, and printing it on every CLI run trains people
            # to ignore stderr -- filter THIS narrow message only.
            import warnings

            warnings.filterwarnings(
                "ignore",
                message=r".*You are using a non-supported Python version.*",
                category=FutureWarning,
            )
            from langchain_google_genai import ChatGoogleGenerativeAI

            model_name = settings.fallback_llm_model or GEMINI_DEFAULT_MODEL
            model = ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=settings.gemini_api_key.strip(),
                temperature=settings.llm_temperature,
                max_output_tokens=settings.llm_max_tokens,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )
            providers.append(GeminiProvider(model, model_name))
        elif name == "ollama":
            # Ollama needs no key; it is reachable-or-not, decided per call.
            providers.append(OllamaProvider(
                model_name=settings.ollama_model,
                base_url=settings.ollama_base_url,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                timeout_seconds=settings.llm_timeout_seconds,
            ))

    if not providers:
        raise LLMConfigurationError(
            "No usable LLM provider. This project uses free tiers only: set "
            "GROQ_API_KEY (https://console.groq.com/keys) or GEMINI_API_KEY "
            "(https://aistudio.google.com/apikey) in backend/.env, or add "
            "ollama + OLLAMA_MODEL for a fully local option. No paid API is "
            "ever used as a fallback."
        )
    return providers
