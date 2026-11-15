from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.providers.llm import (
    CircuitBreaker,
    LLMClient,
    LLMOutputError,
    LLMUnavailableError,
    Usage,
    extract_json,
)


class Out(BaseModel):
    intent: str
    n: int


def _settings(**kw):
    base = dict(_env_file=None, groq_api_key="k", gemini_api_key="g", llm_rpm_limit=100)
    base.update(kw)
    return Settings(**base)


def _resp(text, pt=10, ct=5):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct),
    )


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Sure! {"a": 3} hope this helps') == {"a": 3}


def test_candidate_models_respects_keys_and_breaker():
    c = LLMClient(_settings(gemini_api_key=""))
    assert c.candidate_models("fast") == ["groq/llama-3.1-8b-instant"]
    c2 = LLMClient(_settings())
    assert c2.candidate_models("answer") == ["groq/openai/gpt-oss-120b", "gemini/gemini-2.0-flash"]
    for _ in range(3):
        c2.breaker.record_failure("groq/openai/gpt-oss-120b")
    assert c2.candidate_models("answer") == ["gemini/gemini-2.0-flash"]


def test_circuit_breaker_cooldown():
    b = CircuitBreaker(threshold=1, cooldown=0)
    b.record_failure("m")
    assert b.is_open("m") is False  # cooldown 0 → half-open immediately


async def test_complete_json_retries_on_invalid(monkeypatch):
    c = LLMClient(_settings())
    replies = iter(["not json", '{"intent": "x"}', '{"intent": "x", "n": 2}'])

    async def fake(model, **kw):
        return _resp(next(replies))

    monkeypatch.setattr(c, "_acompletion", fake)
    usage = Usage()
    out = await c.complete_json([{"role": "user", "content": "hi"}], Out, usage=usage)
    assert out == Out(intent="x", n=2)
    assert len(usage.calls) == 3 and usage.total_tokens == 45


async def test_complete_json_gives_up(monkeypatch):
    c = LLMClient(_settings())

    async def fake(model, **kw):
        return _resp("nope")

    monkeypatch.setattr(c, "_acompletion", fake)
    with pytest.raises(LLMOutputError):
        await c.complete_json([{"role": "user", "content": "hi"}], Out, max_attempts=2)


async def test_fallback_model_used_on_failure(monkeypatch):
    c = LLMClient(_settings(llm_max_retries=1))
    seen = []

    async def fake(model, **kw):
        seen.append(model)
        if model.startswith("groq"):
            raise ValueError("bad request")
        return _resp("ok")

    monkeypatch.setattr(c, "_acompletion", fake)
    assert await c.complete_text([{"role": "user", "content": "x"}], "fast") == "ok"
    assert seen == ["groq/llama-3.1-8b-instant", "gemini/gemini-2.0-flash"]


async def test_disabled_without_keys():
    c = LLMClient(
        Settings(
            _env_file=None,
            groq_api_key="",
            gemini_api_key="",
            anthropic_api_key="",
            openai_api_key="",
        )
    )
    with pytest.raises(LLMUnavailableError):
        await c.complete([{"role": "user", "content": "x"}])
