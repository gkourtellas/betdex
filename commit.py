#!/usr/bin/env python3
"""Save changes and send them to GitHub (git commit + push)."""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
BLOCKED_PATHS = (".env", ".env.local")


def run(cmd, check=True):
    print(f"\n> {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT_DIR, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 commit.py "short description of what you changed"')
        sys.exit(1)

    message = " ".join(sys.argv[1:]).strip()
    if not message:
        print("Commit message cannot be empty.")
        sys.exit(1)

    print(f"Project folder: {PROJECT_DIR}")

    status = run(["git", "status", "--porcelain"], check=False)
    if status.returncode != 0:
        print("Git failed. Is this folder a git repo?")
        sys.exit(1)

    lines = [line for line in status.stdout.splitlines() if line.strip()]
    if not lines:
        print("Nothing to commit — no changes.")
        sys.exit(0)

    for line in lines:
        path = line[3:].strip()
        if any(path.endswith(name) or path == name for name in BLOCKED_PATHS):
            print(f"Blocked: will not commit secrets file ({path})")
            sys.exit(1)

    run(["git", "add", "-A"])

    commit = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
    )
    if commit.stdout:
        print(commit.stdout.rstrip())
    if commit.stderr:
        print(commit.stderr.rstrip())
    if commit.returncode != 0:
        print("Commit failed (maybe nothing staged?).")
        sys.exit(commit.returncode)

    push = run(["git", "push"], check=False)
    if push.returncode != 0:
        print("Push failed. Commit may still be saved locally.")
        sys.exit(push.returncode)

    print("\nDone — changes are on GitHub.")


if __name__ == "__main__":
    main()
