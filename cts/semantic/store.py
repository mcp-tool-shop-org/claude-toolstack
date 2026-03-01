"""SQLite-backed embedding store.

Single-file persistence for chunk embeddings.
Schema version tracked in meta table for future migrations.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from cts.semantic import SEMANTIC_SCHEMA_VERSION
from cts.semantic.chunker import Chunk


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    repo        TEXT NOT NULL,
    path        TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id    TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vec         BLOB NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
"""


class SemanticStore:
    """SQLite-backed store for chunk embeddings."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript(_SCHEMA_DDL)
        # Set schema version if not present
        existing = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if existing is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SEMANTIC_SCHEMA_VERSION)),
            )
            self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # -----------------------------------------------------------------------
    # Meta
    # -----------------------------------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        """Get a metadata value."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Set a metadata value (upsert)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def get_status(self) -> Dict[str, Any]:
        """Get store status summary."""
        chunk_count = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        embed_count = self._conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[
            0
        ]
        model = self.get_meta("model") or "none"
        dim = self.get_meta("dim") or "0"
        last_indexed = self.get_meta("last_indexed_at") or "never"

        return {
            "db_path": self.db_path,
            "schema_version": int(
                self.get_meta("schema_version") or SEMANTIC_SCHEMA_VERSION
            ),
            "chunks": chunk_count,
            "embeddings": embed_count,
            "model": model,
            "dim": int(dim),
            "last_indexed_at": last_indexed,
        }

    # -----------------------------------------------------------------------
    # Chunks
    # -----------------------------------------------------------------------

    def upsert_chunks(self, chunks: List[Chunk]) -> int:
        """Insert or update chunks. Returns count of new/updated chunks."""
        changed = 0
        for chunk in chunks:
            existing = self._conn.execute(
                "SELECT content_hash FROM chunks WHERE chunk_id = ?",
                (chunk.chunk_id,),
            ).fetchone()

            if existing is None or existing[0] != chunk.content_hash:
                self._conn.execute(
                    """INSERT OR REPLACE INTO chunks
                       (chunk_id, repo, path, start_line, end_line, content_hash)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        chunk.chunk_id,
                        chunk.repo,
                        chunk.path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.content_hash,
                    ),
                )
                changed += 1

        self._conn.commit()
        return changed

    def get_chunk_ids_for_path(self, path: str) -> List[str]:
        """Get all chunk IDs for a file path."""
        rows = self._conn.execute(
            "SELECT chunk_id FROM chunks WHERE path = ?", (path,)
        ).fetchall()
        return [r[0] for r in rows]

    def get_existing_hashes(self) -> Dict[str, str]:
        """Get mapping of chunk_id -> content_hash for all chunks."""
        rows = self._conn.execute(
            "SELECT chunk_id, content_hash FROM chunks"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def delete_chunks(self, chunk_ids: List[str]) -> None:
        """Delete chunks and their embeddings."""
        if not chunk_ids:
            return
        placeholders = ",".join("?" for _ in chunk_ids)
        self._conn.execute(
            f"DELETE FROM embeddings WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
        self._conn.execute(
            f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
        self._conn.commit()

    def delete_path(self, path: str) -> int:
        """Delete all chunks for a file path. Returns count deleted."""
        ids = self.get_chunk_ids_for_path(path)
        self.delete_chunks(ids)
        return len(ids)

    # -----------------------------------------------------------------------
    # Embeddings
    # -----------------------------------------------------------------------

    def store_embeddings(
        self,
        chunk_ids: List[str],
        vectors: List[bytes],
        model: str,
        dim: int,
    ) -> None:
        """Store embedding vectors for chunks.

        Args:
            chunk_ids: List of chunk IDs.
            vectors: List of serialized embedding blobs (float32 bytes).
            model: Model name used for embedding.
            dim: Embedding dimension.
        """
        now = time.time()
        for cid, vec in zip(chunk_ids, vectors):
            self._conn.execute(
                """INSERT OR REPLACE INTO embeddings
                   (chunk_id, model, dim, vec, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (cid, model, dim, vec, now),
            )
        self._conn.commit()

        # Update meta
        self.set_meta("model", model)
        self.set_meta("dim", str(dim))
        self.set_meta("last_indexed_at", str(now))

    def get_chunks_without_embeddings(self) -> List[str]:
        """Get chunk IDs that have no embedding stored."""
        rows = self._conn.execute(
            """SELECT c.chunk_id FROM chunks c
               LEFT JOIN embeddings e ON c.chunk_id = e.chunk_id
               WHERE e.chunk_id IS NULL"""
        ).fetchall()
        return [r[0] for r in rows]

    def get_all_embeddings(
        self,
    ) -> List[Tuple[str, str, int, int, bytes]]:
        """Get all embeddings with chunk metadata.

        Returns:
            List of (chunk_id, path, start_line, end_line, vec_blob).
        """
        rows = self._conn.execute(
            """SELECT c.chunk_id, c.path, c.start_line, c.end_line, e.vec
               FROM embeddings e
               JOIN chunks c ON c.chunk_id = e.chunk_id"""
        ).fetchall()
        return rows

    def get_embeddings_for_paths(
        self, paths: List[str]
    ) -> List[Tuple[str, str, int, int, bytes]]:
        """Get embeddings for specific file paths only."""
        if not paths:
            return []
        placeholders = ",".join("?" for _ in paths)
        rows = self._conn.execute(
            f"""SELECT c.chunk_id, c.path, c.start_line, c.end_line, e.vec
                FROM embeddings e
                JOIN chunks c ON c.chunk_id = e.chunk_id
                WHERE c.path IN ({placeholders})""",
            paths,
        ).fetchall()
        return rows

    # -----------------------------------------------------------------------
    # Maintenance
    # -----------------------------------------------------------------------

    def rebuild(self) -> None:
        """Drop all data and reset. Schema is preserved."""
        self._conn.execute("DELETE FROM embeddings")
        self._conn.execute("DELETE FROM chunks")
        self._conn.commit()

    def chunk_count(self) -> int:
        """Total number of chunks stored."""
        return self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def embedding_count(self) -> int:
        """Total number of embeddings stored."""
        return self._conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
