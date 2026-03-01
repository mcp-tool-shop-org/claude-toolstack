"""Pluggable embedding backend.

Default: sentence-transformers model running locally.
Device selection: auto (CUDA if available), cpu, or cuda.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol


class EmbedderBackend(Protocol):
    """Protocol for embedding backends."""

    model_name: str
    dim: int

    def embed_texts(self, texts: List[str]) -> List[bytes]:
        """Embed a batch of texts. Returns serialized float32 vectors."""
        ...


@dataclass
class EmbedderInfo:
    """Metadata about the loaded embedder."""

    model_name: str
    dim: int
    device: str
    backend: str


class SentenceTransformerEmbedder:
    """Embedding via sentence-transformers (local GPU/CPU)."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        device: str = "auto",
    ) -> None:
        from cts.semantic import _check_deps

        _check_deps()

        import numpy as np
        import torch
        from sentence_transformers import SentenceTransformer

        self._np = np

        # Resolve device
        if device == "auto":
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self._device = device

        self._model = SentenceTransformer(model_name, device=self._device)
        self.model_name = model_name
        self.dim = self._model.get_sentence_embedding_dimension()
        self._actual_device = self._device

    def embed_texts(self, texts: List[str]) -> List[bytes]:
        """Embed texts and return as serialized float32 byte blobs."""
        if not texts:
            return []

        embeddings = self._model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

        return [
            self._np.array(vec, dtype=self._np.float32).tobytes() for vec in embeddings
        ]

    def info(self) -> EmbedderInfo:
        """Get embedder metadata."""
        return EmbedderInfo(
            model_name=self.model_name,
            dim=self.dim,
            device=self._actual_device,
            backend="sentence-transformers",
        )


class MockEmbedder:
    """Mock embedder for testing (no GPU, no deps).

    Produces deterministic embeddings based on text hash.
    """

    def __init__(self, dim: int = 384) -> None:
        self.model_name = "mock-embedder"
        self.dim = dim

    def embed_texts(self, texts: List[str]) -> List[bytes]:
        """Produce deterministic mock embeddings."""
        import hashlib
        import struct

        results: List[bytes] = []
        for text in texts:
            # Generate deterministic floats from text hash
            h = hashlib.sha256(text.encode()).digest()
            seed_values = []
            for i in range(self.dim):
                # Use rolling hash bytes to generate floats in [-1, 1]
                byte_idx = i % len(h)
                val = (h[byte_idx] + i) % 256
                seed_values.append((val / 255.0) * 2 - 1)

            # Normalize
            magnitude = sum(v * v for v in seed_values) ** 0.5
            if magnitude > 0:
                seed_values = [v / magnitude for v in seed_values]

            results.append(struct.pack(f"{self.dim}f", *seed_values))
        return results

    def info(self) -> EmbedderInfo:
        return EmbedderInfo(
            model_name="mock-embedder",
            dim=self.dim,
            device="cpu",
            backend="mock",
        )


def create_embedder(
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    device: str = "auto",
    mock: bool = False,
    mock_dim: int = 384,
) -> SentenceTransformerEmbedder | MockEmbedder:
    """Create an embedder instance.

    Args:
        model_name: Model name for sentence-transformers.
        device: Device selection (auto/cpu/cuda).
        mock: If True, return MockEmbedder (for testing).
        mock_dim: Dimension for mock embedder.

    Returns:
        An embedder instance.
    """
    if mock:
        return MockEmbedder(dim=mock_dim)
    return SentenceTransformerEmbedder(model_name=model_name, device=device)
