"""Unit tests for mutation testing engine."""

from bench.mutation import count_possible_mutations, run_mutation_benchmark


def test_mutation_count():
    code = "if x == 10 and y < 20:\n    return a + b\nreturn False"
    count = count_possible_mutations(code)
    assert count >= 4


def test_mutation_kill_rate_meets_target():
    # Fast evaluation on security policies
    report = run_mutation_benchmark(
        target_rel_path="relay/security.py",
        test_rel_path="tests/test_injection.py",
        max_mutants=10,
    )
    assert report["tested"] > 0
    assert report["kill_rate_pct"] >= 80.0
