"""Unit tests for G-02 backend helpers on chat_templates table — G-02 RED.

Covers register_chat_template / list_chat_templates / get_chat_template_by_name
on SQLiteBackend (in-memory).  Same contract will be enforced on PostgresBackend
in integration, but SQLite in-memory suffices for the unit gate.

Run command:
    uv run pytest tests/unit/test_chat_template_backend_helpers.py -v
"""

from __future__ import annotations

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    """Fresh migrated in-memory SQLiteBackend for each test."""
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


# ---------------------------------------------------------------------------
# register_chat_template
# ---------------------------------------------------------------------------


class TestRegisterChatTemplate:
    """register_chat_template(name, source, *, jinja, model_id, description, host).

    Returns (id, created).
    """

    def test_register_creates_row(self, backend: SQLiteBackend):
        """First registration inserts a row and returns (template_id, True)."""
        template_id, created = backend.register_chat_template(
            "chatml",
            source="builtin",
            host="test-host",
        )
        assert isinstance(template_id, int)
        assert template_id > 0
        assert created is True

        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT name, source, host FROM chat_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
        assert row["name"] == "chatml"
        assert row["source"] == "builtin"
        assert row["host"] == "test-host"

    def test_register_stores_optional_jinja(self, backend: SQLiteBackend):
        """jinja kwarg is persisted in the jinja column."""
        jinja_str = "{% for m in messages %}{{ m['content'] }}{% endfor %}"
        template_id, _ = backend.register_chat_template(
            "custom-fmt",
            source="custom",
            host="host-1",
            jinja=jinja_str,
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT jinja FROM chat_templates WHERE id = ?", (template_id,)
            ).fetchone()
        assert row["jinja"] == jinja_str

    def test_register_stores_optional_model_id(self, backend: SQLiteBackend):
        """model_id kwarg is persisted in the model_id column."""
        template_id, _ = backend.register_chat_template(
            "llama3-hf",
            source="hf",
            host="host-1",
            model_id="meta-llama/Llama-3.1-8B-Instruct",
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT model_id FROM chat_templates WHERE id = ?", (template_id,)
            ).fetchone()
        assert row["model_id"] == "meta-llama/Llama-3.1-8B-Instruct"

    def test_register_stores_optional_description(self, backend: SQLiteBackend):
        """description kwarg is persisted in the description column."""
        template_id, _ = backend.register_chat_template(
            "alpaca-v2",
            source="builtin",
            host="host-1",
            description="Alpaca instruction format v2",
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT description FROM chat_templates WHERE id = ?", (template_id,)
            ).fetchone()
        assert row["description"] == "Alpaca instruction format v2"

    def test_register_duplicate_name_returns_existing_id_and_created_false(
        self, backend: SQLiteBackend
    ):
        """Second register with the same name returns (same_id, False) — upsert semantics."""
        first_id, first_created = backend.register_chat_template(
            "vicuna",
            source="builtin",
            host="host-1",
        )
        second_id, second_created = backend.register_chat_template(
            "vicuna",
            source="builtin",
            host="host-2",  # different host doesn't matter — name is unique key
        )
        assert first_id == second_id
        assert first_created is True
        assert second_created is False

    def test_register_duplicate_name_does_not_create_second_row(self, backend: SQLiteBackend):
        """After two register calls with the same name, only one row exists."""
        backend.register_chat_template("gemma", source="builtin", host="h1")
        backend.register_chat_template("gemma", source="builtin", host="h2")

        with backend._get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM chat_templates WHERE name = 'gemma'"
            ).fetchone()[0]
        assert count == 1

    def test_register_all_nullables_none_by_default(self, backend: SQLiteBackend):
        """jinja, model_id, description default to NULL when not supplied."""
        template_id, _ = backend.register_chat_template(
            "minimal-fmt",
            source="user",
            host="h1",
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT jinja, model_id, description FROM chat_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
        assert row["jinja"] is None
        assert row["model_id"] is None
        assert row["description"] is None


# ---------------------------------------------------------------------------
# list_chat_templates
# ---------------------------------------------------------------------------


class TestListChatTemplates:
    """list_chat_templates() -> list[dict] — returns all rows."""

    def test_list_returns_empty_when_no_templates(self, backend: SQLiteBackend):
        """With no registered templates, list returns []."""
        result = backend.list_chat_templates()
        assert result == []

    def test_list_returns_all_registered_rows(self, backend: SQLiteBackend):
        """After registering 3 templates, list returns 3 dicts."""
        for name in ("chatml", "llama3", "alpaca"):
            backend.register_chat_template(name, source="builtin", host="h1")

        result = backend.list_chat_templates()
        assert len(result) == 3

    def test_list_rows_are_dicts(self, backend: SQLiteBackend):
        """Each returned row is a dict (not a sqlite3.Row or tuple)."""
        backend.register_chat_template("vicuna", source="builtin", host="h1")
        result = backend.list_chat_templates()
        assert len(result) == 1
        row = result[0]
        assert isinstance(row, dict)

    def test_list_rows_contain_expected_keys(self, backend: SQLiteBackend):
        """Each row dict contains at least: id, name, source, host."""
        backend.register_chat_template("qwen", source="builtin", host="h1")
        result = backend.list_chat_templates()
        row = result[0]
        for key in ("id", "name", "source", "host"):
            assert key in row, f"Expected key '{key}' in row, got keys: {list(row.keys())}"

    def test_list_returns_correct_names(self, backend: SQLiteBackend):
        """Names in the listed rows match what was registered."""
        names = ("chatml", "llama3", "alpaca", "vicuna")
        for name in names:
            backend.register_chat_template(name, source="builtin", host="h1")

        result = backend.list_chat_templates()
        listed_names = {row["name"] for row in result}
        assert listed_names == set(names)

    def test_list_idempotent_on_duplicate_register(self, backend: SQLiteBackend):
        """Registering the same name twice doesn't produce duplicate list entries."""
        backend.register_chat_template("chatml", source="builtin", host="h1")
        backend.register_chat_template("chatml", source="builtin", host="h2")

        result = backend.list_chat_templates()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_chat_template_by_name
# ---------------------------------------------------------------------------


class TestGetChatTemplateByName:
    """get_chat_template_by_name(name) -> dict | None"""

    def test_get_returns_matching_row(self, backend: SQLiteBackend):
        """Fetching by registered name returns the row as a dict."""
        template_id, _ = backend.register_chat_template(
            "llama3",
            source="builtin",
            host="h1",
            description="Llama-3 instruct",
        )
        result = backend.get_chat_template_by_name("llama3")
        assert result is not None
        assert isinstance(result, dict)
        assert result["id"] == template_id
        assert result["name"] == "llama3"
        assert result["description"] == "Llama-3 instruct"

    def test_get_returns_none_for_missing_name(self, backend: SQLiteBackend):
        """Fetching an unknown name returns None (not raising, not empty dict)."""
        result = backend.get_chat_template_by_name("totally_unknown_template_xyz")
        assert result is None

    def test_get_returns_none_on_empty_table(self, backend: SQLiteBackend):
        """Fetching from an empty table returns None."""
        result = backend.get_chat_template_by_name("chatml")
        assert result is None

    def test_get_returns_correct_row_among_several(self, backend: SQLiteBackend):
        """When multiple templates exist, get returns only the requested one."""
        for name in ("chatml", "llama3", "alpaca"):
            backend.register_chat_template(name, source="builtin", host="h1")

        result = backend.get_chat_template_by_name("llama3")
        assert result is not None
        assert result["name"] == "llama3"

    def test_get_row_contains_expected_keys(self, backend: SQLiteBackend):
        """Returned dict contains at least: id, name, source, host."""
        backend.register_chat_template("gemma", source="builtin", host="h1")
        result = backend.get_chat_template_by_name("gemma")
        assert result is not None
        for key in ("id", "name", "source", "host"):
            assert key in result, f"Expected key '{key}' in result, got {list(result.keys())}"

    def test_get_returns_optional_fields_as_none_when_not_set(self, backend: SQLiteBackend):
        """jinja, model_id, description are None when not registered."""
        backend.register_chat_template("bare-fmt", source="user", host="h1")
        result = backend.get_chat_template_by_name("bare-fmt")
        assert result is not None
        assert result.get("jinja") is None
        assert result.get("model_id") is None
        assert result.get("description") is None

    def test_get_returns_jinja_when_set(self, backend: SQLiteBackend):
        """jinja field is returned correctly when it was registered."""
        jinja = "{% for m in messages %}{{ m['content'] }}\n{% endfor %}"
        backend.register_chat_template("custom", source="user", host="h1", jinja=jinja)
        result = backend.get_chat_template_by_name("custom")
        assert result is not None
        assert result["jinja"] == jinja
