"""Stdlib-only rule-based content classifier — Phase E / Wave 0 — C-02.

The rule classifier is the cheap default in the classification chain.
It walks priority-ordered heuristics and emits a :class:`ClassLabel`
for every input (never returns ``None`` — the fallback is
``class=other`` 0.3). Confidence values are hand-calibrated so a
downstream :class:`~corpus_forge.classifiers.llm.LLMClassifier` (P1)
can decide whether to escalate based on
:attr:`ClassifierConfig.escalation_threshold`.

Cost guard: the pedagogy regex is matched against ``title + source_uri``
only, never the document body. Body-scanning regexes would blow up the
per-doc cost on large EPUB / PDF inputs; the title/path pair is enough
signal for the textbook-vs-book disambiguation we care about.
"""

from __future__ import annotations

import re

from .base import ClassifiableDocument, ClassLabel

# ---------------------------------------------------------------------------
# Constants & precompiled patterns
# ---------------------------------------------------------------------------

# Pedagogy markers — case-insensitive, word-boundary-anchored.
_PEDAGOGY_RE = re.compile(
    r"\b(textbook|primer|introduction to|course|handbook|cookbook|"
    r"tutorial|exercises?|lectures?)\b",
    re.IGNORECASE,
)

# Chat markers in the body. We count ``User:`` / ``Assistant:`` /
# ``Human:`` line-starts via re.MULTILINE.
_CHAT_MARKER_RE = re.compile(r"^(User|Assistant|Human):", re.MULTILINE)

# PDF paper signal — Abstract + References + numeric-bracket citations.
_PDF_ABSTRACT_RE = re.compile(r"^Abstract\b", re.MULTILINE)
_PDF_REFERENCES_RE = re.compile(r"\bReferences\b")
_PDF_CITATION_RE = re.compile(r"\[\d+\]")

# Reference-format set: structured data + subtitle formats land here.
_REFERENCE_FORMATS: frozenset[str] = frozenset({"json", "yaml", "toml", "csv", "srt"})

# Path heuristics — keys are case-insensitive substrings of source_uri.
_PAPER_PATH_TOKENS: tuple[str, ...] = ("/papers/", "/research/")
_NOTE_PATH_TOKENS: tuple[str, ...] = ("/notes/", "/daily/", "/journal/")
_ARTICLE_PATH_TOKENS: tuple[str, ...] = ("/blog/", "/posts/", "/articles/")
_REFERENCE_PATH_TOKENS: tuple[str, ...] = ("/docs/", "/reference/", "/api/")

# Chat-source URI schemes.
_CHAT_URI_SCHEMES: tuple[str, ...] = (
    "claude-code://",
    "opencode://",
    "gemini-cli://",
)

# Chat marker density gate — at least N markers, AND at least M markers
# per kB of body (whichever is more permissive). The plan says "many
# `^User:` per kilobyte"; we use min(3, density>=3/kB) so small chat
# fixtures (one short conversation) still trip the rule.
_CHAT_MIN_ABSOLUTE_MARKERS: int = 3
_CHAT_MARKERS_PER_KB: float = 3.0

# PDF length thresholds (in pages).
_PDF_BOOK_MIN_PAGES: int = 50
_PDF_TEXTBOOK_MIN_PAGES: int = 8

# Average chars-per-page proxy used when ``metadata['page_count']`` is
# absent. PDF digital extractors typically emit page_count; this proxy
# is a safety net for inputs that don't carry the metadata.
_CHARS_PER_PAGE_PROXY: int = 3000


def _norm_uri(uri: str) -> str:
    """Lowercased URI for case-insensitive path matching."""
    return uri.lower()


def _has_format(labels: list[tuple[str, str]], value: str) -> bool:
    """Return True iff ``("format", value)`` appears in ``labels``."""
    return ("format", value) in labels


def _pedagogy_hit(title: str | None, source_uri: str) -> bool:
    """Pedagogy regex on title + path (NEVER body — cost guard)."""
    blob = f"{title or ''} {source_uri}"
    return bool(_PEDAGOGY_RE.search(blob))


