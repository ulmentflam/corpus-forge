"""RFC fleet-3 item 1 — federation config scope: extract + merge.

Three test families:

- **Deny-list (structural)**: walks every pydantic model reachable
  from :class:`Config` and asserts no field whose *name* is
  path-shaped or secret-shaped carries the ``scope: shared`` mark.
  Written over the annotation mechanism itself — a future field named
  ``foo_api_key_env`` marked shared fails this test the day it's
  added, with no test edit required.
- **Extraction**: the shared dict contains exactly the corpus-shaped
  subset (dataset name/kind, embedder definitions, retrieval
  settings, model choices) and never a local value (DSN, roots,
  devices, URLs, key-env names).
- **Merge round-trip**: ``merge_shared_scope`` rewrites only
  shared-scope keys in a heavily-commented TOML, preserving every
  comment and all local values.
"""

from __future__ import annotations

import re

import tomlkit
from pydantic import BaseModel

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)
from corpus_forge.config_scope import (
    field_is_shared,
    merge_shared_scope,
    shared_scope_dict,
)

# ─── fixtures ───────────────────────────────────────────────────────────


def make_config() -> Config:
    return Config(
        backend=BackendConfig(kind="postgres", dsn="postgresql://secret-host/corpus"),
        daemon=DaemonConfig(host_id="test-host"),
        datasets=[
            DatasetConfig(
                name="notes",
                kind="text",
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault",
                        vault_root="~/Notes",
                        chunker="markdown",
                    )
                ],
            ),
            DatasetConfig(
                name="chats",
                kind="chat",
                sources=[
                    DatasetSourceConfig(
                        plugin="claude_code",
                        projects_root="~/.claude/projects",
                        chunker="conversation",
                    )
                ],
            ),
        ],
        embedders=[
            EmbedderConfig(
                name="qwen3-4096",
                provider="openai",
                model_id="qwen3-embedding",
                dimension=4096,
                device="cuda:0",
                api_key_env="MY_PROVIDER_KEY",
                extensions=[".py", ".ts"],
            ),
            EmbedderConfig(
                name="nomic-code",
                provider="sentence_transformers",
                model_id="nomic-ai/nomic-embed-code",
                dimension=768,
                device="mps",
            ),
        ],
    )


# ─── deny-list (structural, over the annotation mechanism) ─────────────

# Path-shaped or secret-shaped *field names* that must never be marked
# shared. Matching the RFC non-goal: api_key_env *names* are local;
# values never exist in config at all.
_DENY_RE = re.compile(
    r"(root|path|dir|dsn|file|url|device|key|token|secret|password|env|lanes)",
    re.IGNORECASE,
)


def _walk_models(model_cls: type[BaseModel], seen: set[type[BaseModel]]) -> set[type[BaseModel]]:
    if model_cls in seen:
        return seen
    seen.add(model_cls)
    for field in model_cls.model_fields.values():
        annotation = field.annotation
        # Collect BaseModel classes from the annotation and its args
        # (Optional[X], list[X], unions).
        candidates = [annotation, *getattr(annotation, "__args__", ())]
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                _walk_models(candidate, seen)
    return seen


