"""Portable and traversal-safe filename helpers."""

import re
import unicodedata
from pathlib import Path

from songdrop.exceptions import FileWriteFailed

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_REPEATED_WHITESPACE = re.compile(r"\s+")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def sanitize_component(value: str, *, fallback: str = "Untitled", max_length: int = 180) -> str:
    """Return a portable single path component without inventing metadata."""

    normalized = unicodedata.normalize("NFC", value)
    normalized = _ILLEGAL_CHARS.sub("_", normalized)
    normalized = _REPEATED_WHITESPACE.sub(" ", normalized).strip(" .")
    if normalized in {"", ".", ".."} or not normalized.strip("_"):
        normalized = fallback
    if normalized.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        normalized = f"_{normalized}"
    normalized = normalized[:max_length].rstrip(" .")
    return normalized or fallback


def ensure_within(root: Path, candidate: Path) -> Path:
    """Resolve a candidate and prove it remains below the library root."""

    resolved_root = root.expanduser().resolve(strict=False)
    resolved_candidate = candidate.expanduser().resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise FileWriteFailed(f"Refusing to write outside library: {resolved_candidate}") from error
    return resolved_candidate
