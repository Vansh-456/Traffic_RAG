"""Provider-agnostic LLM client built on LiteLLM.

Features:
- two roles: ``answer`` (strong) and ``fast`` (cheap) models, plus a fallback
- per-model RPM limiting (Groq free tier) shared across processes via Redis
- retries with exponential backoff on transient errors
- circuit breaker: after repeated failures a model is skipped for a cool-down
  and the fallback model is used
- ``complete_json``: JSON output validated against a Pydantic schema, with
  re-prompting on invalid JSON / schema violations
- streaming for answer generation
- usage accounting (tokens, estimated cost) for the RAG internals panel
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import Settings, get_settings
from app.observability.logging import get_logger
from app.observability.metrics import LLM_CALLS, LLM_TOKENS
from app.providers.ratelimit import RateLimiter, TokenBudget

log = get_logger(__name__)

Role = Literal["answer", "fast", "verify", "route"]
T = TypeVar("T", bound=BaseModel)

# Rough USD per 1M tokens (input, output) for cost estimates in internals.
PRICING: dict[str, tuple[float, float]] = {
    "groq/openai/gpt-oss-120b": (0.15, 0.75),
    "groq/openai/gpt-oss-20b": (0.075, 0.30),
    "gemini/gemini-3.5-flash": (0.30, 2.50),
}


class LLMUnavailableError(RuntimeError):
    """No LLM provider is configured or all models failed."""


class LLMOutputError(RuntimeError):
    """The model did not produce valid structured output after retries."""


@dataclass
class Usage:
    """Accumulated usage for one request/trace."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def add(self, model: str, purpose: str, prompt: int, completion: int, ms: float) -> None:
        price_in, price_out = PRICING.get(model, (0.0, 0.0))
        cost = (prompt * price_in + completion * price_out) / 1_000_000
        self.calls.append(
            {
                "model": model,
                "purpose": purpose,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "latency_ms": round(ms, 1),
                "cost_usd": round(cost, 6),
            }
        )

    @property
    def total_tokens(self) -> int:
        return sum(c["prompt_tokens"] + c["completion_tokens"] for c in self.calls)

    @property
    def total_cost(self) -> float:
        return round(sum(c["cost_usd"] for c in self.calls), 6)


class CircuitBreaker:
    """Opens after `threshold` consecutive failures for `cooldown` seconds."""

    def __init__(self, threshold: int = 3, cooldown: float = 60.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, model: str) -> bool:
        opened = self._opened_at.get(model)
        if opened is None:
            return False
        if time.monotonic() - opened >= self.cooldown:
            self._opened_at.pop(model, None)
            self._failures[model] = 0
            return False
        return True

    def record_success(self, model: str) -> None:
        self._failures[model] = 0
        self._opened_at.pop(model, None)

    def record_failure(self, model: str) -> None:
        self._failures[model] = self._failures.get(model, 0) + 1
        if self._failures[model] >= self.threshold:
            self._opened_at[model] = time.monotonic()
            log.warning("circuit opened for model", extra={"model": model})


def _is_rate_limit(exc: BaseException) -> bool:
    """429s, including ones LiteLLM wraps (e.g. MidStreamFallbackError)."""
    text = f"{type(exc).__name__} {exc}"
    return "RateLimit" in text or "429" in text or "RESOURCE_EXHAUSTED" in text


def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__
    transient = (
        "Timeout",
        "APIConnectionError",
        "ServiceUnavailableError",
        "InternalServerError",
        "APIError",
        "TimeoutError",
    )
    return any(t in name for t in transient)


_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating code fences / prose around it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


