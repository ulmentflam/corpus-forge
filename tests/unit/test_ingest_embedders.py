"""Regression tests for get_active_embedders kwarg routing.

Bug: get_active_embedders() unconditionally passed api_key_env to registry.register()
for every provider. SentenceTransformersEmbedder.__init__ does not accept api_key_env,
so every ingest_once call that included a sentence-transformers embedder raised:

    TypeError: SentenceTransformersEmbedder.__init__() got an unexpected keyword argument
    'api_key_env'

The fix conditionally includes api_key_env only when provider == "openai".

These tests pin:
1. The sentence_transformers happy path — api_key_env must NOT reach registry.register.
2. The openai happy path — api_key_env MUST reach registry.register.
3. The multi-embedder case — each call gets exactly the right kwarg set independently.
4. The premise — SentenceTransformersEmbedder.__init__ really does reject api_key_env.
5. Integration via real registry + mocked SentenceTransformer constructor.
6. The active=False filter — inactive embedders are never registered.
"""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.embedders.sentence_transformers import SentenceTransformersEmbedder
from corpus_forge.ingest import get_active_embedders

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_BASE_KEYS = {
    "name",
    "provider",
    "model_id",
    "dimension",
    "normalized",
    "distance",
    "batch_size",
    "device",
}


def _make_st_config(name="st-embedder", active=True):
    """Build a minimal sentence_transformers EmbedderConfig mock."""
    cfg = MagicMock()
    cfg.name = name
    cfg.provider = "sentence_transformers"
    cfg.model_id = "BAAI/bge-small-en-v1.5"
    cfg.dimension = 384
    cfg.normalize = True
    cfg.distance = "cosine"
    cfg.active = active
    cfg.batch_size = 32
    cfg.device = "cpu"
    cfg.api_key_env = "OPENAI_API_KEY"  # present on config but must NOT be forwarded
    return cfg


def _make_openai_config(
    name="oai-embedder",
    api_key_env="OPENAI_API_KEY",
    active=True,
    base_url=None,
):
    """Build a minimal openai EmbedderConfig mock.

    ``base_url`` defaults to ``None`` (the "no local-substitution"
    case); passing a string forwards it to ``OpenAIEmbedder`` so the
    local-or-remote URL path can be asserted independently.
    """
    cfg = MagicMock()
    cfg.name = name
    cfg.provider = "openai"
    cfg.model_id = "text-embedding-3-small"
    cfg.dimension = 1536
    cfg.normalize = True
    cfg.distance = "cosine"
    cfg.active = active
    cfg.batch_size = 256
    cfg.device = "auto"
    cfg.api_key_env = api_key_env
    cfg.base_url = base_url
    return cfg


def _mock_config(*embedder_cfgs):
    """Wrap embedder config mocks in a minimal Config-like object."""

    class _MockConfig:
        embedders = list(embedder_cfgs)  # noqa: RUF012

    return _MockConfig()


# ---------------------------------------------------------------------------
# 1. Sentence-transformers happy path
# ---------------------------------------------------------------------------


