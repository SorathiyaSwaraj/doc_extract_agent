"""Counters, gauges, and histograms in the Prometheus text format, with no dependency.

About a hundred lines replace a client library here, and they show exactly what a
scrape returns. Two rules keep metrics cheap and safe:

- Label values come from small fixed sets: a route template, never a URL with a
  document id in it; a model name, never a file name. Every distinct label set is
  a separate time series, and an id in a label makes a series per document.
- Nothing about document content is ever a label or a value.
"""

import threading
from collections.abc import Callable, Iterable, Sequence

LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)


class _Metric:
    kind = ""

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        self.name, self.help, self.labels = name, help_text, tuple(labels)
        self._lock = threading.Lock()

    def _key(self, values: dict[str, str]) -> tuple[str, ...]:
        if set(values) != set(self.labels):
            raise ValueError(f"{self.name} takes labels {self.labels}, got {tuple(values)}")
        return tuple(str(values[name]) for name in self.labels)

    def _labels(self, key: tuple[str, ...], extra: dict[str, str] | None = None) -> str:
        pairs = list(zip(self.labels, key, strict=True)) + list((extra or {}).items())
        if not pairs:
            return ""
        return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in pairs) + "}"

    def render(self) -> Iterable[str]:
        yield f"# HELP {self.name} {self.help}"
        yield f"# TYPE {self.name} {self.kind}"


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1, **labels: str) -> None:
        if amount < 0:
            raise ValueError("counters only go up")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + amount

    def value(self, **labels: str) -> float:
        return self._values.get(self._key(labels), 0)

    def render(self) -> Iterable[str]:
        yield from super().render()
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            yield f"{self.name}{self._labels(key)} {_number(value)}"


class Gauge(_Metric):
    """A value read at scrape time, such as the queue length from the database."""

    kind = "gauge"

    def __init__(
        self,
        name: str,
        help_text: str,
        read: Callable[[], dict[tuple[str, ...], float]],
        labels: Sequence[str] = (),
    ) -> None:
        super().__init__(name, help_text, labels)
        self._read = read

    def render(self) -> Iterable[str]:
        yield from super().render()
        for key, value in sorted(self._read().items()):
            yield f"{self.name}{self._labels(key)} {_number(value)}"


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: Sequence[str] = (),
        buckets: Sequence[float] = LATENCY_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, labels)
        self.buckets = tuple(sorted(buckets))
        self._series: dict[tuple[str, ...], list[float]] = {}  # bucket counts, sum, count

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            series = self._series.setdefault(key, [0.0] * (len(self.buckets) + 2))
            for index, bound in enumerate(self.buckets):
                if value <= bound:
                    series[index] += 1
            series[-2] += value
            series[-1] += 1

    def render(self) -> Iterable[str]:
        yield from super().render()
        with self._lock:
            items = sorted((key, list(series)) for key, series in self._series.items())
        for key, series in items:
            for bound, count in zip(self.buckets, series, strict=False):
                le = self._labels(key, {"le": _number(bound)})
                yield f"{self.name}_bucket{le} {_number(count)}"
            yield f"{self.name}_bucket{self._labels(key, {'le': '+Inf'})} {_number(series[-1])}"
            yield f"{self.name}_sum{self._labels(key)} {_number(series[-2])}"
            yield f"{self.name}_count{self._labels(key)} {_number(series[-1])}"


class Registry:
    def __init__(self) -> None:
        self._metrics: list[_Metric] = []

    def add(self, metric: _Metric) -> _Metric:
        if any(existing.name == metric.name for existing in self._metrics):
            raise ValueError(f"metric {metric.name} is already registered")
        self._metrics.append(metric)
        return metric

    def counter(self, name: str, help_text: str, labels: Sequence[str] = ()) -> Counter:
        return self.add(Counter(name, help_text, labels))

    def histogram(self, name: str, help_text: str, labels: Sequence[str] = (), **kw) -> Histogram:
        return self.add(Histogram(name, help_text, labels, **kw))

    def gauge(self, name: str, help_text: str, read, labels: Sequence[str] = ()) -> Gauge:
        return self.add(Gauge(name, help_text, read, labels))

    def render(self) -> str:
        return "\n".join(line for metric in self._metrics for line in metric.render()) + "\n"


class ServiceMetrics:
    """Every metric the service exports, in one place."""

    def __init__(self) -> None:
        self.registry = registry = Registry()
        self.requests = registry.counter(
            "doc_extract_http_requests_total", "HTTP requests.", ("method", "route", "status")
        )
        self.request_seconds = registry.histogram(
            "doc_extract_http_request_seconds", "HTTP request latency.", ("route",)
        )
        self.uploads = registry.counter("doc_extract_uploads_total", "Uploads accepted.", ("new",))
        self.processed = registry.counter(
            "doc_extract_documents_processed_total", "Documents decided.", ("decision",)
        )
        self.failures = registry.counter(
            "doc_extract_document_failures_total",
            "Processing attempts that failed.",
            ("kind",),  # transient or error
        )
        self.stage_seconds = registry.histogram(
            "doc_extract_stage_seconds", "Time per pipeline stage.", ("stage",)
        )
        self.tokens = registry.counter(
            "doc_extract_llm_tokens_total", "Model tokens used.", ("model", "direction")
        )
        self.cost = registry.counter(
            "doc_extract_llm_cost_usd_total", "Estimated model cost in USD.", ("model",)
        )
        self.reviews = registry.counter(
            "doc_extract_reviews_total", "Reviewer decisions.", ("action",)
        )
        self.corrections = registry.counter(
            "doc_extract_corrected_fields_total", "Fields changed by reviewers."
        )

    def observe_outcome(self, outcome) -> None:
        self.processed.inc(decision=outcome.decision)
        for stage, seconds in outcome.stages.items():
            self.stage_seconds.observe(seconds, stage=stage)
        for call in outcome.calls:
            model = call.model or "unknown"
            if call.input_tokens:
                self.tokens.inc(call.input_tokens, model=model, direction="input")
            if call.output_tokens:
                self.tokens.inc(call.output_tokens, model=model, direction="output")
            if call.cost_usd:
                self.cost.inc(call.cost_usd, model=model)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))
