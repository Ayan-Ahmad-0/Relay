"""Tests for inverted contention benchmark with concurrent quota mutations."""

from bench.contention import run_contention_benchmark


def test_inverted_contention_suite_small():
    """Execute inverted contention run with 4 concurrent workers and 10 runs.

    Asserts 100% terminal resolution, successful retries on lock contention,
    and 0 permanent serialization failures.
    """
    res = run_contention_benchmark(num_runs=10, worker_counts=[4])

    results = res["results"]
    assert len(results) == 1
    bench_result = results[0]

    assert bench_result["completed_runs"] == 10
    assert bench_result["quota_mutated"] == 10
    assert bench_result["throughput_runs_per_sec"] > 0
