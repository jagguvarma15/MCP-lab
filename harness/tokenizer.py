"""
Token estimation for context cost analysis.

Uses tiktoken (cl100k_base encoding) when available for accurate counts.
Falls back to a character-ratio heuristic when tiktoken is not installed.

Install for accurate counting:
    pip install mcp-lab[tokens]
"""

from __future__ import annotations

try:
    import tiktoken as _tiktoken
    _encoder = _tiktoken.get_encoding("cl100k_base")
except ModuleNotFoundError:
    _tiktoken = None
    _encoder = None

_CHARS_PER_TOKEN_FALLBACK = 4


def estimate_tokens(text: str) -> int:
    """Return an estimated token count for *text*.

    When tiktoken is installed the count is exact (cl100k_base encoding).
    Otherwise falls back to ``len(text) // 4``.
    """
    if _encoder is not None:
        return len(_encoder.encode(text))
    return len(text) // _CHARS_PER_TOKEN_FALLBACK


def has_tiktoken() -> bool:
    """Return True if tiktoken is available for accurate token counting."""
    return _tiktoken is not None
