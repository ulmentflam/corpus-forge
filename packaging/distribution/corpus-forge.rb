# Phase I-12 — Homebrew formula scaffold for ``ulmentflam/homebrew-tap``.
#
# Deploy by copying this file to ``Formula/corpus-forge.rb`` in the
# tap repo (``https://github.com/ulmentflam/homebrew-tap``). On each
# corpus-forge release, update ``url`` to the GitHub tag URL and
# ``sha256`` to the matching wheel's SHA-256. A release-CI job can
# automate this with ``brew bump-formula-pr``.
#
# Install:
#   brew install ulmentflam/tap/corpus-forge
#
# Upgrade (corpus-forge ``update`` subcommand auto-detects the brew
# channel and runs this for you):
#   brew upgrade corpus-forge
class CorpusForge < Formula
  include Language::Python::Virtualenv

  desc "HF-format corpus + multi-embedder ingestion daemon for personal text and chat data"
  homepage "https://github.com/ulmentflam/corpus-forge"
  url "https://github.com/ulmentflam/corpus-forge/archive/refs/tags/v0.1.0b1.tar.gz"
  # Update this on each release. ``shasum -a 256 corpus-forge-<version>.tar.gz``
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "Apache-2.0"
  head "https://github.com/ulmentflam/corpus-forge.git", branch: "main"

  depends_on "python@3.12"
  depends_on "uv" => :recommended

  # Optional runtime deps unlocked by the ``[ocr]`` and ``[whisper]``
  # extras. Brew handles them as recommended depends_on so users can
  # opt out (``--without-poppler``) on a per-formula basis.
  depends_on "poppler" => :recommended
  depends_on "ffmpeg" => :recommended

  def install
    virtualenv_install_with_resources
  end

  test do
    # Smoke: --version prints a reasonable string.
    assert_match "corpus-forge version", shell_output("#{bin}/corpus-forge --version")
    # Smoke: doctor exits 0 on a fresh install (config will be WARN
    # but doctor's healthy invariant tolerates WARN for that check).
    system "#{bin}/corpus-forge", "doctor"
  end
end
