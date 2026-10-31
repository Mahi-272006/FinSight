"""LLM manager: ordered failover across providers behind one call.

POLICY (all deliberate, all testable):

- **Chain order = priority order.** `llm_provider_chain` is walked in order;
  the first provider that answers wins and later ones are never called.
- **Every provider failure fails over** -- rate limits, auth errors,
  timeouts, connection refusals, malformed responses, even unexpected
  exceptions (recorded with their class name). Rationale: text generation has
  no side effects, so trying the next provider is always safe, and a free
  tier that is down should not take the product down.
- **No in-provider retries in M5.** Failover is the retry strategy: SDK
  backoff would hide rate limits and make timing nondeterministic. Cost of
  the choice: a Groq 429 immediately spends Gemini quota instead of waiting
  seconds. Accepted for now (both are free), revisited in hardening.
- **All failures aggregate.** If the whole chain fails, `LLMAllProvidersFailed`
  carries (provider, cause) for EVERY attempt -- one readable error instead
  of "the last thing we tried failed", so misconfiguration (all auth errors)
  and outages (all timeouts) are distinguishable at a glance.
- **Configuration errors are not failover-able.** LLMConfigurationError
  propagates immediately; trying the next provider cannot fix a broken chain
  definition.
"""

from typing import Sequence

from langchain_core.messages import BaseMessage

from app.config import Settings, get_settings
from app.services.llm_providers import (
    LLMAllProvidersFailed,
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    build_providers,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


class LLMManager:
    """One `generate()` over an ordered list of providers."""

    def __init__(self, providers: Sequence[LLMProvider]):
        if not providers:
            raise LLMConfigurationError(
                "LLMManager requires at least one provider (chain is empty "
                "after skipping unconfigured ones -- see build_providers)."
            )
        self._providers = list(providers)

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
            try:
                response = provider.generate(messages)
            except LLMConfigurationError:
                raise  # our bug/config -- failover cannot fix it
            except Exception as exc:  # noqa: BLE001 -- failover on everything else
                cause = str(exc) or type(exc).__name__
                failures.append((provider.name, cause))
                logger.warning(
                    "LLM provider %s failed (%s); failing over%s",
                    provider.name, cause,
                    "" if provider is not self._providers[-1] else " (last provider)",
                )
                continue
            logger.info(
                "LLM %s/%s answered in %.0f ms (in/out tokens: %s/%s)",
                response.provider, response.model, response.latency_ms,
                response.input_tokens, response.output_tokens,
            )
            return response
        raise LLMAllProvidersFailed(failures)

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
    return LLMManager(build_providers(settings))
