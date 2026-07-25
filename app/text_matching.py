"""Canonical whole-token text matcher for the R&D Claim Evidence Hub.

Implements ADR-0003 (term-matching precision and the decision-support boundary).

This module is the ONE matcher used by both ``app.services`` (eligibility review
flags) and ``app.framework_intelligence`` (requirement theme detection). It is
standard library only and must never import from either consumer: the dependency
direction is one-way into ``app.text_matching`` (ADR-0003 D3/D4).

Load-bearing design details:

* Normalisation never strips or replaces hyphens. Replacing ``-`` with a space
  would turn ``procurement-available`` into ``procurement available`` and make
  the proven false positive worse, not better.
* The whole-token lookarounds include ``-``. A plain ``\\b`` does NOT resolve the
  proven case, because a hyphen is itself a word boundary: ``\\bprocurement\\b``
  still matches ``procurement-available``.
* Matching returns evidence (:class:`TermMatch`), not a boolean, so a caller can
  name the term that fired and quote where it fired. That is what converts a
  verdict into a reviewable signal.

Pure functions over strings. No network access, no LLM, no mutation of the text
that was passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence
import re
import unicodedata


# Characters of context kept either side of a matched span in ``TermMatch.excerpt``.
EXCERPT_CONTEXT_CHARS = 40

# ADR-0003 D3 Stage 1. The hyphen inside both lookarounds is the whole point.
_BOUNDARY_PREFIX = r"(?<![a-z0-9-])"
_BOUNDARY_SUFFIX = r"(?![a-z0-9-])"


@dataclass(frozen=True)
class TermMatch:
    """One whole-token hit of a configured term inside normalised text."""

    term: str
    start: int
    end: int
    excerpt: str


def normalise(text: str | None) -> str:
    """Stage 0. Lowercase, NFKC, collapse whitespace, strip, then pad with spaces.

    Hyphens are deliberately preserved.
    """
    lowered = str(text or "").lower()
    normalised = unicodedata.normalize("NFKC", lowered)
    collapsed = re.sub(r"\s+", " ", normalised).strip()
    return f" {collapsed} "


def normalise_term(term: str | None) -> str:
    """Normalise a configured term or stop phrase without the padding spaces."""
    lowered = str(term or "").lower()
    normalised = unicodedata.normalize("NFKC", lowered)
    return re.sub(r"\s+", " ", normalised).strip()


@lru_cache(maxsize=4096)
def compiled_term_pattern(term: str) -> re.Pattern[str] | None:
    """Stage 1. Compile a whole-token, hyphen-compound-aware pattern for ``term``.

    Cached on the term string, so repeated scoring runs do not recompile.
    """
    cleaned = normalise_term(term)
    if not cleaned:
        return None
    words = [re.escape(word) for word in cleaned.split(" ") if word]
    if not words:
        return None
    return re.compile(_BOUNDARY_PREFIX + r"\s+".join(words) + _BOUNDARY_SUFFIX)


def _excerpt(normalised: str, start: int, end: int) -> str:
    left = max(0, start - EXCERPT_CONTEXT_CHARS)
    right = min(len(normalised), end + EXCERPT_CONTEXT_CHARS)
    return normalised[left:right].strip()


def _spans(normalised: str, terms: Sequence[str] | None) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    for term in terms or ():
        pattern = compiled_term_pattern(str(term))
        if pattern is None:
            continue
        for match in pattern.finditer(normalised):
            spans.append((str(term), match.start(), match.end()))
    return spans


def find_matches(
    text: str | None,
    terms: Sequence[str],
    stop_phrases: Sequence[str] = (),
) -> list[TermMatch]:
    """Return every whole-token hit of ``terms`` in ``text`` that survives ``stop_phrases``.

    Stage 2: a term hit is discarded when any stop-phrase match span contains it,
    so ``"no commercial solution existed"`` does not fire the term ``commercial``.

    Results are sorted by position, so the same input always yields the same
    ordered evidence.
    """
    normalised = normalise(text)
    if not normalised.strip():
        return []
    stop_spans = [(start, end) for _, start, end in _spans(normalised, stop_phrases)]
    matches: list[TermMatch] = []
    for term, start, end in _spans(normalised, terms):
        if any(stop_start <= start and end <= stop_end for stop_start, stop_end in stop_spans):
            continue
        matches.append(
            TermMatch(term=term, start=start, end=end, excerpt=_excerpt(normalised, start, end))
        )
    matches.sort(key=lambda item: (item.start, item.end, item.term))
    return matches


def matched_terms(
    text: str | None,
    terms: Sequence[str],
    stop_phrases: Sequence[str] = (),
) -> list[str]:
    """Return the distinct configured terms that matched, in order of first appearance."""
    ordered: list[str] = []
    for match in find_matches(text, terms, stop_phrases):
        if match.term not in ordered:
            ordered.append(match.term)
    return ordered