class LLMClient:
    """High-level async LLM client."""

    def __init__(
        self, settings: Settings | None = None, limiter: RateLimiter | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.limiter = limiter or RateLimiter(self.settings.llm_rpm_limit)
        self.breaker = CircuitBreaker()
        self.tpm = TokenBudget(self.settings.llm_tpm_limit)
        # background clients (worker, CLI ingestion) yield RPM headroom to interactive chat
        self.background = False
        self._push_keys_to_env()

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    def _push_keys_to_env(self) -> None:
        """LiteLLM reads provider keys from env vars; mirror settings into env."""
        import os

        mapping = {
            "GROQ_API_KEY": self.settings.groq_api_key,
            "GEMINI_API_KEY": self.settings.gemini_api_key,
            "ANTHROPIC_API_KEY": self.settings.anthropic_api_key,
            "OPENAI_API_KEY": self.settings.openai_api_key,
        }
        for key, value in mapping.items():
            if value and not os.environ.get(key):
                os.environ[key] = value

    def model_for(self, role: Role) -> str:
        if role == "answer":
            return self.settings.llm_model_answer
        if role in ("verify", "route"):
            return self.settings.llm_model_verify
        return self.settings.llm_model_fast

    def _key_available(self, model: str) -> bool:
        prefix = model.split("/", 1)[0]
        s = self.settings
        return {
            "groq": bool(s.groq_api_key),
            "gemini": bool(s.gemini_api_key),
            "anthropic": bool(s.anthropic_api_key),
            "openai": bool(s.openai_api_key),
            "ollama": True,
        }.get(prefix, True)

    def candidate_models(self, role: Role) -> list[str]:
        # each Groq model has its own token quota, so fall back across them before Gemini
        s = self.settings
        chain = {
            "answer": [s.llm_model_answer, s.llm_model_fast],
            "fast": [s.llm_model_fast, s.llm_model_verify],
            "verify": [s.llm_model_verify, s.llm_model_fast],
            "route": [s.llm_model_verify, s.llm_model_fast],
        }[role]
        models = list(dict.fromkeys([*chain, s.llm_model_fallback]))
        usable = [m for m in models if self._key_available(m) and not self.breaker.is_open(m)]
        if not usable:
            # all circuits open: try anyway with keys available (half-open)
            usable = [m for m in models if self._key_available(m)]
        return usable

    def _extra_kwargs(self, model: str) -> dict[str, Any]:
        if model.startswith("ollama"):
            return {"api_base": self.settings.ollama_base_url}
        if "gpt-oss" in model:
            # reasoning models: keep hidden reasoning short (latency + token budget)
            return {"extra_body": {"reasoning_effort": self.settings.llm_reasoning_effort}}
        return {}

    @staticmethod
    def _estimate_tokens(kwargs: dict[str, Any]) -> int:
        """Groq counts prompt + max_tokens against TPM; estimate both (~4 chars/token)."""
        chars = sum(len(str(m.get("content") or "")) for m in kwargs.get("messages", []))
        if kwargs.get("tools"):
            chars += len(json.dumps(kwargs["tools"]))
        return chars // 4 + int(kwargs.get("max_tokens") or 512)

    async def _wait_for(self, model: str, tokens: int) -> float:
        if not model.startswith("groq/"):  # no token accounting; only provider 429 cooldowns
            return await self.tpm.wait_seconds(model, 0)
        # background traffic may only use half of each model's budget
        share = self.settings.llm_tpm_limit // 2 if self.background else None
        return await self.tpm.wait_seconds(model, tokens, limit=share)

    async def has_budget(self, role: Role, tokens: int) -> bool:
        """True if the role's primary model could serve a call of `tokens` right now."""
        return await self._wait_for(self.model_for(role), tokens) == 0

    async def _models_to_try(self, role: Role, tokens: int) -> AsyncIterator[str]:
        """Yield models ready to call (budget reserved), in this order:

        1. Groq candidates that are free within LLM_MAX_QUEUE_SECONDS (each Groq
           model has its own token quota);
        2. a short wait (<= 8 s) for the soonest Groq model, which beats a slow or
           rate-limited fallback on free tiers;
        3. non-Groq fallbacks (Gemini), respecting their 429 cooldowns;
        4. last resort: wait up to 30 s for the soonest Groq model.
        """
        candidates = self.candidate_models(role)
        groq = [m for m in candidates if m.startswith("groq/")]
        others = [m for m in candidates if not m.startswith("groq/")]
        threshold = 60.0 if self.background else self.settings.llm_max_queue_seconds

        tried: set[str] = set()

        async def soonest(limit: float) -> str | None:
            # only models skipped for budget reasons, never ones that already failed
            pool = [m for m in groq if m not in tried]
            if not pool:
                return None
            wait, model = min([(await self._wait_for(m, tokens), m) for m in pool])
            if wait > limit:
                return None
            log.info(
                "waiting for the soonest model", extra={"model": model, "wait_s": round(wait, 1)}
            )
            await asyncio.sleep(wait)
            await self.tpm.reserve(model, tokens)
            tried.add(model)
            return model

        for model in groq:
            wait = await self._wait_for(model, tokens)
            if wait > threshold:
                log.info(
                    "model saturated; trying next", extra={"model": model, "wait_s": round(wait, 1)}
                )
                continue
            if wait > 0:
                await asyncio.sleep(wait)
            await self.tpm.reserve(model, tokens)
            tried.add(model)
            yield model
        if picked := await soonest(8.0):
            yield picked
        for model in others:
            if await self._wait_for(model, tokens) <= threshold:
                yield model
        if picked := await soonest(30.0):
            yield picked

    async def _on_rate_limit(self, model: str, exc: BaseException) -> None:
        """Honour the provider's 'try again in Ns' hint as a cooldown (not a breaker failure)."""
        m = re.search(r"try again in ([0-9.]+)\s*(ms|s)", str(exc))
        seconds = float(m.group(1)) / (1000 if m and m.group(2) == "ms" else 1) if m else 10.0
        await self.tpm.block(model, seconds)

    async def _acompletion(self, model: str, **kwargs: Any) -> Any:
        import litellm

        limit = self.settings.llm_rpm_limit_background if self.background else None
        await self.limiter.acquire(model, max_wait=300.0 if self.background else 90.0, limit=limit)
        if model.startswith("gemini/") and kwargs.get("max_tokens"):
            # Gemini 3.x "thinking" tokens count against max_tokens; leave room for the answer
            kwargs["max_tokens"] = max(int(kwargs["max_tokens"]) * 3, 2048)
        return await litellm.acompletion(
            model=model,
            timeout=self.settings.llm_timeout_seconds,
            **self._extra_kwargs(model),
            **kwargs,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        role: Role = "fast",
        *,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        json_mode: bool = False,
        usage: Usage | None = None,
        purpose: str = "completion",
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Non-streaming completion. Returns the LiteLLM message object."""
        if not self.enabled:
            raise LLMUnavailableError("no LLM provider configured")
        last_exc: BaseException | None = None
        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        async for model in self._models_to_try(role, self._estimate_tokens(kwargs)):
            started = time.perf_counter()
            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(self.settings.llm_max_retries),
                    wait=wait_exponential_jitter(initial=1, max=20),
                    retry=retry_if_exception(_is_transient),
                    reraise=True,
                ):
                    with attempt:
                        resp = await self._acompletion(model, **kwargs)
                self.breaker.record_success(model)
                LLM_CALLS.labels(model, "ok").inc()
                self._account(resp, model, purpose, started, usage)
                return resp.choices[0].message
            except Exception as exc:
                last_exc = exc
                if _is_rate_limit(exc):
                    await self._on_rate_limit(model, exc)
                else:
                    self.breaker.record_failure(model)
                LLM_CALLS.labels(model, "error").inc()
                log.warning(
                    "llm call failed; trying fallback",
                    extra={"model": model, "err": f"{type(exc).__name__}: {exc}"[:300]},
                )
        raise LLMUnavailableError(f"all models failed: {last_exc}") from last_exc

    async def complete_text(
        self, messages: list[dict[str, Any]], role: Role = "fast", **kwargs: Any
    ) -> str:
        msg = await self.complete(messages, role, **kwargs)
        return str(msg.content or "")

    async def complete_json(
        self,
        messages: list[dict[str, Any]],
        schema: type[T],
        role: Role = "fast",
        *,
        max_attempts: int = 3,
        usage: Usage | None = None,
        purpose: str = "json",
        max_tokens: int = 1500,
    ) -> T:
        """Return a validated `schema` instance; re-prompts with the validation error."""
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        convo = [
            *messages,
            {
                "role": "system",
                "content": (
                    "Respond with a single JSON object only (no prose, no code fences) "
                    f"that validates against this JSON Schema:\n{schema_json}"
                ),
            },
        ]
        last_err = ""
        for attempt in range(max_attempts):
            text = await self.complete_text(
                convo,
                role,
                json_mode=True,
                temperature=0.0 if attempt else 0.1,
                usage=usage,
                purpose=purpose,
                max_tokens=max_tokens,
            )
            try:
                return schema.model_validate(extract_json(text))
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_err = str(exc)[:800]
                log.info("invalid structured output; retrying", extra={"attempt": attempt + 1})
                convo = [
                    *convo,
                    {"role": "assistant", "content": text[:4000]},
                    {
                        "role": "user",
                        "content": f"That output was invalid: {last_err}\nReturn corrected JSON only.",
                    },
                ]
        raise LLMOutputError(f"invalid JSON after {max_attempts} attempts: {last_err}")

    async def stream(
        self,
        messages: list[dict[str, Any]],
        role: Role = "answer",
        *,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        usage: Usage | None = None,
        purpose: str = "generation",
    ) -> AsyncIterator[str]:
        """Stream text deltas, falling back to the next model if the stream fails to start."""
        if not self.enabled:
            raise LLMUnavailableError("no LLM provider configured")
        last_exc: BaseException | None = None
        est = self._estimate_tokens({"messages": messages, "max_tokens": max_tokens})
        async for model in self._models_to_try(role, est):
            started = time.perf_counter()
            emitted = False
            prompt_tokens = completion_tokens = 0
            streamed_chars = 0
            # LiteLLM's Groq stream parser crashes on the usage-only final chunk,
            # so only request it from other providers; estimate usage otherwise.
            extra = {} if model.startswith("groq/") else {"stream_options": {"include_usage": True}}
            try:
                resp = await self._acompletion(
                    model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    **extra,
                )
                async for chunk in resp:
                    u = getattr(chunk, "usage", None)
                    if u:
                        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
                        completion_tokens = getattr(u, "completion_tokens", 0) or 0
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    text = getattr(delta, "content", None)
                    if text:
                        emitted = True
                        streamed_chars += len(text)
                        yield text
                if not completion_tokens:
                    prompt_tokens = sum(len(str(m.get("content") or "")) for m in messages) // 4
                    completion_tokens = streamed_chars // 4
                self.breaker.record_success(model)
                LLM_CALLS.labels(model, "ok").inc()
                if usage is not None:
                    usage.add(
                        model,
                        purpose,
                        prompt_tokens,
                        completion_tokens,
                        (time.perf_counter() - started) * 1000,
                    )
                LLM_TOKENS.labels(model, "prompt").inc(prompt_tokens)
                LLM_TOKENS.labels(model, "completion").inc(completion_tokens)
                return
            except Exception as exc:
                last_exc = exc
                if _is_rate_limit(exc):
                    await self._on_rate_limit(model, exc)
                else:
                    self.breaker.record_failure(model)
                LLM_CALLS.labels(model, "error").inc()
                log.warning("llm stream failed", extra={"model": model, "err": str(exc)[:300]})
                if emitted:
                    raise LLMUnavailableError(f"stream interrupted: {exc}") from exc
        raise LLMUnavailableError(f"all models failed: {last_exc}") from last_exc

    def _account(
        self, resp: Any, model: str, purpose: str, started: float, usage: Usage | None
    ) -> None:
        u = getattr(resp, "usage", None)
        prompt = int(getattr(u, "prompt_tokens", 0) or 0)
        completion = int(getattr(u, "completion_tokens", 0) or 0)
        LLM_TOKENS.labels(model, "prompt").inc(prompt)
        LLM_TOKENS.labels(model, "completion").inc(completion)
        if usage is not None:
            usage.add(model, purpose, prompt, completion, (time.perf_counter() - started) * 1000)


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """Process-wide client (limiter attaches to Redis lazily via `attach_redis`)."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def attach_redis(redis: Any) -> None:
    """Share the RPM window across processes once Redis is connected."""
    get_llm().limiter.redis = redis
    get_llm().tpm.redis = redis
