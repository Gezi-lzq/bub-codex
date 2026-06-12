from __future__ import annotations


def prompt_with_startup_context(*, prompt: str, startup_context: str | None) -> str:
    if not startup_context:
        return prompt
    return f"Startup context:\n{startup_context}\n\nUser message:\n{prompt}"