class TestSentenceTransformersKwargs:
    """api_key_env must NOT appear in registry.register kwargs for sentence_transformers."""

    def test_register_called_without_api_key_env(self):
        """registry.register is called without api_key_env for sentence_transformers."""
        cfg = _make_st_config()

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="st-embedder")
            get_active_embedders(_mock_config(cfg))

        mock_register.assert_called_once()
        _, kwargs = mock_register.call_args
        assert "api_key_env" not in kwargs, (
            "api_key_env must NOT be forwarded to sentence_transformers registry.register"
        )

    def test_register_called_with_all_required_kwargs(self):
        """registry.register receives all expected kwargs for sentence_transformers."""
        cfg = _make_st_config()

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="st-embedder")
            get_active_embedders(_mock_config(cfg))

        _, kwargs = mock_register.call_args
        assert kwargs["name"] == "st-embedder"
        assert kwargs["provider"] == "sentence_transformers"
        assert kwargs["model_id"] == "BAAI/bge-small-en-v1.5"
        assert kwargs["dimension"] == 384
        assert kwargs["normalized"] is True
        assert kwargs["distance"] == "cosine"
        assert kwargs["batch_size"] == 32
        assert kwargs["device"] == "cpu"

    def test_register_kwarg_keys_are_exactly_base_set(self):
        """No unexpected extra keys sneak in for sentence_transformers."""
        cfg = _make_st_config()

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="st-embedder")
            get_active_embedders(_mock_config(cfg))

        _, kwargs = mock_register.call_args
        assert set(kwargs.keys()) == _EXPECTED_BASE_KEYS

    def test_does_not_raise_with_sentence_transformers_provider(self):
        """get_active_embedders does not raise TypeError for sentence_transformers."""
        cfg = _make_st_config()

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="st-embedder")
            # Must not raise — this is the regression guard
            result = get_active_embedders(_mock_config(cfg))

        assert len(result) == 1


# ---------------------------------------------------------------------------
# 2. OpenAI happy path
# ---------------------------------------------------------------------------


class TestOpenAIKwargs:
    """api_key_env MUST appear in registry.register kwargs for openai."""

    def test_register_called_with_api_key_env_default(self):
        """registry.register receives default OPENAI_API_KEY for openai provider."""
        cfg = _make_openai_config()

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="oai-embedder")
            get_active_embedders(_mock_config(cfg))

        _, kwargs = mock_register.call_args
        assert "api_key_env" in kwargs
        assert kwargs["api_key_env"] == "OPENAI_API_KEY"

    def test_register_called_with_custom_api_key_env(self):
        """registry.register forwards a custom api_key_env for openai."""
        cfg = _make_openai_config(api_key_env="MY_CUSTOM_KEY")

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="oai-embedder")
            get_active_embedders(_mock_config(cfg))

        _, kwargs = mock_register.call_args
        assert kwargs["api_key_env"] == "MY_CUSTOM_KEY"

    def test_register_called_with_all_required_kwargs(self):
        """registry.register receives all expected kwargs for openai."""
        cfg = _make_openai_config()

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="oai-embedder")
            get_active_embedders(_mock_config(cfg))

        _, kwargs = mock_register.call_args
        assert kwargs["name"] == "oai-embedder"
        assert kwargs["provider"] == "openai"
        assert kwargs["model_id"] == "text-embedding-3-small"
        assert kwargs["dimension"] == 1536
        assert kwargs["normalized"] is True
        assert kwargs["distance"] == "cosine"
        assert kwargs["batch_size"] == 256
        assert kwargs["device"] == "auto"

    def test_register_kwarg_keys_are_base_set_plus_api_key_env(self):
        """openai registers with exactly base keys + api_key_env."""
        cfg = _make_openai_config()

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="oai-embedder")
            get_active_embedders(_mock_config(cfg))

        _, kwargs = mock_register.call_args
        assert set(kwargs.keys()) == _EXPECTED_BASE_KEYS | {"api_key_env"}


# ---------------------------------------------------------------------------
# 3. Multi-embedder case
# ---------------------------------------------------------------------------


