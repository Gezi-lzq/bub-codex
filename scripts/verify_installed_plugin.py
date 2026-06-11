#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


EXPECTED_LINE = "run_model_stream: builtin, codex"


def main() -> int:
    env = os.environ.copy()
    env["BUB_CODEX_ENABLED"] = "false"

    command = [sys.executable, "-m", "bub", "hooks"]
    result = subprocess.run(
        command,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)

    if result.returncode != 0:
        print("bub hooks failed while verifying installed bub-codex plugin.", file=sys.stderr)
        print(f"command: {' '.join(command)}", file=sys.stderr)
        if output:
            print(output, file=sys.stderr)
        return result.returncode

    if EXPECTED_LINE not in result.stdout:
        print("bub-codex plugin was not discovered by Bub.", file=sys.stderr)
        print(f"expected hook report line: {EXPECTED_LINE}", file=sys.stderr)
        if output:
            print(output, file=sys.stderr)
        return 1

    print(f"OK: Bub discovered installed bub-codex plugin ({EXPECTED_LINE}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
