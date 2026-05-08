"""Conflict file naming utilities for the sync subsystem."""


def conflict_filename(original, host, ts, provider=None):
    """Return a canonical conflict filename for *original*.

    Format without provider::

        <stem>.conflict-<host>-<isoZ-no-colons><suffix>

    Format with provider::

        <stem>.conflict-<provider>-<host>-<isoZ-no-colons><suffix>

    The timestamp is ISO 8601 basic (UTC, no colons), e.g. ``20260507T223045Z``.

    Parameters
    ----------
    original : Path
        The original file path.
    host : str
        The host identifier.
    ts : datetime
        UTC datetime to embed in the filename.
    provider : str | None
        Optional cloud provider tag (e.g. ``"icloud"``).

    Returns
    -------
    Path
        The conflict filename as a :class:`pathlib.Path`.
    """
    raise NotImplementedError("conflict_filename not yet implemented")
