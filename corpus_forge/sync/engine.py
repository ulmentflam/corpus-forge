"""SyncEngine — orchestrates PushPipeline and PullPipeline."""

import logging
from pathlib import Path

from corpus_forge.sync.echo import EchoSuppressor
from corpus_forge.sync.pull import PullPipeline
from corpus_forge.sync.push import PushPipeline

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        dataset_id: int,
        dataset_config,
        source,
        backend,
        embedders,
        host_id: str,
        daemon_config,
        source_root: Path | None = None,
        discovery_callback=None,
        source_uri_prefix: str = "",
    ) -> None:
        # ``dataset_id`` is the backend row id, resolved by the caller
        # (typically ``run_daemon``) via
        # ``backend.find_dataset_id_by_name(name)``.  ``DatasetConfig``
        # (the Pydantic config object) carries no ``id`` — the id
        # lives only on the backend's ``corpus.datasets`` row.
        self._dataset_id = dataset_id
        self._dataset_config = dataset_config
        self._source = source
        self._backend = backend
        self._embedders = embedders
        self._host_id = host_id
        self._daemon_config = daemon_config
        # ``discovery_callback`` is forwarded to PushPipeline so the
        # watchdog handler routes brand-new files into the per-file
        # ingest path.  PullPipeline does not need it — pulls are
        # driven off revisions a remote host already pushed.
        self._discovery_callback = discovery_callback
        # ``source_uri_prefix`` aligns PushPipeline's lookup URI with
        # the value ``Source.parse`` writes into ``corpus.documents``
        # (e.g. ``filesystem://Workspace/``).  Without it,
        # ``find_document`` never matches and modifications fall
        # through to discovery every time, paying the full embedder
        # cost on every save.
        self._source_uri_prefix = source_uri_prefix
        self._echo_suppressor = EchoSuppressor()
        self._push_pipeline = None
        self._pull_pipeline = None

        # ``source_root`` is plugin-aware: the on-disk path lives on
        # ``source.root`` for ``filesystem`` and ``source.vault_root``
        # for ``markdown_vault``.  When ``run_daemon`` passes it
        # explicitly we use that; otherwise we fall back to
        # ``source.root`` for the legacy duck-typed shape used by the
        # cross-host integration tests (which mock the source object).
        self._source_root: Path = (
            Path(source_root) if source_root is not None else Path(source.root)
        )

    def start(self) -> None:
        echo = self._echo_suppressor
        push = PushPipeline(
            self._backend,
            self._dataset_id,
            echo,
            self._host_id,
            discovery_callback=self._discovery_callback,
        )
        push.start(
            source_root=self._source_root,
            # ``exclude_globs`` is a field on ``DatasetSourceConfig``,
            # not ``DatasetConfig`` — each source defines its own
            # exclusion patterns.  Empty list when the source has none.
            exclude_globs=getattr(self._source, "exclude_globs", None) or [],
            debounce_seconds=self._daemon_config.debounce_seconds,
            source_uri_prefix=self._source_uri_prefix,
        )
        self._push_pipeline = push

        pull = PullPipeline(
            self._backend,
            self._dataset_id,
            self._source_root,
            echo,
            self._host_id,
        )
        pull.start(
            source_root=self._source_root,
            poll_interval_s=self._daemon_config.sync_poll_interval_s,
        )
        self._pull_pipeline = pull

    def stop(self) -> None:
        if self._push_pipeline is not None:
            self._push_pipeline.stop()
        if self._pull_pipeline is not None:
            self._pull_pipeline.stop()
