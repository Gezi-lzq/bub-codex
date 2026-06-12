"""Shared JSON, hashing, and preview helpers.

These helpers define deterministic JSON behavior used by event ids, hashes, and
audit previews. Keep semantic runtime logic out of this module.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


JsonObject = dict[str, Any]


def dict_or_empty(value: Any) -> JsonObject:
    return value if isinstance(value, dict) else {}


def optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str | None:
    if value is None:
        return None
    return sha256_text(canonical_json(value))


def preview_json(value: Any, *, max_chars: int = 800, truncation_marker: str = "") -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else canonical_json(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + truncation_marker
