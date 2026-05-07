"""HuggingFace export functionality for corpus-forge."""

import os

try:
    from datasets import Dataset

    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False


def export_to_hf_dataset(
    view_name: str = "corpus_text_export", database_url: str | None = None
) -> Dataset | None:
    """
    Export a corpus view as a HuggingFace Dataset.

    Args:
        view_name: Name of the database view to export
            (e.g., 'corpus_text_export', 'corpus_chat_export')
        database_url: PostgreSQL connection string.
            If None, uses DATABASE_URL env var.

    Returns:
        HuggingFace Dataset object or None if HF packages not available
    """
    if not HF_AVAILABLE:
        raise ImportError("HuggingFace datasets package is required for export")

    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL environment variable not set")

    # Construct SQL query
    sql = f"SELECT * FROM corpus.{view_name}"

    # Load dataset from SQL
    dataset = Dataset.from_sql(sql, database_url)

    return dataset


def push_to_hub(
    dataset: Dataset, repo_id: str, token: str | None = None, private: bool = False
) -> None:
    """
    Push a dataset to the HuggingFace Hub.

    Args:
        dataset: HuggingFace Dataset to push
        repo_id: Hub repository ID (e.g., "username/dataset-name")
        token: HF API token. If None, uses HF_TOKEN env var.
        private: Whether the repo should be private
    """
    if not HF_AVAILABLE:
        raise ImportError("HuggingFace datasets package is required for export")

    if token is None:
        token = os.getenv("HF_TOKEN")
        if not token:
            raise ValueError("HF_TOKEN environment variable not set")

    dataset.push_to_hub(repo_id, token=token, private=private)
