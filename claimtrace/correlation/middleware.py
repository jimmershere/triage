"""Correlation context binding and fail-closed outbound validation."""

from __future__ import annotations

from contextlib import ContextDecorator
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, Callable, Mapping

from .segments import extract_trace
from .transport import TraceContext, from_headers, read_envelope


current_trace_context: ContextVar[TraceContext | None] = ContextVar(
    "claimtrace_context",
    default=None,
)


class CorrelationError(RuntimeError):
    pass


def _resolve_context(
    *,
    ctx: TraceContext | None = None,
    headers: Mapping[str, str] | None = None,
    envelope_path: str | None = None,
    x12_text: str | None = None,
) -> TraceContext:
    if ctx is not None:
        return ctx
    if headers:
        return from_headers(headers)
    if envelope_path:
        return read_envelope(envelope_path)
    if x12_text:
        return extract_trace(x12_text)
    raise CorrelationError("no correlation context provided")


class CorrelationScope(ContextDecorator):
    def __init__(self, **kwargs: Any) -> None:
        self.ctx = _resolve_context(**kwargs)
        self._token: Token[TraceContext | None] | None = None

    def __enter__(self) -> TraceContext:
        self._token = current_trace_context.set(self.ctx)
        return self.ctx

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._token is not None:
            current_trace_context.reset(self._token)
        return False


def with_correlation(func: Callable[..., Any] | None = None, **kwargs: Any):
    if func is None:
        return CorrelationScope(**kwargs)

    @wraps(func)
    def wrapper(*args: Any, **inner_kwargs: Any) -> Any:
        ctx = inner_kwargs.pop("trace_context", None)
        with CorrelationScope(ctx=ctx or current_trace_context.get()):
            return func(*args, **inner_kwargs)

    return wrapper


def require_outbound_context(headers: Mapping[str, str] | None = None) -> None:
    ctx = current_trace_context.get()
    if ctx is None:
        raise CorrelationError("outbound operation has no active claim correlation context")
    headers = headers or {}
    required = {"X-Claim-ID", "X-Trace-ID", "X-State-Hash"}
    missing = sorted(header for header in required if not headers.get(header))
    if missing:
        raise CorrelationError(f"outbound operation missing correlation headers: {', '.join(missing)}")
