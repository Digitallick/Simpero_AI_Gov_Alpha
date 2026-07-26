"""Sandbox retrieval UI (served): upload a PDF, ask a question, see the chunks.

The retrieval-mode sibling of the claims report. Unlike the report (a static
file://), retrieval is interactive -- an arbitrary question, embedded through
Voyage at request time -- so this is a small served FastAPI app, not a static
page.

    ./sandbox/retrieve.sh          # starts this app and opens the browser

Flow:
  POST /ingest  (a PDF)      -> subprocess emit_chunks in the parse service, then
                                embed (voyage-4-large, if VOYAGE_API_KEY is set)
                                and INSERT the chunks into the sandbox chunks
                                table, org-scoped; replaces any prior upload.
  POST /query   (a question) -> embed the question (if a key) and hybrid_search;
                                returns the ranked chunks.

Without VOYAGE_API_KEY it runs sparse-only (the keyword leg): chunks go in
unembedded and the dense leg is empty. Set the key for full dense+sparse.

Sandbox only: writes to the LOCAL Postgres (dd_app @ localhost:5433), never a
cloud database. One fixed demo org; re-uploading a PDF replaces its chunks.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import delete, select, text

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models import Chunk, Organisation
from app.models.organisation import OrgType
from app.services.embedding import get_embedder
from app.services.retrieval import hybrid_search
from scripts.ingest_chunks import _fold_content, _row_from_chunk

_HERE = Path(__file__).resolve().parent
_PARSER_DIR = _HERE.parent.parent / "Simpero_Gov_AI_Services"  # sibling of the backend repo
_ORG_KEY = "sandbox_retrieval"
_EMBED_DIM = 1024  # placeholder query vector in sparse-only mode

app = FastAPI(title="Simpero retrieval sandbox")


def _has_voyage() -> bool:
    return bool(get_settings().voyage_api_key)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (_HERE / "retrieve.html").read_text()


@app.get("/mode")
async def mode() -> dict:
    """So the page can show whether the dense leg is live."""
    return {"dense": _has_voyage()}


async def _org_id(session) -> int:
    """Get-or-create the demo tenant, RLS-scoped (same idiom as ingest_chunks)."""
    org = await session.scalar(select(Organisation).where(Organisation.clerk_org_id == _ORG_KEY))
    if org is None:
        org = Organisation(clerk_org_id=_ORG_KEY, name="retrieval sandbox", type=OrgType.PE_FIRM)
        session.add(org)
        await session.flush()
    return org.id


@app.post("/ingest")
async def ingest(request: Request, filename: str = "upload.pdf") -> JSONResponse:
    """Parse + chunk the uploaded PDF (in the parse service), embed if a key is
    present, and persist the chunks. Replaces any previous upload's chunks so the
    sandbox always reflects the document currently on screen.

    The PDF is the raw request body (not multipart) so the sandbox needs no
    python-multipart dependency; the browser sends the file directly and passes
    its name as `?filename=`.
    """
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty upload")
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / (Path(filename).name or "upload.pdf")
        pdf_path.write_bytes(data)
        # Chunking lives in the parse service (docling); the backend venv has no
        # docling, so shell out to emit_chunks in the sibling repo.
        # env without VIRTUAL_ENV so `uv run` picks the parser's own venv, not the
        # backend venv this app runs in (same reason run.sh uses `env -u`).
        parser_env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        proc = subprocess.run(
            ["uv", "run", "python", "scripts/emit_chunks.py", str(pdf_path)],
            cwd=_PARSER_DIR,
            capture_output=True,
            text=True,
            env=parser_env,
        )
        if proc.returncode != 0:
            raise HTTPException(500, f"emit_chunks failed: {proc.stderr[-500:]}")
        chunks = json.loads(proc.stdout)["chunks"]

    embeddings: list[list[float] | None] = [None] * len(chunks)
    version: str | None = None
    if _has_voyage() and chunks:
        embedder = get_embedder()
        embeddings = list(await embedder.embed_documents([_fold_content(c) for c in chunks]))
        version = embedder.version

    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE dd_app"))
        await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": _ORG_KEY})
        org_id = await _org_id(session)
        await session.execute(delete(Chunk).where(Chunk.org_id == org_id))  # replace prior upload
        session.add_all(
            _row_from_chunk(c, org_id, embedding=e, embedding_version=version)
            for c, e in zip(chunks, embeddings, strict=True)
        )
    return JSONResponse({"filename": filename, "chunks": len(chunks), "embedded": _has_voyage()})


@app.post("/query")
async def query(body: dict) -> JSONResponse:
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "empty question")
    query_embedding = (
        list(await get_embedder().embed_query(question)) if _has_voyage() else [0.0] * _EMBED_DIM
    )
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE dd_app"))
        await session.execute(text("SELECT set_config('app.org_id', :k, true)"), {"k": _ORG_KEY})
        hits = await hybrid_search(session, query_text=question, query_embedding=query_embedding)
    return JSONResponse(
        {
            "dense": _has_voyage(),
            "hits": [
                {
                    "score": round(h.score, 4),
                    "page": h.page,
                    "element_type": h.element_type,
                    "content": h.content,
                }
                for h in hits
            ],
        }
    )
