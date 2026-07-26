"""Embedding provider for retrieval (AE-A-RETR-8, SIM-245).

voyage-4-large is the Alpha *default* embedding model -- the vector half of the
hybrid query path (SIM-240) runs on it, and chunks are embedded with it at write
time. Default, not locked: general benchmarks do not predict financial-retrieval
quality (FinMTEB, EMNLP 2025), and the golden set (SIM-65) is what will actually
settle the model. Three things keep the choice reversible:

  - the call sits behind the `Embedder` protocol, so a model swap (AE-A-RETR-9)
    is a new class plus a one-line change in `get_embedder`, not edits scattered
    through the ingest and query paths;
  - the output dimension is configuration, not a constant -- the voyage-4 family
    serves 2048/1024/512/256 from one Matryoshka space, so the number is a
    runtime choice (settings.embedding_dimension);
  - every embedder reports a `version`, stored per chunk (`embedding_version`)
    so a later re-embed knows what to redo.

Documents and queries are embedded with different `input_type`s ("document" vs
"query") -- Voyage's asymmetric embedding, which measurably helps retrieval, so
the two entry points stay distinct rather than collapsing into one call.

No provider SDK: the request is a single JSON POST, and httpx (already a
dependency) keeps it async-native and the client injectable for tests. The key
is read from the environment via Settings and never logged.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

import httpx

from app.core.config import get_settings

_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
# Voyage accepts up to 1000 inputs per request; stay well under it so a batch of
# large table chunks never trips the per-request token ceiling either.
_MAX_BATCH = 128
_TIMEOUT = httpx.Timeout(30.0)


class EmbeddingError(RuntimeError):
    """The provider is unreachable, misconfigured, or returned a vector that
    failed the sanity check (wrong length, all-zero, or non-finite)."""


class Embedder(Protocol):
    """The swappable seam. Ingest and the query path depend on THIS, never on a
    concrete model, so a model swap is contained to `get_embedder`."""

    @property
    def version(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


def _sane(vector: list[float], dimension: int) -> list[float]:
    """Reject a degenerate vector rather than store it. Embedding endpoints have
    been reported to silently return all-zero or truncated vectors for some
    inputs, and an all-zero vector is worse than an error -- cosine distance to it
    is undefined, so it would quietly poison ranking rather than fail loudly."""
    if len(vector) != dimension:
        raise EmbeddingError(f"voyage returned dim {len(vector)}, expected {dimension}")
    if not all(math.isfinite(x) for x in vector):
        raise EmbeddingError("voyage returned a vector with non-finite values")
    if not any(x != 0.0 for x in vector):
        raise EmbeddingError("voyage returned a degenerate all-zero vector")
    return vector


class VoyageEmbedder:
    """`Embedder` backed by the Voyage AI embeddings API. The httpx client is
    injectable so the request/response contract is testable without a network or
    an API key."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimension: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise EmbeddingError(
                "VOYAGE_API_KEY is not set, so the embedder cannot run. Set it in the "
                "environment (never in code) to enable dense retrieval."
            )
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._client = client

    @property
    def version(self) -> str:
        # Model AND dimension: a 1024-d and a 2048-d vector from the same model
        # are not interchangeable in an index, so both belong in what a re-embed
        # keys off.
        return f"{self._model}:{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed(list(texts), input_type="document")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], input_type="query")
        return vectors[0]

    async def _embed(self, inputs: list[str], *, input_type: str) -> list[list[float]]:
        client = self._client or httpx.AsyncClient(timeout=_TIMEOUT)
        owns_client = self._client is None
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(inputs), _MAX_BATCH):
                batch = inputs[start : start + _MAX_BATCH]
                rows = await self._post(client, batch, input_type)
                # Each row carries its own `index`; order by it rather than
                # trusting the response to come back positionally.
                for row in sorted(rows, key=lambda row: row["index"]):
                    vectors.append(_sane(list(row["embedding"]), self._dimension))
        finally:
            if owns_client:
                await client.aclose()
        return vectors

    async def _post(
        self, client: httpx.AsyncClient, batch: list[str], input_type: str
    ) -> list[dict]:
        payload = {
            "input": batch,
            "model": self._model,
            "input_type": input_type,
            "output_dimension": self._dimension,
            "output_dtype": "float",
        }
        try:
            response = await client.post(
                _VOYAGE_URL, json=payload, headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"voyage request failed: {exc}") from exc
        if response.status_code != 200:
            raise EmbeddingError(f"voyage returned {response.status_code}: {response.text[:200]}")
        data = response.json().get("data")
        if not isinstance(data, list) or not data:
            raise EmbeddingError("voyage response carried no embeddings")
        if len(data) != len(batch):
            raise EmbeddingError(f"voyage returned {len(data)} vectors for {len(batch)} inputs")
        return data


@lru_cache
def get_embedder() -> Embedder:
    """The configured embedder. The ONE place that names a concrete model;
    everything else depends on the `Embedder` protocol, so a model swap
    (AE-A-RETR-9) changes only this function."""
    settings = get_settings()
    return VoyageEmbedder(
        api_key=settings.voyage_api_key,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
