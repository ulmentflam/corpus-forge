"""Phase L Wave 6 — redactor module (W6-02).

Covers every pattern the bug-report bundler relies on so the redaction
sweep is the load-bearing guarantee that no DSN / API key / bearer
token leaves the user's machine.

Idempotency is a hard contract: ``redact_string(redact_string(s)[0])[1]``
must be 0 — a second pass over already-redacted text replaces nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ─── redact_string — pattern coverage ─────────────────────────────────────


class TestRedactString:
    def test_empty_string_returns_zero(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        text, count = redact_string("")
        assert text == ""
        assert count == 0

    def test_innocuous_string_unchanged(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        plain = "Hello world, this is fine."
        text, count = redact_string(plain)
        assert text == plain
        assert count == 0

    def test_redacts_postgres_dsn_with_password(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "host: postgresql://admin:s3cr3t@db.internal:5432/corpus"
        text, count = redact_string(src)
        assert count >= 1
        assert "s3cr3t" not in text
        assert "«redacted»" in text

    def test_redacts_mysql_dsn(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "mysql://user:pw@host/db"
        text, count = redact_string(src)
        assert count >= 1
        assert "pw" not in text

    def test_redacts_openai_api_key(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "OPENAI_API_KEY=sk-abcdef1234567890ABCDEFGHI"
        text, count = redact_string(src)
        assert count >= 1
        assert "sk-abcdef1234567890ABCDEFGHI" not in text
        assert "«redacted»" in text

    def test_redacts_xai_api_key(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "xai-abcdef1234567890ABCDEFGHIJ"
        text, count = redact_string(src)
        assert count >= 1
        assert "xai-abcdef1234567890ABCDEFGHIJ" not in text

    def test_redacts_anthropic_api_key(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "claude-abcdef1234567890ABCDEFGHIJ"
        text, count = redact_string(src)
        assert count >= 1
        assert "claude-abcdef1234567890ABCDEFGHIJ" not in text

    def test_redacts_generic_password_assignment(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "password=hunter2"
        text, count = redact_string(src)
        assert count >= 1
        assert "hunter2" not in text

    def test_redacts_api_key_assignment_case_insensitive(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "API_KEY = MyTopSecretValue123"
        text, count = redact_string(src)
        assert count >= 1
        assert "MyTopSecretValue123" not in text

    def test_redacts_secret_colon_separator(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "secret: zzz-very-secret"
        text, count = redact_string(src)
        assert count >= 1
        assert "zzz-very-secret" not in text

    def test_redacts_bearer_token(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
        text, count = redact_string(src)
        assert count >= 1
        assert "eyJhbGciOiJIUzI1NiJ9.payload.signature" not in text

    def test_idempotent(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        src = "dsn = postgresql://u:p@h/db\napi_key=sk-abcdef1234567890ABCDEFGH\n"
        first_pass, first_count = redact_string(src)
        second_pass, second_count = redact_string(first_pass)
        assert first_pass == second_pass
        assert second_count == 0
        assert first_count >= 1

    def test_returns_tuple_of_str_and_int(self) -> None:
        from corpus_forge.diagnostics.redact import redact_string

        out = redact_string("hello")
        assert isinstance(out, tuple)
        assert isinstance(out[0], str)
        assert isinstance(out[1], int)


# ─── redact_toml_dict — key-name walk ─────────────────────────────────────


class TestRedactTomlDict:
    def test_redacts_dsn_key_value(self) -> None:
        import tomlkit

        from corpus_forge.diagnostics.redact import redact_toml_dict

        doc = tomlkit.parse('[backend]\nkind = "postgres"\ndsn = "postgresql://u:p@h/db"\n')
        new_doc, count = redact_toml_dict(doc)

        assert count >= 1
        rendered = tomlkit.dumps(new_doc)
        assert "postgresql://u:p@h/db" not in rendered
        assert "«redacted»" in rendered

    def test_redacts_password_key_even_when_value_is_plain(self) -> None:
        import tomlkit

        from corpus_forge.diagnostics.redact import redact_toml_dict

        doc = tomlkit.parse('[creds]\npassword = "plainvalue"\n')
        new_doc, count = redact_toml_dict(doc)

        assert count >= 1
        rendered = tomlkit.dumps(new_doc)
        assert "plainvalue" not in rendered

    def test_redacts_api_key_suffix_match(self) -> None:
        import tomlkit

        from corpus_forge.diagnostics.redact import redact_toml_dict

        doc = tomlkit.parse('[openai]\nopenai_api_key = "sk-abcdef1234567890ABCDEFGH"\n')
        new_doc, count = redact_toml_dict(doc)

        assert count >= 1
        rendered = tomlkit.dumps(new_doc)
        assert "sk-abcdef" not in rendered

    def test_redacts_token_key(self) -> None:
        import tomlkit

        from corpus_forge.diagnostics.redact import redact_toml_dict

        doc = tomlkit.parse('[hf]\nhf_token = "hf_abcdefghij"\n')
        new_doc, count = redact_toml_dict(doc)
        assert count >= 1
        rendered = tomlkit.dumps(new_doc)
        assert "hf_abcdefghij" not in rendered

    def test_redacts_secret_key(self) -> None:
        import tomlkit

        from corpus_forge.diagnostics.redact import redact_toml_dict

        doc = tomlkit.parse('[oauth]\nclient_secret = "topsecret"\n')
        new_doc, count = redact_toml_dict(doc)
        assert count >= 1
        rendered = tomlkit.dumps(new_doc)
        assert "topsecret" not in rendered

    def test_preserves_comments_and_order(self) -> None:
        import tomlkit

        from corpus_forge.diagnostics.redact import redact_toml_dict

        src = """# Top-level comment