class TestMultiEmbedderKwargs:
    """Each embedder in a mixed config gets exactly its own kwarg set."""

    def test_two_calls_in_order_with_correct_kwargs(self):
        """Mixed config results in two ordered register calls with correct kwarg sets."""
        st_cfg = _make_st_config(name="st-first")
        oai_cfg = _make_openai_config(name="oai-second")

        mock_st_embedder = MagicMock()
        mock_st_embedder.name = "st-first"
        mock_oai_embedder = MagicMock()
        mock_oai_embedder.name = "oai-second"

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.side_effect = [mock_st_embedder, mock_oai_embedder]
            result = get_active_embedders(_mock_config(st_cfg, oai_cfg))

        assert mock_register.call_count == 2
        st_call, oai_call = mock_register.call_args_list

        # sentence_transformers call must not have api_key_env
        _, st_kwargs = st_call
        assert "api_key_env" not in st_kwargs
        assert st_kwargs["name"] == "st-first"
        assert st_kwargs["provider"] == "sentence_transformers"

        # openai call must have api_key_env
        _, oai_kwargs = oai_call
        assert "api_key_env" in oai_kwargs
        assert oai_kwargs["name"] == "oai-second"
        assert oai_kwargs["provider"] == "openai"

        # Result list preserves order
        assert result[0].name == "st-first"
        assert result[1].name == "oai-second"

    def test_reversed_order_still_correct(self):
        """openai-first, sentence_transformers-second — each still gets the right kwargs."""
        oai_cfg = _make_openai_config(name="oai-first")
        st_cfg = _make_st_config(name="st-second")

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock()
            get_active_embedders(_mock_config(oai_cfg, st_cfg))

        oai_call, st_call = mock_register.call_args_list

        _, oai_kwargs = oai_call
        assert "api_key_env" in oai_kwargs

        _, st_kwargs = st_call
        assert "api_key_env" not in st_kwargs

    def test_two_sentence_transformers_embedders(self):
        """Two sentence_transformers embedders: neither call gets api_key_env."""
        st1 = _make_st_config(name="st-alpha")
        st2 = _make_st_config(name="st-beta")

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock()
            get_active_embedders(_mock_config(st1, st2))

        assert mock_register.call_count == 2
        for c in mock_register.call_args_list:
            _, kwargs = c
            assert "api_key_env" not in kwargs, (
                f"api_key_env leaked into sentence_transformers call: {kwargs}"
            )

    def test_two_openai_embedders_both_get_api_key_env(self):
        """Two openai embedders: both calls get api_key_env."""
        oai1 = _make_openai_config(name="oai-alpha", api_key_env="KEY_A")
        oai2 = _make_openai_config(name="oai-beta", api_key_env="KEY_B")

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock()
            get_active_embedders(_mock_config(oai1, oai2))

        call_a, call_b = mock_register.call_args_list
        _, kwargs_a = call_a
        _, kwargs_b = call_b
        assert kwargs_a["api_key_env"] == "KEY_A"
        assert kwargs_b["api_key_env"] == "KEY_B"


# ---------------------------------------------------------------------------
# 4. Premise pin — SentenceTransformersEmbedder rejects api_key_env
# ---------------------------------------------------------------------------


