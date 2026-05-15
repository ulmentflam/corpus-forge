"""Unit tests for `RuleBasedClassifier`.

Phase E / Wave 0 — C-02.

The rule classifier walks priority-ordered rules and emits a
`ClassLabel` for every input — never returns `None` (fallback is
`class=other` 0.3). Tests cover:

* One positive case per class value (9 values).
* Pedagogy regex anchored to title + path (not body — cost).
* PDF page-count proxy when ``metadata["page_count"]`` is absent.
* Chat-marker density gate (≥3 per kB).
* Idempotency — same input yields same output.
"""

from __future__ import annotations

import pytest

from corpus_forge.classifiers.base import ClassifiableDocument, ClassLabel
from corpus_forge.classifiers.rule_based import RuleBasedClassifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc(
    *,
    source_uri: str = "file:///x/y.md",
    title: str | None = None,
    text: str = "body",
    format_labels: list[tuple[str, str]] | None = None,
    metadata: dict | None = None,
) -> ClassifiableDocument:
    return ClassifiableDocument(
        document_id=1,
        source_uri=source_uri,
        title=title,
        text=text,
        format_labels=format_labels or [],
        metadata=metadata or {},
    )


@pytest.fixture
def classifier() -> RuleBasedClassifier:
    return RuleBasedClassifier()


# ---------------------------------------------------------------------------
# Positive cases — one per class value
# ---------------------------------------------------------------------------


class TestEachClassValueEmittable:
    def test_format_code_to_code(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///repo/main.py",
                text="def f(): return 1",
                format_labels=[("format", "code"), ("language", "python")],
            )
        )
        assert isinstance(out, ClassLabel)
        assert out.value == "code"
        assert out.confidence >= 0.9

    def test_claude_code_uri_to_chat(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="claude-code://session-abc",
                text="User: hello\nAssistant: hi",
            )
        )
        assert out.value == "chat"
        assert out.confidence >= 0.9

    def test_format_conversation_to_chat(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="opencode://x",
                text="hi",
                format_labels=[("format", "conversation")],
            )
        )
        assert out.value == "chat"

    def test_format_epub_to_book(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///library/small-book.epub",
                title="Small fixture book",
                text="Once upon a time...",
                format_labels=[("format", "epub")],
            )
        )
        assert out.value == "book"

    def test_format_epub_pedagogy_to_textbook(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///library/intro-to-stats.epub",
                title="Introduction to Statistics — A Textbook",
                text="Chapter 1: ...",
                format_labels=[("format", "epub")],
            )
        )
        assert out.value == "textbook"

    def test_path_papers_to_paper(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///vault/papers/some.md",
                text="abstract...",
                format_labels=[("format", "markdown")],
            )
        )
        assert out.value == "paper"

    def test_arxiv_prefix_to_paper(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///downloads/arxiv-2305.12345.pdf",
                text="brief",
                format_labels=[("format", "pdf")],
            )
        )
        assert out.value == "paper"

    def test_bib_extension_to_paper(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///x/refs.bib",
                text="@article{foo}",
            )
        )
        assert out.value == "paper"

    def test_path_notes_to_note(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///vault/notes/today.md",
                text="todo",
                format_labels=[("format", "markdown")],
            )
        )
        assert out.value == "note"

    def test_vault_daily_to_note(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///vault/daily/2025-01-01.md",
                text="journal",
                format_labels=[("format", "markdown")],
            )
        )
        assert out.value == "note"

    def test_blog_path_to_article(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///site/blog/welcome.md",
                text="post body",
                format_labels=[("format", "markdown")],
            )
        )
        assert out.value == "article"

    def test_path_docs_to_reference(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///proj/docs/api.md",
                text="ref",
                format_labels=[("format", "markdown")],
            )
        )
        assert out.value == "reference"

    def test_format_json_to_reference(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///data/config.json",
                text='{"k": 1}',
                format_labels=[("format", "json")],
            )
        )
        assert out.value == "reference"
        assert out.confidence >= 0.8

    def test_format_yaml_to_reference(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///data/config.yaml",
                text="k: 1",
                format_labels=[("format", "yaml")],
            )
        )
        assert out.value == "reference"

    def test_format_toml_to_reference(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///data/config.toml",
                text="k = 1",
                format_labels=[("format", "toml")],
            )
        )
        assert out.value == "reference"

    def test_format_csv_to_reference(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///data/rows.csv",
                text="a,b,c\n1,2,3\n",
                format_labels=[("format", "csv")],
            )
        )
        assert out.value == "reference"

    def test_format_srt_to_reference(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///media/t.srt",
                text="1\n00:00:00 --> 00:00:01\nhi\n",
                format_labels=[("format", "srt")],
            )
        )
        assert out.value == "reference"

    def test_chat_markers_density_to_chat(self, classifier: RuleBasedClassifier) -> None:
        body = "\n".join(
            [
                "User: hello",
                "Assistant: hi there",
                "User: thanks",
                "Assistant: welcome",
                "Human: bye",
            ]
        )
        out = classifier.classify(
            _doc(
                source_uri="file:///x/transcript.md",
                text=body,
                format_labels=[("format", "markdown")],
            )
        )
        # Should trip the chat-markers heuristic; >=3 markers in a tiny body.
        assert out.value == "chat"

    def test_pdf_paper_pattern_to_paper(self, classifier: RuleBasedClassifier) -> None:
        body = (
            "Abstract\n"
            "This paper investigates ...\n"
            "Intro [1] and discussion [2].\n"
            "\nReferences\n[1] Foo\n[2] Bar\n"
        )
        out = classifier.classify(
            _doc(
                source_uri="file:///downloads/some-paper.pdf",
                title="On the matter of foo",
                text=body,
                format_labels=[("format", "pdf")],
                metadata={"page_count": 12},
            )
        )
        assert out.value == "paper"

    def test_pdf_long_no_pattern_to_book(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///library/novel.pdf",
                title="A Novel",
                text="prose " * 1000,
                format_labels=[("format", "pdf")],
                metadata={"page_count": 60},
            )
        )
        assert out.value == "book"

    def test_pdf_medium_pedagogy_to_textbook(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///library/intro-to-cs-handbook.pdf",
                title="Introduction to CS Handbook",
                text="prose " * 200,
                format_labels=[("format", "pdf")],
                metadata={"page_count": 20},
            )
        )
        assert out.value == "textbook"

    def test_pdf_medium_no_pedagogy_to_book(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///library/some-novel.pdf",
                title="Some Novel",
                text="prose " * 200,
                format_labels=[("format", "pdf")],
                metadata={"page_count": 20},
            )
        )
        assert out.value == "book"

    def test_pdf_short_to_article(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///downloads/leaflet.pdf",
                title="Short PDF",
                text="just a few words",
                format_labels=[("format", "pdf")],
                metadata={"page_count": 3},
            )
        )
        assert out.value == "article"

    def test_markdown_default_to_note(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///vault/random-thought.md",
                text="just a markdown note",
                format_labels=[("format", "markdown")],
            )
        )
        assert out.value == "note"

    def test_html_to_article(self, classifier: RuleBasedClassifier) -> None:
        # HTML with no other signal: content-heuristic returns article (a
        # PDF without a paper pattern + short → article; HTML's natural
        # default is article via the path-or-content fallback).
        out = classifier.classify(
            _doc(
                source_uri="file:///site/welcome.html",
                title="Welcome",
                text="hello world",
                format_labels=[("format", "html")],
            )
        )
        assert out.value == "article"

    def test_fallback_to_other(self, classifier: RuleBasedClassifier) -> None:
        # No format labels, no path hints, no body signal.
        out = classifier.classify(
            _doc(
                source_uri="urn:opaque",
                title=None,
                text="x",
                format_labels=[],
                metadata={},
            )
        )
        assert out.value == "other"
        assert out.confidence == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# Pedagogy regex anchored to title + path
