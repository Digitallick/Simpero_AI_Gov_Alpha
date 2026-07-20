# pyright: reportMissingImports=false
"""Refresh the sandbox HTML report's DATA file after a pipeline run.

    called by run.sh as its final step; manual re-run:
    ( cd ../Simpero_Gov_AI_Services && uv run python <this file> [--org KEY] [--pdf PATH] )

The report is split in two so no CIM content can leak onto git:

  - sandbox/report.html      -- the template, committed, contains ZERO data.
  - sandbox/cim/report_data.js -- everything confidential (claims + rendered
    page images as data URIs), written here on every run. Lives in
    sandbox/cim/, which is gitignored, so it is never committed.

The template loads the data with a plain <script src="cim/report_data.js">
tag -- unlike fetch(), that works from file:// with no local server.

Pages: only pages carrying at least one bbox-cited claim are rendered
(missing claims have no span/bbox, enforced by ck_claims_missing_has_no_span).
Rendered with pypdfium2 at 144 DPI. bbox is [x0, top, x1, bottom] in
TOPLEFT-origin PDF points (contracts/claims.schema.json); pypdfium2 rasters
line up under a flat `pixel = point * scale`, no axis flip -- the same
mapping the parser repo's inspect.py verified empirically.

Claims come out of the sandbox Postgres via `docker compose exec psql`
returning JSON, so neither repo needs a DB driver for this. Runs under the
PARSER repo's venv (pypdfium2 + Pillow live there; this backend repo
deliberately has no PDF libraries -- hence the pyright pragma above).
"""

import argparse
import base64
import io
import json
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium

SANDBOX_DIR = Path(__file__).resolve().parent
SCALE = 2.0  # 144 DPI over 72-point base; pixel = point * scale, no axis flip


def fetch_claims(org_key: str) -> list[dict]:
    """Pull the tenant's claims as JSON through psql inside the container."""
    org_sql = org_key.replace("'", "''")
    sql = f"""
      SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json) FROM (
        SELECT c.entity, c.attribute, c.value, c.kind, c.page,
               c.char_start, c.char_end, c.bbox, c.status, c.flags
        FROM claims c JOIN organisation o ON o.id = c.org_id
        WHERE o.clerk_org_id = '{org_sql}'
          -- every run.sh ingest appends under a fresh session_id; the report
          -- shows THIS parse, not the pile-up of all previous runs
          AND c.session_id IS NOT DISTINCT FROM (
            SELECT c2.session_id
            FROM claims c2 JOIN organisation o2 ON o2.id = c2.org_id
            WHERE o2.clerk_org_id = '{org_sql}'
            ORDER BY c2.created_at DESC LIMIT 1)
        ORDER BY c.page, c.char_start NULLS LAST) t;
    """
    proc = subprocess.run(
        ["docker", "compose", "-f", str(SANDBOX_DIR / "docker-compose.yml"),
         "exec", "-T", "postgres",
         "psql", "-U", "doadmin", "-d", "simpero", "-tA", "-c", sql],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"error: psql failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def render_pages(pdf_path: Path, pages: list[int]) -> dict[int, dict]:
    """Render the given 1-based pages; return {page: {uri, w_pt, h_pt}}."""
    doc = pdfium.PdfDocument(str(pdf_path))
    out: dict[int, dict] = {}
    for page_no in pages:
        if not 1 <= page_no <= len(doc):
            sys.exit(f"error: claim cites page {page_no}, but the PDF has {len(doc)} pages "
                     f"-- wrong --pdf for this dataset?")
        page = doc[page_no - 1]
        image = page.render(scale=SCALE).to_pil().convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        out[page_no] = {
            "uri": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "w_pt": page.get_width(),
            "h_pt": page.get_height(),
        }
        print(f"      rendered page {page_no}  ({image.width}x{image.height}px)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", default="sandbox_demo", help="tenant key (default: sandbox_demo)")
    ap.add_argument("--pdf", type=Path, default=None,
                    help="source PDF; defaults to the single PDF in sandbox/cim/")
    args = ap.parse_args()

    pdf_path = args.pdf
    if pdf_path is None:
        pdfs = sorted(p for p in (SANDBOX_DIR / "cim").glob("*.pdf"))
        if len(pdfs) != 1:
            sys.exit(f"error: {len(pdfs)} PDFs in sandbox/cim/ -- pick one with --pdf")
        pdf_path = pdfs[0]
    if not pdf_path.is_file():
        sys.exit(f"error: no such file: {pdf_path}")

    print(f"      tenant '{args.org}', source {pdf_path.name}")
    claims = fetch_claims(args.org)
    if not claims:
        sys.exit(f"error: no claims for tenant '{args.org}' -- run ./sandbox/run.sh first")

    cited_pages = sorted({c["page"] for c in claims if c["bbox"] and c["page"] is not None})
    print(f"      {len(claims)} claims, {len(cited_pages)} page(s) carry a citation")
    pages = render_pages(pdf_path, cited_pages)

    out = SANDBOX_DIR / "cim" / "report_data.js"
    data = {"pdf": pdf_path.name, "org": args.org, "claims": claims, "pages": pages}
    out.write_text("window.REPORT = " + json.dumps(data) + ";\n")
    print(f"      wrote {out}  ({out.stat().st_size / 1e6:.1f} MB; gitignored)")
    print(f"      open  {SANDBOX_DIR / 'report.html'}  in a browser")


if __name__ == "__main__":
    main()
