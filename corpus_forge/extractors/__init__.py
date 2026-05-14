"""File-format extractor package.

Phase D milestone. Public surface intentionally narrow — most concrete
extractors are import-on-demand to keep the optional-dep matrix sane.
"""

from .base import ChunkerHint, ExtractedDocument, Extractor
from .registry import ExtractorRegistry, register_default_extractors

__all__ = [
    "ChunkerHint",
    "ExtractedDocument",
    "Extractor",
    "ExtractorRegistry",
    "register_default_extractors",
]
