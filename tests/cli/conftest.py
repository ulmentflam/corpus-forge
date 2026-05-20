"""CLI-test-local pytest configuration.

Suppress the INFO-level startup log that the root callback emits on every
invocation.  The log goes to ``sys.stderr``, which the click CliRunner
(v8.2+) mixes into ``result.output``.  Tests that assert
``json.loads(result.output.strip())`` must not see that line.

Setting ``CF_LOG_LEVEL=WARNING`` raises the RichHandler threshold so the
INFO record never reaches the captured stream.  All existing CLI tests pass
with WARNING level because none of them assert on the presence of the INFO
startup log.
"""

import os

os.environ.setdefault("CF_LOG_LEVEL", "WARNING")
