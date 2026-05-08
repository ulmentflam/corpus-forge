"""SyncEngine — orchestrates PushPipeline and PullPipeline."""

import logging

from corpus_forge.sync.echo import EchoSuppressor
from corpus_forge.sync.pull import PullPipeline
from corpus_forge.sync.push import PushPipeline

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(
        self,
        dataset_config,
        source,
        backend,
        embedders,
        host_id: str,
        daemon_config,
    ) -> None:
        self._dataset_config = dataset_config
        self._source = source
        self._backend = backend
        self._embedders = embedders
        self._host_id = host_id
        self._daemon_config = daemon_config
        self._echo_suppressor = EchoSuppressor()
        self._push_pipeline = None
        self._pull_pipeline = None

    def start(self) -> None:
        echo = self._echo_suppressor
        push = PushPipeline(
            self._backend, self._dataset_config.id, echo, self._host_id
        )
        push.start(
            source_root=self._source.root,
            exclude_globs=self._dataset_config.exclude_globs or [],
            debounce_seconds=self._daemon_config.debounce_seconds,
        )
        self._push_pipeline = push

        pull = PullPipeline(
            self._backend,
            self._dataset_config.id,
            self._source.root,
            echo,
            self._host_id,
        )
        pull.start(
            source_root=self._source.root,
            poll_interval_s=self._daemon_config.sync_poll_interval_s,
        )
        self._pull_pipeline = pull

    def stop(self) -> None:
        if self._push_pipeline is not None:
            self._push_pipeline.stop()
        if self._pull_pipeline is not None:
            self._pull_pipeline.stop()
        self._backend.upsert_document(self._dataset_config.id, None, [])
