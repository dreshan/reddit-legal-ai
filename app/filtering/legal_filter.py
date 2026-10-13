"""Stage 1: cheap rule-based legal keyword pre-filter.

The filter is deliberately permissive - its job is to avoid sending obviously
irrelevant posts to Claude, not to make the final legal/non-legal call.
Posts are labelled ``CANDIDATE_FOR_AI`` or ``FILTERED_OUT``.

Keyword list is configurable via ``LEGAL_KEYWORDS`` (replaces the defaults)
and ``EXTRA_LEGAL_KEYWORDS`` (adds to them).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.models.schemas import FilterDecision, FilterResult, RedditPost

DEFAULT_LEGAL_KEYWORDS: tuple[str, ...] = (
    "law", "legal", "lawyer", "attorney", "court", "judge", "lawsuit", "sue", "suing",
    "contract", "agreement", "claim", "liability", "negligence", "injury", "damages",
    "tenant", "landlord", "eviction", "lease", "divorce", "custody", "child support",
    "employment law", "termination", "harassment", "discrimination", "copyright",
    "trademark", "patent", "privacy", "data protection", "criminal", "police", "arrest",
    "charge", "bail", "immigration", "visa", "citizenship", "tax", "insurance",
    "accident", "settlement", "appeal", "legal advice", "solicitor", "barrister",
)


def _compile(keyword: str) -> re.Pattern[str]:
    # Whole-word match ("sue" must not match "issue"), flexible whitespace in
    # phrases, and simple plurals ("lawyers", "charges").
    words = r"\s+".join(re.escape(word) for word in keyword.lower().split())
    return re.compile(rf"(?<![a-z0-9]){words}(?:s|es)?(?![a-z0-9])", re.IGNORECASE)


class LegalFilter:
    def __init__(self, keywords: Iterable[str] | None = None, min_score: int = 1) -> None:
        unique = dict.fromkeys(k.strip().lower() for k in (keywords or DEFAULT_LEGAL_KEYWORDS) if k.strip())
        if not unique:
            raise ValueError("At least one legal keyword is required.")
        if min_score < 1:
            raise ValueError("min_score must be >= 1.")
        self.keywords: tuple[str, ...] = tuple(unique)
        self.min_score = min_score
        self._patterns = [(keyword, _compile(keyword)) for keyword in self.keywords]

    def matched_keywords(self, post: RedditPost) -> list[str]:
        text = post.text
        return [keyword for keyword, pattern in self._patterns if pattern.search(text)]

    def calculate_legal_keyword_score(self, post: RedditPost) -> int:
        """Number of distinct legal keywords found in the title and body."""
        return len(self.matched_keywords(post))

    def is_legal_related(self, post: RedditPost) -> bool:
        return self.calculate_legal_keyword_score(post) >= self.min_score

    def evaluate(self, post: RedditPost) -> FilterResult:
        matched = self.matched_keywords(post)
        legal = len(matched) >= self.min_score
        return FilterResult(
            legal_related=legal,
            keyword_score=len(matched),
            matched_keywords=matched,
            decision=FilterDecision.CANDIDATE_FOR_AI if legal else FilterDecision.FILTERED_OUT,
        )

    @classmethod
    def from_settings(cls, settings) -> LegalFilter:  # noqa: ANN001 - avoids import cycle
        base = settings.legal_keywords or list(DEFAULT_LEGAL_KEYWORDS)
        return cls(keywords=[*base, *settings.extra_legal_keywords], min_score=settings.min_keyword_score)


_default_filter = LegalFilter()


def calculate_legal_keyword_score(post: RedditPost, keywords: Iterable[str] | None = None) -> int:
    f = _default_filter if keywords is None else LegalFilter(keywords)
    return f.calculate_legal_keyword_score(post)


def is_legal_related(post: RedditPost, keywords: Iterable[str] | None = None, min_score: int = 1) -> bool:
    f = _default_filter if keywords is None and min_score == 1 else LegalFilter(keywords, min_score)
    return f.is_legal_related(post)