class TestDenyList:
    def test_no_path_or_secret_shaped_field_is_shared(self) -> None:
        for model_cls in _walk_models(Config, set()):
            for name, field in model_cls.model_fields.items():
                if field_is_shared(field):
                    assert not _DENY_RE.search(name), (
                        f"{model_cls.__name__}.{name} is marked scope=shared but its "
                        "name is path-shaped or secret-shaped; fleet-shared config "
                        "must never carry machine paths, endpoints, or key material."
                    )

    def test_no_extracted_leaf_key_is_deny_shaped(self) -> None:
        """Same invariant over the actual extraction output."""

        def walk(node: object, path: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    assert not _DENY_RE.search(str(key)), f"deny-shaped key at {path}.{key}"
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{path}[{i}]")

        walk(shared_scope_dict(make_config()), "$")

    def test_unannotated_fields_default_to_local(self) -> None:
        """The mechanism is private-by-default: no annotation → local."""
        shared = shared_scope_dict(make_config())
        assert "backend" not in shared  # dsn lives here
        assert "daemon" not in shared  # host_id, trash_dir live here
        flat = str(shared)
        assert "secret-host" not in flat
        assert "MY_PROVIDER_KEY" not in flat
        assert "~/Notes" not in flat
        assert "cuda:0" not in flat


# ─── extraction ─────────────────────────────────────────────────────────


class TestSharedScopeDict:
    def test_dataset_name_and_kind_shared_sources_local(self) -> None:
        shared = shared_scope_dict(make_config())
        assert shared["datasets"] == [
            {"name": "notes", "kind": "text"},
            {"name": "chats", "kind": "chat"},
        ]

    def test_embedder_definition_shared_machine_knobs_local(self) -> None:
        shared = shared_scope_dict(make_config())
        embedders = shared["embedders"]
        assert isinstance(embedders, list)
        qwen = embedders[0]
        assert qwen == {
            "name": "qwen3-4096",
            "provider": "openai",
            "model_id": "qwen3-embedding",
            "dimension": 4096,
            "normalize": True,
            "distance": "cosine",
            "active": True,
            "extensions": [".py", ".ts"],
            # RFC fleet-6: the model-identity alias set is shared scope (the
            # fleet must agree on identity); empty here, federated in item 5.
            "model_aliases": [],
        }

    def test_model_aliases_federate_as_shared_scope(self) -> None:
        """RFC fleet-6 item 5: a non-empty ``model_aliases`` set is shared
        scope, so `config publish` propagates it and every host agrees on the
        model identity. (The empty-default case is covered above; this proves
        declared aliases actually federate.)"""
        from corpus_forge.config import ModelAlias

        cfg = make_config()
        cfg.embedders[0].model_aliases = [ModelAlias(provider="llama-cpp", model_id="nomic-code")]
        shared = shared_scope_dict(cfg)
        assert shared["embedders"][0]["model_aliases"] == [
            {"provider": "llama-cpp", "model_id": "nomic-code"}
        ]

    def test_retrieval_settings_shared_reranker_split(self) -> None:
        shared = shared_scope_dict(make_config())
        retrieval = shared["retrieval"]
        assert isinstance(retrieval, dict)
        assert retrieval["fusion"] == "rrf"
        assert retrieval["default_k"] == 10
        reranker = retrieval["reranker"]
        assert isinstance(reranker, dict)
        assert reranker["model_id"] == "BAAI/bge-reranker-v2-m3"
        assert "device" not in reranker
        assert "batch_size" not in reranker

    def test_model_choices_shared_endpoints_local(self) -> None:
        shared = shared_scope_dict(make_config())
        classifier = shared["classifier"]
        assert isinstance(classifier, dict)
        assert classifier["llm_model"] == "qwen2.5:7b-instruct"
        assert classifier["chain"] == ["rule", "llm"]
        assert "llm_url" not in classifier
        assert "llm_api_key_env" not in classifier
        assert "llm_timeout_s" not in classifier

    def test_values_are_toml_representable(self) -> None:
        """The dict must serialize as TOML — that's the storage shape."""
        text = tomlkit.dumps(shared_scope_dict(make_config()))
        assert "qwen3-4096" in text


# ─── merge round-trip ───────────────────────────────────────────────────

_LOCAL_TOML = """\
# corpus-forge — workstation config (hand-tuned, do not lose comments!)

[backend]
kind = "postgres"
dsn = "postgresql://localhost/corpus"  # local socket, NOT fleet-shared
schema = "corpus"

[daemon]
host_id = "workstation"  # this machine's identity

[[datasets]]
name = "notes"
kind = "text"  # will converge with the fleet

[[datasets.sources]]
plugin = "markdown_vault"
vault_root = "~/Notes"  # MY directory layout — never touched by pull
chunker = "markdown"

[[embedders]]
name = "qwen3-4096"
provider = "openai"
model_id = "stale-model-id"  # fleet will overwrite this
dimension = 4096
device = "mps"  # Apple Silicon here, CUDA on the big box
api_key_env = "MY_PROVIDER_KEY"

[retrieval]
alpha = 0.9  # stale local experiment, fleet says 0.5
default_k = 10
"""


class TestMergeSharedScope:
    def test_only_shared_keys_change_and_comments_survive(self) -> None:
        shared = shared_scope_dict(make_config())
        merged = merge_shared_scope(_LOCAL_TOML, shared)

        # Every comment survives the rewrite.
        for comment in (
            "do not lose comments!",
            "local socket, NOT fleet-shared",
            "this machine's identity",
            "MY directory layout — never touched by pull",
            "Apple Silicon here, CUDA on the big box",
        ):
            assert comment in merged

        doc = tomlkit.parse(merged)
        # Local scope untouched.
        assert doc["backend"]["dsn"] == "postgresql://localhost/corpus"
        assert doc["daemon"]["host_id"] == "workstation"
        assert doc["datasets"][0]["sources"][0]["vault_root"] == "~/Notes"
        assert doc["embedders"][0]["device"] == "mps"
        assert doc["embedders"][0]["api_key_env"] == "MY_PROVIDER_KEY"
        # Shared scope converged.
        assert doc["embedders"][0]["model_id"] == "qwen3-embedding"
        assert doc["retrieval"]["alpha"] == 0.5

    def test_missing_shared_items_are_added(self) -> None:
        shared = shared_scope_dict(make_config())
        merged = merge_shared_scope(_LOCAL_TOML, shared)
        doc = tomlkit.parse(merged)

        # The fleet knows a second dataset and a second embedder this
        # host has never seen; both arrive.
        dataset_names = [d["name"] for d in doc["datasets"]]
        assert dataset_names == ["notes", "chats"]
        embedder_names = [e["name"] for e in doc["embedders"]]
        assert embedder_names == ["qwen3-4096", "nomic-code"]
        # The appended embedder carries the shared definition only —
        # no device, no key-env name.
        nomic = doc["embedders"][1]
        assert nomic["dimension"] == 768
        assert "device" not in nomic
        assert "api_key_env" not in nomic
        # Retrieval table was absent some keys; they were added.
        assert doc["retrieval"]["fusion"] == "rrf"

    def test_merge_into_empty_document(self) -> None:
        shared = shared_scope_dict(make_config())
        merged = merge_shared_scope("# fresh machine\n", shared)
        assert "# fresh machine" in merged
        doc = tomlkit.parse(merged)
        assert [d["name"] for d in doc["datasets"]] == ["notes", "chats"]

    def test_merge_is_idempotent(self) -> None:
        shared = shared_scope_dict(make_config())
        once = merge_shared_scope(_LOCAL_TOML, shared)
        twice = merge_shared_scope(once, shared)
        assert once == twice

    def test_inline_empty_array_placeholder_becomes_table_arrays(self) -> None:
        """Issue #120: ``setup --join`` renders an inline ``datasets = []``
        placeholder, which tomlkit parses as an ``Array`` (a ``list``
        subclass that is NOT an ``AoT``). The old guard let it through and
        appended full tables into the inline array, dumping unparseable
        TOML. The merge must rebuild it as proper ``[[datasets]]`` blocks.
        """
        local = "datasets = []\n"
        shared = shared_scope_dict(make_config())
        merged = merge_shared_scope(local, shared)

        # The whole point: the result must re-parse (the bug produced
        # unparseable TOML), and render as table-array blocks, not an inline
        # array of bare key-values.
        doc = tomlkit.parse(merged)
        assert [d["name"] for d in doc["datasets"]] == ["notes", "chats"]
        assert "[[datasets]]" in merged
        # Defensive: no inline-array remnant of the placeholder survived.
        assert "datasets = [" not in merged

    def test_inline_array_with_existing_inline_tables_is_preserved(self) -> None:
        """A non-empty inline array of inline tables is also rebuilt as
        table-arrays without dropping the local item — the merge carries the
        existing inline-table data over before applying shared scope."""
        local = 'datasets = [{ name = "notes", kind = "text" }]\n'
        shared = shared_scope_dict(make_config())
        merged = merge_shared_scope(local, shared)

        doc = tomlkit.parse(merged)  # must parse
        names = [d["name"] for d in doc["datasets"]]
        # The pre-existing 'notes' item is preserved (merged by name), and
        # the shared-only 'chats' is appended.
        assert names == ["notes", "chats"]
        assert "[[datasets]]" in merged


# ─── hard backcompat bar ────────────────────────────────────────────────


class TestBackcompat:
    def test_annotations_do_not_change_validation(self) -> None:
        """Annotated models validate exactly as before — pure metadata."""
        config = make_config()
        assert config.datasets[0].name == "notes"
        assert config.embedders[0].dimension == 4096

    def test_scope_metadata_invisible_in_dump(self) -> None:
        """``model_dump`` output carries values only — no scope marks."""

        def walk(node: object) -> None:
            if isinstance(node, dict):
                assert "scope" not in node or node.get("scope") == "corpus", (
                    "scope annotation leaked into a dumped value"
                )
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(make_config().model_dump(mode="json"))

    def test_field_constraints_still_enforced(self) -> None:
        """Validators wrapped alongside annotations still fire."""
        import pytest

        with pytest.raises(ValueError):
            EmbedderConfig(
                name="bad",
                provider="not_a_provider",
                model_id="x",
                dimension=8,
            )
