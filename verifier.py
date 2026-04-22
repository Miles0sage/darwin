#!/usr/bin/env python3
"""
Darwin Verifier — Runs the agent in a subprocess and checks pass/fail.

Returns structured result: success bool, stdout, stderr, return code.
"""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).parent


@dataclass
class VerifyResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int


def verify_agent() -> VerifyResult:
    """Run agent.py and capture result."""
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "agent.py")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(BASE_DIR),
    )
    return VerifyResult(
        success=result.returncode == 0,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


def main():
    print("=" * 60)
    print("DARWIN VERIFIER — Running agent health check")
    print("=" * 60)

    result = verify_agent()

    if result.success:
        print("\n[PASS] Agent executed successfully.")
        print(result.stdout)
    else:
        print("\n[FAIL] Agent crashed!")
        print(f"  Return code: {result.returncode}")
        print(f"  stderr: {result.stderr.strip()}")

    return result


if __name__ == "__main__":
    r = main()
    sys.exit(0 if r.success else 1)