# ---------------------------------------------------------------------------


class TestPedagogyRegexAnchor:
    def test_pedagogy_body_alone_does_not_trip(self, classifier: RuleBasedClassifier) -> None:
        """The pedagogy regex must NOT scan the document body — cost guard.

        Body says "textbook" repeatedly but title + path don't; we should
        still classify as `book`, not `textbook`.
        """
        out = classifier.classify(
            _doc(
                source_uri="file:///library/random.epub",
                title="A Story",
                text="This is a textbook in spirit. textbook textbook.",
                format_labels=[("format", "epub")],
            )
        )
        assert out.value == "book"

    def test_pedagogy_title_trips(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///library/x.epub",
                title="A Practical Tutorial on Bayesian Inference",
                text="body",
                format_labels=[("format", "epub")],
            )
        )
        assert out.value == "textbook"

    def test_pedagogy_path_trips(self, classifier: RuleBasedClassifier) -> None:
        out = classifier.classify(
            _doc(
                source_uri="file:///library/course-notes-2024.epub",
                title=None,
                text="body",
                format_labels=[("format", "epub")],
            )
        )
        assert out.value == "textbook"


# ---------------------------------------------------------------------------
# Idempotency & contract
# ---------------------------------------------------------------------------


class TestContract:
    def test_classifier_name(self, classifier: RuleBasedClassifier) -> None:
        assert classifier.name == "rule"

    def test_idempotent(self, classifier: RuleBasedClassifier) -> None:
        d = _doc(
            source_uri="file:///vault/notes/today.md",
            text="x",
            format_labels=[("format", "markdown")],
        )
        a = classifier.classify(d)
        b = classifier.classify(d)
        assert a == b

    def test_never_returns_none(self, classifier: RuleBasedClassifier) -> None:
        """Rule classifier is the safety net — always emits a `ClassLabel`."""
        out = classifier.classify(_doc(source_uri="urn:totally-opaque"))
        assert out is not None
        assert isinstance(out, ClassLabel)

    def test_confidence_in_bounds(self, classifier: RuleBasedClassifier) -> None:
        d = _doc(source_uri="file:///x/y.md", format_labels=[("format", "markdown")])
        out = classifier.classify(d)
        assert 0.0 <= out.confidence <= 1.0
