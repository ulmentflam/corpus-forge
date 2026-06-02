"""Source-URI -> absolute disk path resolution.

corpus-forge chunks carry a ``source_uri`` string that encodes the
plugin family and a logical path:

- ``filesystem://<root.name>/<rel>``  (generic ``filesystem`` plugin,
  emitted from :mod:`corpus_forge.sources.filesystem`).
- ``vault://<root.name>/<rel>``        (legacy ``markdown_vault``
  plugin, emitted from :mod:`corpus_forge.sources.markdown_vault`).
- ``claude-code://...``, ``chatgpt-export://...``, ``codex-cli://...``,
  ``gemini-cli://...``, ``jsonl-chat://...``, ``opencode://...``,
  ``zotero://...``, ``claude-code-history://...``, ``http(s)://...`` —
  no on-disk file in the simple sense; we return ``None``.

The resolver looks up the matching ``[[datasets.sources]]`` entry in
the user's :class:`~corpus_forge.config.Config`, walks every source's
``root`` (filesystem) or ``vault_root`` (markdown_vault), and matches
on the directory's basename — that's exactly what the source emits
into ``source_uri``.

The function never raises. It returns ``None`` for any URI it can't
resolve (malformed, unknown root name, no matching source).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import Config


# Schemes we know map to on-disk files via a configured filesystem-like source.
_FS_SCHEMES: tuple[str, ...] = ("filesystem://", "vault://")


def resolve_abs_path(source_uri: str | None, config: Config) -> Path | None:
    """Resolve ``source_uri`` to an absolute filesystem path, or ``None``.

    Behaviour:

    - ``filesystem://<root_name>/<rel>`` matches against every source
      with ``plugin == "filesystem"`` and ``Path(source.root).name ==
      <root_name>``. Returns ``(Path(source.root) / rel).resolve()``
      without requiring the file to exist on disk (so a moved/deleted
      file still yields a usable path the caller can show the user).
    - ``vault://<root_name>/<rel>`` matches the same way against
      ``plugin == "markdown_vault"`` and ``Path(source.vault_root).name
      == <root_name>``.
    - Any other URI scheme returns ``None``.
    - Malformed URIs, unknown root names, or sources with the matching
      plugin but mismatched ``.name`` return ``None``. The function
      never raises.

    When the URI has no rel component (e.g. ``filesystem://Notes/`` or
    ``filesystem://Notes``), the resolved root itself is returned.
    """
    if not source_uri or not isinstance(source_uri, str):
        return None
    # Fast-reject schemes we don't claim — `claude-code://`, `https://`, etc.
    if not source_uri.startswith(_FS_SCHEMES):
        return None

    # Pick the right scheme + field.
    if source_uri.startswith("filesystem://"):
        scheme_len = len("filesystem://")
        plugin_name = "filesystem"
        root_field = "root"
    elif source_uri.startswith("vault://"):
        scheme_len = len("vault://")
        plugin_name = "markdown_vault"
        root_field = "vault_root"
    else:
        return None

    rest = source_uri[scheme_len:]
    if not rest:
        return None  # ``filesystem://`` alone — nothing to match.

    # Split ``<root_name>/<rel>``. A trailing slash or missing slash both
    # yield an empty rel — the resolver returns the root itself.
    if "/" in rest:
        root_name, _, rel = rest.partition("/")
    else:
        root_name, rel = rest, ""

    if not root_name:
        return None

    datasets = getattr(config, "datasets", None) or []
    for ds in datasets:
        for src in getattr(ds, "sources", None) or []:
            if getattr(src, "plugin", None) != plugin_name:
                continue
            root_val = getattr(src, root_field, None)
            if not root_val:
                continue
            root_path = Path(root_val)
            if root_path.name != root_name:
                continue
            if rel:
                return (root_path / rel).resolve()
            return root_path.resolve()

    return None
