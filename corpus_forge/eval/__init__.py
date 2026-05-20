"""corpus_forge.eval — retrieval evaluation harness.

The eval harness is dual-use:

1. **Retrieval-quality validation**: compute NDCG / MRR / Recall against a
   bundled gold set (``corpus_forge/eval/datasets/forge_self.jsonl``) and
   gate CI on a pinned NDCG@10 baseline.

2. **Corpus-quality signal for training-data prep** (PRIMARY use): run the
   same harness over a user-provided held-out QA set
   (``corpus-forge eval corpus-quality --dataset <path>``).  Low recall@20
   = chunking regression = bad training signal — caught BEFORE export.

Public API:

- ``ndcg_at_k``, ``mrr_at_k``, ``recall_at_k`` — pure-NumPy metric funcs.
- ``GoldQuery`` + ``load_gold`` — JSONL gold-set loader.
- ``RetrievalMetrics`` (re-export from ``corpus_forge.retrieval.types``).

R3 adds ``evaluate_retriever`` and ``report`` once R3-04 lands.  The
``__init__`` will be extended at that point — public surface is stable.
"""

from corpus_forge.eval.cag import run_cag_eval
from corpus_forge.eval.dataset import GoldQuery, load_gold
from corpus_forge.eval.judge import JudgeClient, JudgeUnavailable
from corpus_forge.eval.judge_mock import score as judge_mock_score
from corpus_forge.eval.metrics import mrr_at_k, ndcg_at_k, recall_at_k
from corpus_forge.eval.rag import run_rag_eval
from corpus_forge.eval.runner import dump_json, evaluate_retriever, report
from corpus_forge.retrieval.types import RetrievalMetrics

__all__ = [
    "GoldQuery",
    "JudgeClient",
    "JudgeUnavailable",
    "RetrievalMetrics",
    "dump_json",
    "evaluate_retriever",
    "judge_mock_score",
    "load_gold",
    "mrr_at_k",
    "ndcg_at_k",
    "recall_at_k",
    "report",
    "run_cag_eval",
    "run_rag_eval",
]
