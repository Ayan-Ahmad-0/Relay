"""Helper script to create and push the 3 break-it sabotage branches.

Creates:
  1. break-it-fencing: breaks claim_epoch validation
  2. break-it-replay: breaks nondeterminism replay guard
  3. break-it-idempotency: breaks idempotency key generation
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BRANCHES = [
    {
        "name": "break-it-fencing",
        "patch": "break-it/01_fencing.patch",
        "commit_msg": "test(sabotage): break fencing token validation to verify CI failure",
    },
    {
        "name": "break-it-replay",
        "patch": "break-it/02_replay.patch",
        "commit_msg": "test(sabotage): break nondeterminism replay guard to verify CI failure",
    },
    {
        "name": "break-it-idempotency",
        "patch": "break-it/03_idempotency.patch",
        "commit_msg": "test(sabotage): break idempotency key generation to verify CI failure",
    },
]


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)


def main():
    print("[*] Creating 3 break-it branches from current main...")
    orig_branch_res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    orig_branch = orig_branch_res.stdout.strip() or "main"

    for b in BRANCHES:
        name = b["name"]
        patch = str(ROOT / b["patch"])
        msg = b["commit_msg"]

        print(f"\n--- Setting up {name} ---")
        run_cmd(["git", "checkout", orig_branch])
        run_cmd(["git", "branch", "-D", name])
        chk = run_cmd(["git", "checkout", "-b", name])
        if chk.returncode != 0:
            print(f"[-] Failed to checkout branch {name}: {chk.stderr}")
            continue

        apply = run_cmd(["git", "apply", patch])
        if apply.returncode != 0:
            print(f"[-] Failed to apply patch {patch}: {apply.stderr}")
            run_cmd(["git", "checkout", orig_branch])
            continue

        run_cmd(["git", "add", "."])
        run_cmd(["git", "commit", "-m", msg])
        print(f"[+] Created branch {name} with sabotage commit!")

    run_cmd(["git", "checkout", orig_branch])
    print(f"\n[+] Returned to {orig_branch}. All 3 break-it branches created locally!")
    print("[*] To push all break-it branches to remote, run:")
    print("    git push -u origin break-it-fencing break-it-replay break-it-idempotency")


if __name__ == "__main__":
    main()
