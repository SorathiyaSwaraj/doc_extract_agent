import threading

import pytest

from doc_extract.observability.metrics import Registry, ServiceMetrics
from doc_extract.service.pipeline import Call, Outcome


def test_counter_renders_in_prometheus_format():
    registry = Registry()
    requests = registry.counter("http_total", "Requests.", ("route", "status"))

    requests.inc(route="/v1/documents", status="202")
    requests.inc(2, route="/v1/documents", status="202")
    requests.inc(route='/a"b', status="401")

    assert registry.render().splitlines() == [
        "# HELP http_total Requests.",
        "# TYPE http_total counter",
        'http_total{route="/a\\"b",status="401"} 1',
        'http_total{route="/v1/documents",status="202"} 3',
    ]


def test_histogram_buckets_are_cumulative():
    registry = Registry()
    latency = registry.histogram("seconds", "Latency.", buckets=(1, 5))

    for value in (0.5, 2, 10):
        latency.observe(value)

    lines = registry.render().splitlines()
    assert 'seconds_bucket{le="1"} 1' in lines
    assert 'seconds_bucket{le="5"} 2' in lines
    assert 'seconds_bucket{le="+Inf"} 3' in lines
    assert "seconds_sum 12.5" in lines and "seconds_count 3" in lines


def test_gauge_reads_at_scrape_time():
    registry = Registry()
    depth = {"queued": 2}
    registry.gauge("docs", "Docs.", lambda: {(k,): v for k, v in depth.items()}, ("status",))

    depth["queued"] = 5

    assert 'docs{status="queued"} 5' in registry.render()


def test_wrong_labels_and_negative_counts_are_refused():
    registry = Registry()
    counter = registry.counter("c", "C.", ("a",))

    with pytest.raises(ValueError):
        counter.inc(b="1")
    with pytest.raises(ValueError):
        counter.inc(-1, a="1")
    with pytest.raises(ValueError):
        registry.counter("c", "again")


def test_counter_is_thread_safe():
    counter = Registry().counter("c", "C.")

    def add():
        for _ in range(10_000):
            counter.inc()

    threads = [threading.Thread(target=add) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counter.value() == 80_000


def test_outcome_feeds_tokens_cost_and_stages():
    metrics = ServiceMetrics()
    outcome = Outcome("d", "v", None, "approve", 0.98, [], [], [])
    outcome.calls = [Call("primary", "vision", "gemini", "g", 1300, 700, 4.0, cost_usd=0.0004)]
    outcome.stages = {"primary": 4.2}

    metrics.observe_outcome(outcome)

    text = metrics.registry.render()
    assert 'doc_extract_documents_processed_total{decision="approve"} 1' in text
    assert 'doc_extract_llm_tokens_total{direction="input",model="g"} 1300' not in text
    assert 'doc_extract_llm_tokens_total{model="g",direction="input"} 1300' in text
    assert 'doc_extract_llm_cost_usd_total{model="g"} 0.0004' in text
    assert 'doc_extract_stage_seconds_count{stage="primary"} 1' in text
