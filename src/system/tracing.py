from __future__ import annotations

import os
from functools import lru_cache

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _OTEL_AVAILABLE = True
except ModuleNotFoundError:
    trace = None  # type: ignore
    OTLPSpanExporter = None  # type: ignore
    Resource = None  # type: ignore
    TracerProvider = None  # type: ignore
    BatchSpanProcessor = None  # type: ignore
    _OTEL_AVAILABLE = False


class _NoopSpan:
    def set_attribute(self, *_args, **_kwargs) -> None:  # pragma: no cover - trivial
        return None

    def add_event(self, *_args, **_kwargs) -> None:  # pragma: no cover - trivial
        return None

    def record_exception(self, *_args, **_kwargs) -> None:  # pragma: no cover - trivial
        return None

    def end(self) -> None:  # pragma: no cover - trivial
        return None

    def __enter__(self):  # pragma: no cover - trivial
        return self

    def __exit__(self, *_exc_info):  # pragma: no cover - trivial
        return False


class _NoopTracer:
    def start_as_current_span(self, *_args, **_kwargs):  # pragma: no cover - trivial
        return _NoopSpan()

    def start_span(self, *_args, **_kwargs):  # pragma: no cover - trivial
        return _NoopSpan()


@lru_cache(maxsize=1)
def _configure_tracing(service_name: str) -> None:
    if not _OTEL_AVAILABLE:
        return
    # Only configure OTLP exporter if endpoint is explicitly provided
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv(
        "OTEL_EXPORTER_OTLP_HTTP_ENDPOINT"
    )
    if not endpoint:
        # No collector endpoint specified, do not initialize exporter
        return
    resource = Resource.create({"service.name": service_name})

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)


def init_tracing(service_name: str = "fenix-trading-bot") -> None:
    """Initialize global tracer provider and OTLP exporter (idempotent)."""
    _configure_tracing(service_name)


def get_tracer(name: str | None = None):
    """Return a tracer, ensuring the provider is initialized."""
    init_tracing()
    if not _OTEL_AVAILABLE or trace is None:
        return _NoopTracer()
    return trace.get_tracer(name or "fenix-trading-bot")
