#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


EXPECTED_HOOK = "run_model_stream"
EXPECTED_PLUGIN = "codex"


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

    plugins = _hook_plugins(result.stdout, EXPECTED_HOOK)
    if EXPECTED_PLUGIN not in plugins:
        print("bub-codex plugin was not discovered by Bub.", file=sys.stderr)
        print(f"expected {EXPECTED_PLUGIN!r} under {EXPECTED_HOOK!r}", file=sys.stderr)
        if output:
            print(output, file=sys.stderr)
        return 1

    print(f"OK: Bub discovered installed bub-codex plugin under {EXPECTED_HOOK}.")
    return 0


def _hook_plugins(output: str, hook_name: str) -> set[str]:
    prefix = f"{hook_name}:"
    for line in output.splitlines():
        if not line.startswith(prefix):
            continue
        names = line[len(prefix) :].strip()
        return {name.strip() for name in names.split(",") if name.strip()}
    return set()


if __name__ == "__main__":
    raise SystemExit(main())
