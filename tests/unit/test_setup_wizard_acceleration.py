"""Setup-wizard integration tests for the auto-accelerator embedder lane.

When the user picks ``embedder = "auto"`` (the new default), the
wizard calls ``recommend_embedder_preset(detect_accelerator())`` and
emits the matching ``[[embedders]]`` block.  ``st`` / ``openai`` /
``both`` stay as opt-out paths for manual control.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from corpus_forge.acceleration import Accelerator, AcceleratorInfo
from corpus_forge.setup.wizard import render_config_toml


def _answers(embedder: str = "auto", backend: str = "sqlite") -> dict[str, str]:
    return {
        "backend": backend,
        "embedder": embedder,
    }


def _stub(info: AcceleratorInfo):
    return patch("corpus_forge.setup.wizard.detect_accelerator", return_value=info)


class TestAutoEmbedderLane:
    def test_cuda_emits_llama_cpp_block_with_full_offload(self, tmp_path: Path):
        info = AcceleratorInfo(
            kind=Accelerator.CUDA,
            device_name="NVIDIA RTX 4090",
            vram_mb=24576,
        )
        with _stub(info):
            toml = render_config_toml(_answers("auto"), tmp_path / "db.sqlite")
        # The auto lane lands a single llama-cpp embedder block with
        # full GPU offload — no sentence_transformers + openai block.
        assert "[[embedders]]" in toml
        assert 'provider   = "llama-cpp"' in toml
        assert 'model_id   = "qwen3-embedding:8b"' in toml
        assert "n_gpu_layers = -1" in toml
        assert "sentence_transformers" not in toml

    def test_cuda_low_vram_emits_smaller_model(self, tmp_path: Path):
        info = AcceleratorInfo(
            kind=Accelerator.CUDA,
            device_name="NVIDIA GTX 1060 6GB",
            vram_mb=6144,
        )
        with _stub(info):
            toml = render_config_toml(_answers("auto"), tmp_path / "db.sqlite")
        assert 'model_id   = "nomic-embed-text"' in toml
        assert "dimension  = 768" in toml
        assert "n_gpu_layers = -1" in toml  # GPU still offloads

    def test_mps_emits_metal_offload(self, tmp_path: Path):
        with _stub(AcceleratorInfo(kind=Accelerator.MPS)):
            toml = render_config_toml(_answers("auto"), tmp_path / "db.sqlite")
        assert 'provider   = "llama-cpp"' in toml
        assert "n_gpu_layers = -1" in toml
        assert 'model_id   = "qwen3-embedding:8b"' in toml

    def test_cpu_emits_cpu_lane(self, tmp_path: Path):
        with _stub(AcceleratorInfo(kind=Accelerator.CPU)):
            toml = render_config_toml(_answers("auto"), tmp_path / "db.sqlite")
        assert 'provider   = "llama-cpp"' in toml
        assert "n_gpu_layers = 0" in toml
        assert 'model_id   = "nomic-embed-text"' in toml

    def test_legacy_st_choice_still_renders_sentence_transformers(self, tmp_path: Path):
        """``embedder=st`` opt-out path keeps the legacy block.

        Users who already have a working sentence-transformers setup
        shouldn't be forced into the auto lane on a wizard re-run.
        """
        # Should NOT call detect_accelerator on legacy paths.
        with patch("corpus_forge.setup.wizard.detect_accelerator") as mock_detect:
            toml = render_config_toml(_answers("st"), tmp_path / "db.sqlite")
            mock_detect.assert_not_called()
        assert 'provider  = "sentence_transformers"' in toml
        assert "llama-cpp" not in toml

    def test_legacy_openai_choice_still_renders_openai(self, tmp_path: Path):
        with patch("corpus_forge.setup.wizard.detect_accelerator") as mock_detect:
            toml = render_config_toml(
                {**_answers("openai"), "openai_api_key_env": "OPENAI_API_KEY"},
                tmp_path / "db.sqlite",
            )
            mock_detect.assert_not_called()
        assert 'provider  = "openai"' in toml
        assert "llama-cpp" not in toml


class TestQuestionsToml:
    """The questions.toml file MUST advertise the new ``auto`` choice
    as the wizard's default; otherwise the install.sh + ps1 shells
    won't know it exists."""

    def test_auto_is_a_choice_and_default(self):
        import tomllib
        from pathlib import Path as _Path

        path = _Path(__file__).resolve().parents[2] / "corpus_forge" / "setup" / "questions.toml"
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        questions = {q["id"]: q for q in data["question"]}
        embedder_q = questions["embedder"]
        assert "auto" in embedder_q["choices"]
        assert embedder_q["default"] == "auto"