class TestSentenceTransformersConstructorContract:
    """Pin the underlying contract that makes the bug matter.

    If SentenceTransformersEmbedder ever gains an api_key_env parameter the
    kwarg routing fix becomes unnecessary — but until then it must reject it.
    """

    def test_raises_type_error_when_api_key_env_passed(self):
        """Directly instantiating SentenceTransformersEmbedder with api_key_env raises."""
        with pytest.raises(TypeError, match="api_key_env"):
            SentenceTransformersEmbedder(
                name="should-fail",
                model_id="BAAI/bge-small-en-v1.5",
                dimension=384,
                api_key_env="OPENAI_API_KEY",
            )

    def test_succeeds_without_api_key_env(self):
        """Directly instantiating SentenceTransformersEmbedder without api_key_env succeeds."""
        embedder = SentenceTransformersEmbedder(
            name="should-succeed",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        assert embedder.name == "should-succeed"
        assert embedder.provider == "sentence_transformers"
        assert embedder.dimension == 384


# ---------------------------------------------------------------------------
# 5. Integration via real registry with mocked SentenceTransformer constructor
# ---------------------------------------------------------------------------


class TestRealRegistryWithMockedModel:
    """Instantiate through the real registry (not a registry mock) to catch
    kwarg routing bugs that only surface when the real constructor runs.

    This is the closest mirror of the original bug scenario.
    """

    def test_real_registry_register_st_does_not_raise(self):
        """registry.register with sentence_transformers succeeds via real registry.

        The SentenceTransformer model loader is patched to prevent any network
        call or disk access; only the constructor kwarg routing is exercised.
        """
        from corpus_forge.embedders.registry import EmbedderRegistry

        local_registry = EmbedderRegistry()
        st_cfg = _make_st_config(name="integration-st")

        # Patch SentenceTransformer at the module level used by the embedder
        with patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer"):
            # Must NOT raise TypeError — this is exactly the regression scenario
            embedder = local_registry.register(
                name=st_cfg.name,
                provider=st_cfg.provider,
                model_id=st_cfg.model_id,
                dimension=st_cfg.dimension,
                normalized=st_cfg.normalize,
                distance=st_cfg.distance,
                batch_size=st_cfg.batch_size,
                device=st_cfg.device,
                # api_key_env intentionally absent
            )

        assert isinstance(embedder, SentenceTransformersEmbedder)
        assert embedder.name == "integration-st"

    def test_real_registry_register_st_raises_if_api_key_env_passed(self):
        """registry.register with sentence_transformers raises if api_key_env sneaks in.

        This test encodes the exact TypeError the bug produced and confirms
        the real registry + constructor rejects it.
        """
        from corpus_forge.embedders.registry import EmbedderRegistry

        local_registry = EmbedderRegistry()

        with (
            patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer"),
            pytest.raises(TypeError, match="api_key_env"),
        ):
            local_registry.register(
                name="bug-reproduction",
                provider="sentence_transformers",
                model_id="BAAI/bge-small-en-v1.5",
                dimension=384,
                normalized=True,
                distance="cosine",
                batch_size=32,
                device="cpu",
                api_key_env="OPENAI_API_KEY",  # the bug: this was always passed before fix
            )

    def test_get_active_embedders_via_real_registry_does_not_raise(self):
        """get_active_embedders with a real registry and mocked SentenceTransformer succeeds.

        Patches corpus_forge.ingest.registry with a fresh EmbedderRegistry so
        we exercise the full call chain without a mock double replacing register().
        """
        from corpus_forge.embedders.registry import EmbedderRegistry

        st_cfg = _make_st_config(name="end-to-end-st")

        fresh_registry = EmbedderRegistry()

        with (
            patch("corpus_forge.ingest.registry", fresh_registry),
            patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer"),
        ):
            result = get_active_embedders(_mock_config(st_cfg))

        assert len(result) == 1
        assert isinstance(result[0], SentenceTransformersEmbedder)
        assert result[0].name == "end-to-end-st"


# ---------------------------------------------------------------------------
# 6. active=False filter
# ---------------------------------------------------------------------------


class TestActiveFilter:
    """Inactive embedders must be skipped entirely."""

    def test_inactive_embedder_skipped(self):
        """registry.register is never called for an inactive embedder."""
        cfg = _make_st_config(active=False)

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            result = get_active_embedders(_mock_config(cfg))

        mock_register.assert_not_called()
        assert result == []

    def test_mixed_active_inactive_only_active_registered(self):
        """Only the active embedder is registered when mixed with inactive."""
        active_cfg = _make_st_config(name="active-one", active=True)
        inactive_cfg = _make_openai_config(name="inactive-oai", active=False)

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            mock_register.return_value = MagicMock(name="active-one")
            result = get_active_embedders(_mock_config(active_cfg, inactive_cfg))

        mock_register.assert_called_once()
        _, kwargs = mock_register.call_args
        assert kwargs["name"] == "active-one"
        assert len(result) == 1

    def test_all_inactive_returns_empty_list(self):
        """Empty list returned when all embedders are inactive."""
        cfg1 = _make_st_config(active=False)
        cfg2 = _make_openai_config(active=False)

        with patch("corpus_forge.ingest.registry.register") as mock_register:
            result = get_active_embedders(_mock_config(cfg1, cfg2))

        mock_register.assert_not_called()
        assert result == []