def _page_count(doc: ClassifiableDocument) -> int:
    """Best-effort page count.

    Prefers ``metadata['page_count']`` (set by the digital PDF
    extractor); falls back to a coarse ``len(text) / _CHARS_PER_PAGE_PROXY``
    estimate. Always returns ``>= 1``.
    """
    raw = doc.metadata.get("page_count") if doc.metadata else None
    if isinstance(raw, int) and raw > 0:
        return raw
    return max(1, len(doc.text) // _CHARS_PER_PAGE_PROXY)


def _chat_density_trips(text: str) -> bool:
    """Return True iff body has enough chat markers to call it a chat."""
    n_markers = len(_CHAT_MARKER_RE.findall(text))
    if n_markers < _CHAT_MIN_ABSOLUTE_MARKERS:
        return False
    kb = max(1.0, len(text.encode("utf-8")) / 1024.0)
    return (n_markers / kb) >= _CHAT_MARKERS_PER_KB


# ---------------------------------------------------------------------------
# Rule-based classifier
# ---------------------------------------------------------------------------


class RuleBasedClassifier:
    """Priority-ordered heuristic classifier.

    See the module docstring for the full rule set. The classifier is
    stateless — instances are safe to share across threads.
    """

    name: str = "rule"

    def classify(self, doc: ClassifiableDocument) -> ClassLabel:
        """Return a :class:`ClassLabel` for ``doc`` (never ``None``).

        Walks the rules in priority order; the first match wins.
        """
        labels = doc.format_labels or []
        uri = doc.source_uri or ""
        norm_uri = _norm_uri(uri)

        # ── 1. Format-label fast path ─────────────────────────────────
        if _has_format(labels, "code"):
            return ClassLabel(
                value="code",
                confidence=0.99,
                rationale="format=code",
            )

        # Chat: by URI scheme OR by format=conversation.
        if any(uri.startswith(s) for s in _CHAT_URI_SCHEMES) or _has_format(labels, "conversation"):
            return ClassLabel(
                value="chat",
                confidence=0.99,
                rationale="chat URI scheme or format=conversation",
            )

        # EPUB: pedagogy → textbook, else book.
        if _has_format(labels, "epub"):
            if _pedagogy_hit(doc.title, uri):
                return ClassLabel(
                    value="textbook",
                    confidence=0.85,
                    rationale="format=epub + pedagogy regex",
                )
            return ClassLabel(
                value="book",
                confidence=0.7,
                rationale="format=epub",
            )

        # ── 2. Path / filename heuristics ─────────────────────────────
        if (
            any(tok in norm_uri for tok in _PAPER_PATH_TOKENS)
            or "/arxiv-" in norm_uri
            or norm_uri.rsplit("/", 1)[-1].startswith("arxiv-")
            or norm_uri.endswith(".bib")
        ):
            return ClassLabel(
                value="paper",
                confidence=0.7,
                rationale="path/filename hint: papers/arxiv/.bib",
            )

        if any(tok in norm_uri for tok in _NOTE_PATH_TOKENS):
            return ClassLabel(
                value="note",
                confidence=0.8,
                rationale="path hint: notes/daily/journal",
            )

        if any(tok in norm_uri for tok in _ARTICLE_PATH_TOKENS):
            return ClassLabel(
                value="article",
                confidence=0.7,
                rationale="path hint: blog/posts/articles",
            )

        if any(tok in norm_uri for tok in _REFERENCE_PATH_TOKENS):
            return ClassLabel(
                value="reference",
                confidence=0.7,
                rationale="path hint: docs/reference/api",
            )

        # ── 3. Content heuristics ─────────────────────────────────────
        # Structured-data + subtitle formats → reference.
        for fmt in _REFERENCE_FORMATS:
            if _has_format(labels, fmt):
                return ClassLabel(
                    value="reference",
                    confidence=0.9,
                    rationale=f"format={fmt}",
                )

        # Chat markers in body (density-gated).
        if _chat_density_trips(doc.text):
            return ClassLabel(
                value="chat",
                confidence=0.85,
                rationale="chat markers in body",
            )

        # PDF heuristics.
        if _has_format(labels, "pdf"):
            text = doc.text or ""
            is_paper = (
                bool(_PDF_ABSTRACT_RE.search(text))
                and bool(_PDF_REFERENCES_RE.search(text))
                and bool(_PDF_CITATION_RE.search(text))
            )
            if is_paper:
                return ClassLabel(
                    value="paper",
                    confidence=0.75,
                    rationale="PDF: Abstract+References+[n] citations",
                )
            pages = _page_count(doc)
            if pages >= _PDF_BOOK_MIN_PAGES:
                return ClassLabel(
                    value="book",
                    confidence=0.55,
                    rationale=f"PDF: long ({pages}p)",
                )
            if pages >= _PDF_TEXTBOOK_MIN_PAGES:
                if _pedagogy_hit(doc.title, uri):
                    return ClassLabel(
                        value="textbook",
                        confidence=0.45,
                        rationale=f"PDF: medium ({pages}p) + pedagogy",
                    )
                return ClassLabel(
                    value="book",
                    confidence=0.45,
                    rationale=f"PDF: medium ({pages}p), no pedagogy",
                )
            return ClassLabel(
                value="article",
                confidence=0.5,
                rationale=f"PDF: short ({pages}p)",
            )

        # HTML → article when no other signal hit (path heuristics
        # already ran above so we know it isn't under /blog/ etc.).
        if _has_format(labels, "html"):
            return ClassLabel(
                value="article",
                confidence=0.5,
                rationale="format=html",
            )

        # ── 4. Markdown-vault default ─────────────────────────────────
        if _has_format(labels, "markdown"):
            return ClassLabel(
                value="note",
                confidence=0.5,
                rationale="markdown default",
            )

        # ── 4b. Filename-extension fallback ──────────────────────────
        # Several extractors (passthrough markdown, plaintext, structured
        # data, subtitle, notebook, office) do not emit a ``format=*``
        # label even though the extension is unambiguous. Recover the
        # signal from ``source_uri`` so the user gets a usable class
        # rather than the catch-all ``other``.
        ext_class = _classify_by_extension(uri)
        if ext_class is not None:
            return ext_class

        # ── 5. Fallback ───────────────────────────────────────────────
        return ClassLabel(
            value="other",
            confidence=0.3,
            rationale="no rule matched",
        )


# ---------------------------------------------------------------------------
# Extension-based fallback (Phase E, calibration vs the multi-format fixture)
# ---------------------------------------------------------------------------

# Markdown-like extensions → note (mirrors the "Markdown-vault default"
# rule for inputs whose extractor didn't emit ``format=markdown``).
_NOTE_EXTS: frozenset[str] = frozenset({".md", ".markdown", ".rst", ".txt", ".tex"})

# Structured-data + manifest formats → reference. The structured
# extractor doesn't emit a format label so we recover it here.
_REFERENCE_EXTS: frozenset[str] = frozenset(
    {".json", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".srt", ".vtt"}
)

# Office / notebook / heavyweight knowledge formats → article when
# nothing stronger applies. Notebooks contain code cells but are usually
# narrative; office docs are most commonly reports / memos / decks.
_ARTICLE_EXTS: frozenset[str] = frozenset({".ipynb", ".docx", ".pptx", ".xlsx", ".odt", ".ods"})


def _classify_by_extension(uri: str) -> ClassLabel | None:
    """Return a fallback :class:`ClassLabel` from ``uri``'s extension, or None."""
    # Strip query/fragment if present and pick the suffix.
    if "?" in uri:
        uri = uri.split("?", 1)[0]
    if "#" in uri:
        uri = uri.split("#", 1)[0]
    last = uri.rsplit("/", 1)[-1].lower()
    if "." not in last:
        return None
    ext = "." + last.rsplit(".", 1)[-1]
    if ext in _NOTE_EXTS:
        return ClassLabel(
            value="note",
            confidence=0.5,
            rationale=f"extension fallback: {ext}",
        )
    if ext in _REFERENCE_EXTS:
        return ClassLabel(
            value="reference",
            confidence=0.7,
            rationale=f"extension fallback: {ext}",
        )
    if ext in _ARTICLE_EXTS:
        return ClassLabel(
            value="article",
            confidence=0.45,
            rationale=f"extension fallback: {ext}",
        )
    return None
