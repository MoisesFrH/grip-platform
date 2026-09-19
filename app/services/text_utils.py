"""Shared text-normalization helpers for keyword-based matching (used by
both the safety layer and the intent classifier's deterministic rules)."""

import re
import unicodedata


def normalize(text: str) -> str:
    """Lowercase + strip accents, so 'depresión'/'depresion' and
    'AUTOLESIÓN'/'autolesion' match the same pattern."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def compile_keyword_pattern(keywords: list[str]) -> re.Pattern:
    escaped = [re.escape(normalize(k)) for k in keywords]
    return re.compile(r"(" + "|".join(escaped) + r")")
