"""Write corpus records to JSONL files.

Each record occupies exactly one line — compact JSON with no
trailing commas — making the output trivial to stream, grep,
and append to.
"""

from __future__ import annotations

import json
from typing import List

from cts.corpus.model import CorpusRecord, PassRecord


def write_corpus(
    records: List[CorpusRecord],
    out_path: str,
    *,
    append: bool = False,
) -> int:
    """Write corpus records to a JSONL file.

    Args:
        records: List of :class:`CorpusRecord` to write.
        out_path: Output file path.
        append: If ``True``, append to an existing file.

    Returns:
        Number of records written.
    """
    mode = "a" if append else "w"
    with open(out_path, mode, encoding="utf-8") as f:
        for record in records:
            line = json.dumps(record.to_dict(), default=str, separators=(",", ":"))
            f.write(line)
            f.write("\n")
    return len(records)


def write_passes(
    records: List[PassRecord],
    out_path: str,
    *,
    append: bool = False,
) -> int:
    """Write pass-level records to a JSONL file.

    Args:
        records: List of :class:`PassRecord` to write.
        out_path: Output file path.
        append: If ``True``, append to an existing file.

    Returns:
        Number of records written.
    """
    mode = "a" if append else "w"
    with open(out_path, mode, encoding="utf-8") as f:
        for record in records:
            line = json.dumps(record.to_dict(), default=str, separators=(",", ":"))
            f.write(line)
            f.write("\n")
    return len(records)