[backend]
kind = "postgres"  # backend kind
dsn = "postgresql://u:p@h/db"

[embedders]
name = "qwen3"
"""
        doc = tomlkit.parse(src)
        new_doc, _ = redact_toml_dict(doc)
        rendered = tomlkit.dumps(new_doc)

        assert "# Top-level comment" in rendered
        # Backend block comes before embedders.
        assert rendered.index("[backend]") < rendered.index("[embedders]")

    def test_nested_table(self) -> None:
        import tomlkit

        from corpus_forge.diagnostics.redact import redact_toml_dict

        src = """[creds.primary]
password = "p1"

[[creds.alt]]
secret = "s1"
"""
        doc = tomlkit.parse(src)
        new_doc, count = redact_toml_dict(doc)
        rendered = tomlkit.dumps(new_doc)
        assert count >= 2
        assert "p1" not in rendered
        assert "s1" not in rendered

    def test_non_secret_keys_untouched(self) -> None:
        import tomlkit

        from corpus_forge.diagnostics.redact import redact_toml_dict

        doc = tomlkit.parse('[server]\nhost = "localhost"\nport = 5432\n')
        new_doc, count = redact_toml_dict(doc)
        assert count == 0
        rendered = tomlkit.dumps(new_doc)
        assert "localhost" in rendered
        assert "5432" in rendered


# ─── redact_file — atomic round-trip ─────────────────────────────────────


class TestRedactFile:
    def test_file_round_trip_redacts(self, tmp_path: Path) -> None:
        from corpus_forge.diagnostics.redact import redact_file

        src = tmp_path / "config.txt"
        src.write_text("dsn = postgresql://u:p@h/db\nplain = ok\n")

        count = redact_file(src)

        assert count >= 1
        new_text = src.read_text()
        assert "p@h" not in new_text
        assert "«redacted»" in new_text
        assert "plain = ok" in new_text  # untouched lines preserved

    def test_file_redact_is_idempotent_round_trip(self, tmp_path: Path) -> None:
        from corpus_forge.diagnostics.redact import redact_file

        src = tmp_path / "config.txt"
        src.write_text("api_key=sk-abcdefghij1234567890abc\n")

        first = redact_file(src)
        second = redact_file(src)
        assert first >= 1
        assert second == 0  # nothing left to redact

    def test_innocuous_file_unchanged(self, tmp_path: Path) -> None:
        from corpus_forge.diagnostics.redact import redact_file

        src = tmp_path / "ok.txt"
        original = "no secrets here, just words\n"
        src.write_text(original)

        count = redact_file(src)
        assert count == 0
        assert src.read_text() == original


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
