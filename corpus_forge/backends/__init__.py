"""corpus_forge.backends — storage backend package.

Re-exports the public exception class so callers can do either:
    from corpus_forge.backends import IngestRunInProgressError
or:
    from corpus_forge.backends.base import IngestRunInProgressError
"""

from .base import IngestRunInProgressError

__all__ = ["IngestRunInProgressError"]
