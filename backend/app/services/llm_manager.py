"""LLM manager: ordered failover across providers behind one call.

POLICY (all deliberate, all testable):

- **Chain order = priority order.** `llm_provider_chain` is walked in order;
  the first provider that answers wins and later ones are never called.
- **Every provider failure fails over** -- rate limits, auth errors,
  timeouts, connection refusals, malformed responses, even unexpected
  exceptions (recorded with their class name). Rationale: text generation has
  no side effects, so trying the next provider is always safe, and a free
  tier that is down should not take the product down.
- **One wait on rate limits, then failover (M6 hardening of M5's
  "no in-provider retry").** M5 failed over on ANY error immediately. Live
  agent usage on Groq's free 8000-TPM tier proved the cost: a 429 that says
  "try again in 13.8s" spent flaky Gemini quota instead of waiting seconds,
  and the run died. Now a rate-limit error gets ONE wait honoring the
  provider's Retry-After hint (parsed from the message, capped at 60s,
  default `llm_rate_limit_wait_seconds`), ONE retry, and only then failover.
  Every other error still fails over at once -- waiting cannot fix an auth
  error or a timeout, and side-effect-free generation makes trying the next
  provider always safe. No SDK backoff: the wait is ours, visible in the
  logs, and bounded by policy rather than hidden in an SDK.
- **All failures aggregate.** If the whole chain fails, `LLMAllProvidersFailed`
  carries (provider, cause) for EVERY attempt -- one readable error instead
  of "the last thing we tried failed", so misconfiguration (all auth errors)
  and outages (all timeouts) are distinguishable at a glance.
- **Configuration errors are not failover-able.** LLMConfigurationError
  propagates immediately; trying the next provider cannot fix a broken chain
  definition.
"""

import time
from typing import Callable, Sequence

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda

from app.config import Settings, get_settings
from app.services.llm_providers import (
    LLMAllProvidersFailed,
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    build_providers,
    parse_retry_after,
    _classify,  # shared taxonomy classifier (same package; one source of truth)
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Default wait when a 429 gives us no parseable Retry-After hint. Sized to
# the observed Groq free-tier behavior ("try again in ~14s"): long enough
# to outlast a per-minute window, short enough to stay interactive.
_DEFAULT_RATE_LIMIT_WAIT = 12.0


class LLMManager:
    """One `generate()` over an ordered list of providers."""

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        rate_limit_wait_seconds: float = _DEFAULT_RATE_LIMIT_WAIT,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not providers:
            raise LLMConfigurationError(
                "LLMManager requires at least one provider (chain is empty "
                "after skipping unconfigured ones -- see build_providers)."
            )
        self._providers = list(providers)
        # Injectable so tests can record waits instead of sleeping.
        self._rate_limit_wait_seconds = rate_limit_wait_seconds
        self._sleep = sleep

    @property
    def provider_names(self) -> list[str]:
        """Chain order, visible to callers (the CLI prints what served)."""
        return [p.name for p in self._providers]

    def generate(self, messages: Sequence[BaseMessage]) -> LLMResponse:
        """Try providers in order; return the first success, else aggregate."""
        if not messages:
            raise LLMConfigurationError("generate() called with no messages.")
        failures: list[tuple[str, str]] = []
        for provider in self._providers:
            attempt = 0
            while True:
                try:
                    response = provider.generate(messages)
                except LLMConfigurationError:
                    raise  # our bug/config -- failover cannot fix it
                except Exception as exc:  # noqa: BLE001 -- failover on everything else
                    cause = str(exc) or type(exc).__name__
                    classified = _classify(exc, provider.name)
                    if isinstance(classified, LLMRateLimitError) and attempt == 0:
                        waited = (
                            parse_retry_after(cause)
                            or self._rate_limit_wait_seconds
                        )
                        logger.warning(
                            "LLM provider %s rate limited; waiting %.1fs "
                            "once before retrying",
                            provider.name, waited,
                        )
                        self._sleep(waited)
                        attempt += 1
                        continue  # one wait, one retry, same provider
                    failures.append((provider.name, cause))
                    logger.warning(
                        "LLM provider %s failed (%s); failing over%s",
                        provider.name, type(exc).__name__,
                        "" if provider is not self._providers[-1] else " (last provider)",
                    )
                    break
                logger.info(
                    "LLM %s/%s answered in %.0f ms (in/out tokens: %s/%s)",
                    response.provider, response.model, response.latency_ms,
                    response.input_tokens, response.output_tokens,
                )
                return response
        raise LLMAllProvidersFailed(failures)

    def bind_tools(self, tools):
        """A LangChain runnable with `tools` bound, failover preserved.

        Used by M6's agent graph. Providers are tool-capable only if they
        expose `bind_tools` -- our hand-rolled Ollama adapter has no tool
        protocol, so it is skipped with a log; if that empties the set,
        LLMConfigurationError says so instead of failing at call time.

        The result is a RunnableWithFallbacks: if the primary provider
        raises mid-graph, the next tool-capable provider answers instead,
        and tool calls survive the wrapper (verified live in the M6 probe).

        Each branch is additionally wrapped with the same one-wait-one-retry
        rate-limit policy as `generate()`, plus taxonomy classification:
        langchain's fallbacks wrapper re-raises the FIRST branch's raw error
        when every branch fails (observed live), so whatever escapes this
        chain must already be our LLMError taxonomy for the CLI to exit
        gracefully instead of printing a provider traceback.
        """
        capable = [p for p in self._providers if hasattr(p, "bind_tools")]
        if not capable:
            raise LLMConfigurationError(
                "No tool-capable provider in chain: the Ollama adapter does "
                "not implement tool calling. Set GROQ_API_KEY or "
                "GEMINI_API_KEY (free tiers) for the research agent."
            )
        skipped = [p.name for p in self._providers if not hasattr(p, "bind_tools")]
        if skipped:
            logger.info(
                "tool calling: skipping non-tool provider(s) %s", ", ".join(skipped)
            )
        runnables = [
            self._wrap_branch(p.bind_tools(tools), p.name) for p in capable
        ]
        if len(runnables) == 1:
            return runnables[0]
        return runnables[0].with_fallbacks(runnables[1:])

    def _wrap_branch(self, runnable, provider_name: str) -> RunnableLambda:
        """One wait-and-retry on rate limits; classify everything else.

        Non-rate errors are classified and raised immediately (failover
        happens at the fallbacks layer -- waiting cannot fix them). A rate
        limit gets ONE wait honoring the provider's Retry-After hint; a
        second failure is raised in our taxonomy so the next provider in
        the chain takes over -- the same bounded policy as generate().
        """

        def _invoke(messages):
            try:
                return runnable.invoke(messages)
            except Exception as first:  # noqa: BLE001 -- classify at boundary
                classified = (
                    first if isinstance(first, LLMProviderError)
                    else _classify(first, provider_name)
                )
                if not isinstance(classified, LLMRateLimitError):
                    # langchain's fallbacks wrapper swallows non-first
                    # branch errors entirely (no logging in fallbacks.py):
                    # log HERE or a fallback's cause is invisible.
                    logger.warning(
                        "tool chain %s failed (%s)",
                        provider_name,
                        type(classified).__name__,
                    )
                    if classified is first:
                        raise  # already ours: preserve the exact subtype
                    raise classified from first
                waited = (
                    parse_retry_after(str(classified))
                    or self._rate_limit_wait_seconds
                )
                logger.warning(
                    "tool chain %s rate limited; waiting %.1fs once "
                    "before retrying",
                    provider_name, waited,
                )
                self._sleep(waited)
            try:
                return runnable.invoke(messages)  # one retry, same branch
            except Exception as second:  # noqa: BLE001
                if isinstance(second, LLMProviderError):
                    logger.warning(
                        "tool chain %s retry failed (%s)",
                        provider_name,
                        type(second).__name__,
                    )
                    raise  # one retry only; our taxonomy, next provider up
                classified = _classify(second, provider_name)
                logger.warning(
                    "tool chain %s retry failed (%s)",
                    provider_name,
                    type(classified).__name__,
                )
                raise classified from second

        return RunnableLambda(_invoke)

    def close(self) -> None:
        for provider in self._providers:
            try:
                provider.close()
            except Exception:  # noqa: BLE001 -- closing must never mask results
                pass

    def __enter__(self) -> "LLMManager":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def build_manager(settings: Settings | None = None) -> LLMManager:
    """Settings -> provider chain -> manager (offline; no key/network checks).

    Credential presence is checked here, reachability is NOT -- probing keys
    would cost a network call per build, and an unreachable provider is
    indistinguishable from a failed one at call time anyway (it fails over).
    """
    settings = settings or get_settings()
    return LLMManager(
        build_providers(settings),
        rate_limit_wait_seconds=settings.llm_rate_limit_wait_seconds,
    )
