"""Verification script for Phase 6 break-it sabotage experiments.

Tests that sabotaging each of the three foundational safety invariants:
  1. Fencing token validation (break-it-fencing)
  2. Nondeterminism replay guard (break-it-replay)
  3. Idempotency key generation (break-it-idempotency)
results in 100% test failure (turns RED), proving that Relay's safeguards
are actively enforced and not false passes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SABOTAGE_EXPERIMENTS = [
    {
        "name": "break-it-fencing",
        "description": "Sabotage fencing token validation in ExecutionRuntime._verify_fencing_token",
        "target_file": ROOT / "relay" / "runtime.py",
        "original": """        if row["claim_epoch"] != expected_epoch:
            raise FencingTokenError(
                f"Zombie worker detected for run {run_id}: held epoch {expected_epoch} "
                f"!= active epoch {row['claim_epoch']}"
            )""",
        "sabotaged": "        pass  # SABOTAGE: Ignored fencing token",
        "test_target": "tests/test_runtime.py::test_zombie_worker_fencing_protection",
    },
    {
        "name": "break-it-replay",
        "description": "Sabotage NondeterminismGuard in ReplayEngine.feed_next",
        "target_file": ROOT / "relay" / "replay.py",
        "original": """        if recorded.event_type != expected_type:
            raise NondeterminismError(
                f"Nondeterminism Guard Triggered on run {self.run_id} at sequence {expected_seq}: "
                f"Recorded event is '{recorded.event_type}', but replay requested '{expected_type}'"
            )""",
        "sabotaged": "        pass  # SABOTAGE: Silenced nondeterminism guard",
        "test_target": "tests/test_replay.py::test_deliberate_live_clock_read_fails_guard_loudly",
    },
    {
        "name": "break-it-idempotency",
        "description": "Sabotage idempotency key uniqueness in apply_effect",
        "target_file": ROOT / "relay" / "runtime.py",
        "original": "        # Step 1 & 2: Check or insert pending",
        "sabotaged": '        import uuid\n        idempotency_key = f"{idempotency_key}_{uuid.uuid4()}"\n        # Step 1 & 2: Check or insert pending',
        "test_target": "tests/test_runtime.py::test_outbound_effect_idempotency_with_sink",
    },
]


def run_test(test_target: str) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", test_target, "-v", "--no-header"]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return res.returncode, res.stdout + res.stderr


def run_experiment(exp: dict) -> bool:
    target_path = Path(exp["target_file"])
    content = target_path.read_text(encoding="utf-8")

    # Normalize line endings
    normalized_content = content.replace("\r\n", "\n")
    normalized_original = exp["original"].replace("\r\n", "\n")

    if normalized_original not in normalized_content:
        print(f"[-] ERROR: Original target snippet not found in {target_path}")
        return False

    print("\n========================================================")
    print(f"Running Experiment: {exp['name']}")
    print(f"Description: {exp['description']}")
    print(f"Target Test: {exp['test_target']}")
    print("========================================================")

    # 1. Verify test PASSES under normal conditions
    rc_clean, out_clean = run_test(exp["test_target"])
    if rc_clean != 0:
        print("[-] Baseline test failed before sabotage!")
        print(out_clean)
        return False
    print("[+] Baseline test PASSED (Green)")

    # 2. Apply Sabotage
    sabotaged_content = normalized_content.replace(normalized_original, exp["sabotaged"])
    target_path.write_text(sabotaged_content, encoding="utf-8")

    try:
        # 3. Verify test FAILS under sabotage
        rc_sabotaged, out_sabotaged = run_test(exp["test_target"])
        if rc_sabotaged == 0:
            print("[-] FAILED: Test unexpectedly PASSED despite sabotage!")
            return False

        print(f"[+] Sabotage verified! Test correctly turned RED (Exit code: {rc_sabotaged})")
        return True
    finally:
        # 4. Restore original file
        target_path.write_text(content, encoding="utf-8")
        print("[+] Restored original file")


def main():
    print("Starting Phase 6 Break-It Sabotage Verification Suite...")
    successes = 0
    for exp in SABOTAGE_EXPERIMENTS:
        if run_experiment(exp):
            successes += 1
        else:
            print(f"[-] Experiment {exp['name']} failed verification!")
            sys.exit(1)

    print("\n========================================================")
    print(
        f"Break-It Verification Complete: {successes}/{len(SABOTAGE_EXPERIMENTS)} sabotages caught!"
    )
    print("Safety Invariant Kill Rate: 100.0% (All sabotages triggered test failures)")
    print("========================================================")


if __name__ == "__main__":
    main()
