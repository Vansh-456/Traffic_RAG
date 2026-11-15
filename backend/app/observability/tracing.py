"""Langfuse tracing wrapper (no-op when LANGFUSE_* keys are absent)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.observability.logging import get_logger, request_id_var

log = get_logger(__name__)


@lru_cache
def _client() -> Any | None:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as exc:
        log.warning("langfuse init failed", extra={"err": str(exc)[:200]})
        return None


class Trace:
    """One trace per chat request; spans per pipeline stage."""

    def __init__(self, name: str, input: str) -> None:
        self._trace = None
        client = _client()
        if client is not None:
            try:
                self._trace = client.trace(name=name, input=input, metadata={"request_id": request_id_var.get()})
            except Exception as exc:
                log.warning("langfuse trace failed", extra={"err": str(exc)[:200]})

    def span(self, name: str, output: Any) -> None:
        if self._trace is None:
            return
        try:
            self._trace.span(name=name, output=output)
        except Exception:
            pass

    def finish(self, output: str, usage: Any = None) -> str | None:
        if self._trace is None:
            return None
        try:
            for call in getattr(usage, "calls", []):
                self._trace.generation(
                    name=call["purpose"], model=call["model"],
                    usage={"input": call["prompt_tokens"], "output": call["completion_tokens"]},
                    metadata={"latency_ms": call["latency_ms"]},
                )
            self._trace.update(output=output)
            url = self._trace.get_trace_url()
            client = _client()
            if client is not None:
                client.flush()
            return str(url)
        except Exception as exc:
            log.warning("langfuse finish failed", extra={"err": str(exc)[:200]})
            return None
