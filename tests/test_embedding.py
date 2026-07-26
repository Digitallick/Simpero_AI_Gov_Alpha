"""VoyageEmbedder: the request/response contract with the Voyage embeddings API,
exercised through an httpx MockTransport so it needs no network and no API key.

What matters is the contract, not the model: documents and queries carry the
right input_type, the configured dimension is requested and enforced, a
degenerate vector is rejected rather than stored, and batching preserves order.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.services import embedding as emb
from app.services.embedding import EmbeddingError, VoyageEmbedder

DIM = 4


def _handler(
    calls: list[dict],
    *,
    vectors_for: Callable[[int, str], list[float]] | None = None,
    status: int = 200,
    body: dict | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """A MockTransport handler that records each request's JSON and returns one
    float vector per input (or a caller-supplied status/body)."""

    make = vectors_for or (lambda i, _text: [float(i + 1)] * DIM)

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append({"payload": payload, "auth": request.headers.get("Authorization")})
        if status != 200:
            return httpx.Response(status, json=body or {})
        rows = [{"index": i, "embedding": make(i, t)} for i, t in enumerate(payload["input"])]
        return httpx.Response(200, json={"data": rows, "model": payload["model"]})

    return handle


def _embedder(handler, *, dimension: int = DIM, api_key: str = "vk-test") -> VoyageEmbedder:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VoyageEmbedder(
        api_key=api_key, model="voyage-4-large", dimension=dimension, client=client
    )


async def test_embed_documents_one_vector_per_input_with_document_input_type() -> None:
    calls: list[dict] = []
    vectors = await _embedder(_handler(calls)).embed_documents(["alpha", "beta", "gamma"])
    assert len(vectors) == 3
    assert all(len(v) == DIM for v in vectors)
    assert calls[0]["payload"]["input_type"] == "document"
    assert calls[0]["payload"]["output_dimension"] == DIM
    assert calls[0]["auth"] == "Bearer vk-test"


async def test_embed_query_uses_query_input_type_and_returns_one_vector() -> None:
    calls: list[dict] = []
    vector = await _embedder(_handler(calls)).embed_query("total revenue")
    assert len(vector) == DIM
    assert calls[0]["payload"]["input_type"] == "query"
    assert calls[0]["payload"]["input"] == ["total revenue"]


async def test_empty_document_list_makes_no_request() -> None:
    calls: list[dict] = []
    assert await _embedder(_handler(calls)).embed_documents([]) == []
    assert calls == []


async def test_batching_splits_and_preserves_global_order(monkeypatch) -> None:
    monkeypatch.setattr(emb, "_MAX_BATCH", 2)
    calls: list[dict] = []
    embedder = _embedder(_handler(calls, vectors_for=lambda _i, t: [float(int(t) + 1)] * DIM))
    vectors = await embedder.embed_documents(["0", "1", "2", "3", "4"])
    assert len(calls) == 3  # 2 + 2 + 1
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


async def test_out_of_order_response_is_reordered_by_index() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["input"])
        rows = [{"index": i, "embedding": [float(i) + 1.0] * DIM} for i in reversed(range(n))]
        return httpx.Response(200, json={"data": rows, "model": "voyage-4-large"})

    vectors = await _embedder(handle).embed_documents(["a", "b", "c"])
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0]


async def test_degenerate_all_zero_vector_is_rejected() -> None:
    embedder = _embedder(_handler([], vectors_for=lambda _i, _t: [0.0] * DIM))
    with pytest.raises(EmbeddingError, match="all-zero"):
        await embedder.embed_query("x")


async def test_wrong_dimension_from_api_is_rejected() -> None:
    embedder = _embedder(_handler([], vectors_for=lambda _i, _t: [1.0, 2.0]))
    with pytest.raises(EmbeddingError, match="dim 2"):
        await embedder.embed_query("x")


async def test_non_200_raises() -> None:
    embedder = _embedder(_handler([], status=500, body={"error": "boom"}))
    with pytest.raises(EmbeddingError, match="500"):
        await embedder.embed_query("x")


async def test_missing_api_key_fails_closed() -> None:
    with pytest.raises(EmbeddingError, match="VOYAGE_API_KEY"):
        VoyageEmbedder(api_key="", model="voyage-4-large", dimension=DIM)


def test_version_carries_model_and_dimension() -> None:
    embedder = VoyageEmbedder(api_key="k", model="voyage-4-large", dimension=1024)
    assert embedder.version == "voyage-4-large:1024"
    assert embedder.dimension == 1024
