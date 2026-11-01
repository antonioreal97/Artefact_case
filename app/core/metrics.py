"""Tiny in-process metrics registry with Prometheus text exposition (format 0.0.4).

Exposure decision: ``GET /metrics`` is **unauthenticated** (standard Prometheus scrape) and
therefore carries only tenant-free aggregates: label values are route templates, HTTP
status codes, channels, handoff reasons and error class names - never tenant ids, user
references, message text or raw paths. Restrict it at the network layer in production.

Counters are per-process (reset on restart; one series set per worker process).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)

LabelValues = tuple[str, ...]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(names: Sequence[str], values: Sequence[str], extra: str = "") -> str:
    parts = [f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True)]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


def _format_value(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return repr(float(value)) if not float(value).is_integer() else str(int(value))


class _Metric:
    kind = ""

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str]) -> LabelValues:
        if set(labels) != set(self.labelnames):
            raise ValueError(f"{self.name}: expected labels {self.labelnames}")
        return tuple(str(labels[n]) for n in self.labelnames)

    def header(self) -> list[str]:
        return [f"# HELP {self.name} {self.documentation}", f"# TYPE {self.name} {self.kind}"]

    def samples(self) -> list[str]:
        raise NotImplementedError


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, documentation, labelnames)
        self._values: dict[LabelValues, float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("counters only increase")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        with self._lock:
            return self._values.get(self._key(labels), 0.0)

    def samples(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        return [
            f"{self.name}{_format_labels(self.labelnames, k)} {_format_value(v)}" for k, v in items
        ]


@dataclass
class _HistogramSeries:
    bucket_counts: list[int]
    total: int = 0
    sum: float = 0.0


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_LATENCY_BUCKETS,
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self.buckets = tuple(sorted(buckets))
        self._series: dict[LabelValues, _HistogramSeries] = {}

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            series = self._series.setdefault(key, _HistogramSeries([0] * len(self.buckets)))
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    series.bucket_counts[i] += 1
            series.total += 1
            series.sum += value

    def count(self, **labels: str) -> int:
        with self._lock:
            series = self._series.get(self._key(labels))
            return series.total if series else 0

    def samples(self) -> list[str]:
        lines: list[str] = []
        with self._lock:
            for key, series in sorted(self._series.items()):
                for bound, cumulative in zip(self.buckets, series.bucket_counts, strict=True):
                    labels = _format_labels(self.labelnames, key, f'le="{_format_value(bound)}"')
                    lines.append(f"{self.name}_bucket{labels} {cumulative}")
                inf_labels = _format_labels(self.labelnames, key, 'le="+Inf"')
                lines.append(f"{self.name}_bucket{inf_labels} {series.total}")
                base = _format_labels(self.labelnames, key)
                lines.append(f"{self.name}_sum{base} {_format_value(series.sum)}")
                lines.append(f"{self.name}_count{base} {series.total}")
        return lines


class Registry:
    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}

    def register[M: _Metric](self, metric: M) -> M:
        if metric.name in self._metrics:
            raise ValueError(f"duplicate metric {metric.name}")
        self._metrics[metric.name] = metric
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for metric in self._metrics.values():
            lines.extend(metric.header())
            lines.extend(metric.samples())
        return "\n".join(lines) + "\n"


REGISTRY = Registry()

HTTP_REQUESTS = REGISTRY.register(
    Counter(
        "http_requests_total",
        "HTTP requests by method, route template and status code.",
        ("method", "route", "status"),
    )
)
HTTP_LATENCY = REGISTRY.register(
    Histogram(
        "http_request_duration_seconds",
        "HTTP request latency by method and route template.",
        ("method", "route"),
    )
)
CHAT_TURNS = REGISTRY.register(
    Counter("chat_turns_total", "Completed chat turns by channel.", ("channel",))
)
HANDOFFS = REGISTRY.register(
    Counter(
        "handoffs_total",
        "Human handoffs by reason and origin (agent turn or explicit API call).",
        ("reason", "origin"),
    )
)
RETRIEVAL = REGISTRY.register(
    Counter(
        "retrieval_results_total",
        "Chat turns by knowledge retrieval outcome (hit = >=1 chunk retrieved; "
        "insufficient = no_information/low_confidence handoff).",
        ("result",),
    )
)
TOOL_ERRORS = REGISTRY.register(
    Counter("tool_errors_total", "Tool calls that returned an error, by channel.", ("channel",))
)
MODEL_ERRORS = REGISTRY.register(
    Counter(
        "model_errors_total",
        "Chat turns that failed with an unexpected error (model/provider/agent), by class.",
        ("error_type",),
    )
)
AUTH_FAILURES = REGISTRY.register(
    Counter("auth_failures_total", "Rejected API-key authentications by reason.", ("reason",))
)
WHATSAPP_MESSAGES = REGISTRY.register(
    Counter(
        "whatsapp_messages_total",
        "Inbound WhatsApp messages by outcome.",
        ("outcome",),
    )
)
