"""corpus_forge.cag — Cache-Augmented Generation (CAG) support.

Provides a hybrid selector that routes queries either to a precomputed disk
cache (cache hit) or to a live retriever (cache miss), plus the build-side
utilities for producing those cache files.

Phase P Wave 3.  See `.planning/tdd/phase_o_eda_cleaning.md` § Wave P3.
"""

from corpus_forge.cag.cache import (
    build_cache,
    cache_key,
    cache_path,
    invalidate,
    invalidate_for_chunk,
    list_cached_keys,
)

__all__ = [
    "build_cache",
    "cache_key",
    "cache_path",
    "invalidate",
    "invalidate_for_chunk",
    "list_cached_keys",
]
