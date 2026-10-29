"""Pick out the words of a query that carry meaning for keyword matching."""

import re

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for",
        "from", "how", "i", "in", "is", "it", "me", "of", "on", "or", "tell", "the",
        "to", "what", "when", "where", "which", "who", "why", "with", "you",
    }
)


def significant_terms(text: str, *, min_length: int = 2) -> list[str]:
    """Lower-cased tokens of text that are not stopwords, in order of appearance."""
    return [
        token
        for token in _TOKEN_PATTERN.findall(text.lower())
        if len(token) >= min_length and token not in STOPWORDS
    ]
