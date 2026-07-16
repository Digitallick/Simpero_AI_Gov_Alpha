"""DS-W3-7 claim emission -- the JSON seam from parse output to the
deterministic claims spine (contracts/facts.schema.json, the frozen C3
contract).

Given a claim candidate (entity, attribute, and the quote or cell it should
come from) plus the parse-time records it is read against, this module
resolves it through DS-W3-3 (exact-span resolver) and DS-W3-4 / DS-W3-5
(scale capture) into one Fact JSON row. All-or-nothing provenance: a claim
either resolves to an exact span/cell, or is written `confidence="missing"` --
never a partial citation.

Pydantic (`extra="forbid"`, closed Literal enums) is the boundary validation
the ticket calls for: a key-name or enum-value drift raises here, before the
JSON ever crosses the seam.

Every flag raised while emitting a fact is both attached to that fact's
`flags` array AND appended to a `FlagLog` (run_id, stage, element_id,
flag_type, detail) -- the structured per-run capture the ticket asks for,
without building the aggregation/dashboard surface that's explicitly out of
scope for alpha.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .elements import ChartElement, TableElement
from .resolver import resolve
from .scale import ScaleSource, ValueType, determine_scale
from .schemas import BBox, PageIndex, TableCellRecord, TableRecord, XlsxCellRecord, XlsxSheetRecord
from .xlsx_parser import determine_xlsx_scale

Confidence = Literal["extracted", "modeled", "formula-verified", "stub", "missing"]

# Mirrors contracts/facts.schema.json's value.flags enum exactly. Kept here
# (rather than re-derived from the schema file at import time) so an unknown
# flag string raises immediately, at the call site that produced it.
FLAG_TYPES = frozenset(
    {
        "ragged_table_rows",
        "chart_data_not_extracted",
        "scale_assumed",
        "quote_unresolved",
        "ambiguous_location",
        "ambiguous_region_bounds",
        "ambiguous_unit",
        "table_touches_page_edge",
        "zero_text_page",
        "empty_section",
    }
)


class FactValue(BaseModel):
    """Mirrors the C3 contract's `value` object. `scale_source` has no null in
    its JSON-schema type (it's a bare enum), so it must be omitted -- never
    emitted as null -- whenever it isn't known; see `to_json`."""

    model_config = ConfigDict(extra="forbid")

    raw: str
    normalized: float | None
    unit: str | None
    value_type: ValueType
    scale_multiplier: float | None = None
    scale_source: ScaleSource | None = None

    def to_json(self) -> dict:
        out: dict = {
            "raw": self.raw,
            "normalized": self.normalized,
            "unit": self.unit,
            "value_type": self.value_type,
        }
        if self.scale_multiplier is not None:
            out["scale_multiplier"] = self.scale_multiplier
        if self.scale_source is not None:
            out["scale_source"] = self.scale_source
        return out


class PdfLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pdf"] = "pdf"
    file: str
    page: int
    char_start: int
    char_end: int
    bbox: list[BBox] = Field(default_factory=list)
    paragraph: int | None = None
    document_id: str | None = None
    document_name: str | None = None

    def to_json(self) -> dict:
        out: dict = {
            "kind": "pdf",
            "file": self.file,
            "page": self.page,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }
        if self.bbox:
            out["bbox"] = [[b.x0, b.top, b.x1, b.bottom] for b in self.bbox]
        if self.paragraph is not None:
            out["paragraph"] = self.paragraph
        if self.document_id is not None:
            out["document_id"] = self.document_id
        if self.document_name is not None:
            out["document_name"] = self.document_name
        return out


class XlsxLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["xlsx"] = "xlsx"
    file: str
    sheet: str
    cell_ref: str
    document_id: str | None = None
    document_name: str | None = None

    def to_json(self) -> dict:
        out: dict = {
            "kind": "xlsx",
            "file": self.file,
            "sheet": self.sheet,
            "cell_ref": self.cell_ref,
        }
        if self.document_id is not None:
            out["document_id"] = self.document_id
        if self.document_name is not None:
            out["document_name"] = self.document_name
        return out


Location = PdfLocation | XlsxLocation


class Fact(BaseModel):
    """One claims-spine row, per contracts/facts.schema.json. `id`/`deal_id`/
    `session_id`/`org_id` are the backend's to fill in at persistence time --
    the parser doesn't know them, so they're omitted here rather than
    populated with placeholders."""

    model_config = ConfigDict(extra="forbid")

    entity: str
    attribute: str
    value: FactValue
    location: Location
    confidence: Confidence
    section: str | None = None
    flags: list[str] = Field(default_factory=list)

    def to_json(self) -> dict:
        out: dict = {
            "entity": self.entity,
            "attribute": self.attribute,
            "value": self.value.to_json(),
            "location": self.location.to_json(),
            "confidence": self.confidence,
        }
        if self.section is not None:
            out["section"] = self.section
        if self.flags:
            out["flags"] = self.flags
        return out


class FlagLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    stage: str
    element_id: str
    flag_type: str
    detail: str | None = None


class FlagLog:
    """Structured per-run flag log -- alpha-appropriate monitoring capture,
    not a dashboard. Every flag raised anywhere in a run is recorded here, in
    addition to being attached to the fact/element it belongs to, so the
    history exists once the aggregation/dashboard surface is built (Pilot).
    One FlagLog per parse run.
    """

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or str(uuid.uuid4())
        self.entries: list[FlagLogEntry] = []

    def log(self, stage: str, element_id: str, flag_type: str, detail: str | None = None) -> None:
        if flag_type not in FLAG_TYPES:
            raise ValueError(f"unknown flag_type {flag_type!r}; not in the C3 contract's flag enum")
        self.entries.append(
            FlagLogEntry(
                run_id=self.run_id,
                stage=stage,
                element_id=element_id,
                flag_type=flag_type,
                detail=detail,
            )
        )

    def log_all(
        self, stage: str, element_id: str, flag_types: list[str], detail: str | None = None
    ) -> None:
        for flag_type in flag_types:
            self.log(stage, element_id, flag_type, detail)

    def to_json(self) -> list[dict]:
        return [e.model_dump(exclude_none=True) for e in self.entries]


_STAGE_CLAIM_EMISSION = "claim_emission"
_STAGE_ELEMENT_PROCESSING = "element_processing"


def _missing_pdf_fact(
    entity: str,
    attribute: str,
    raw: str,
    value_type: ValueType,
    page: PageIndex,
    file: str,
    flags: list[str],
    *,
    document_id: str | None,
    document_name: str | None,
    section: str | None,
) -> Fact:
    return Fact(
        entity=entity,
        attribute=attribute,
        value=FactValue(raw=raw, normalized=None, unit=None, value_type=value_type),
        location=PdfLocation(
            file=file,
            page=page.page,
            char_start=0,
            char_end=0,
            document_id=document_id,
            document_name=document_name,
        ),
        confidence="missing",
        section=section,
        flags=flags,
    )


def emit_pdf_fact(
    entity: str,
    attribute: str,
    quote: str,
    page: PageIndex,
    *,
    value_type: ValueType,
    file: str,
    flag_log: FlagLog,
    table: TableRecord | None = None,
    cell: TableCellRecord | None = None,
    section: str | None = None,
    document_id: str | None = None,
    document_name: str | None = None,
    stage: str = _STAGE_CLAIM_EMISSION,
) -> Fact:
    """Emit one PDF-sourced fact for `quote` on `page`. Fails closed to
    `confidence="missing"` on a zero-text page or an unresolved/ambiguous
    quote (DS-W3-3) -- never a fabricated or partially-cited value. `table`/
    `cell` are passed through to DS-W3-4's column-header scale lookup when the
    quote came from a table cell; omit both for prose."""
    element_id = f"pdf:{file}:p{page.page}:{attribute}"

    if not page.text.strip():
        flag_log.log(stage, element_id, "zero_text_page")
        return _missing_pdf_fact(
            entity,
            attribute,
            quote,
            value_type,
            page,
            file,
            ["zero_text_page"],
            document_id=document_id,
            document_name=document_name,
            section=section,
        )

    span = resolve(quote, page)
    if span is None:
        flag_log.log(stage, element_id, "quote_unresolved", detail=quote)
        return _missing_pdf_fact(
            entity,
            attribute,
            quote,
            value_type,
            page,
            file,
            ["quote_unresolved"],
            document_id=document_id,
            document_name=document_name,
            section=section,
        )

    flags: list[str] = []
    if value_type == "text":
        value = FactValue(raw=quote, normalized=None, unit=None, value_type=value_type)
    else:
        scale_result = determine_scale(
            quote, page, span.char_start, value_type=value_type, table=table, cell=cell
        )
        flags = list(scale_result.flags)
        if (
            value_type == "currency"
            and scale_result.unit is None
            and scale_result.scale_source
            in (
                "column_header",
                "page_header",
            )
        ):
            # A real scale header was found and applied, but it carried no
            # currency code ("(in Thousands)" with no "CAD"/"USD" prefix) --
            # distinct from assumed_1x (no header at all), so flagged
            # separately: the multiplier is trusted, the currency is not.
            flags.append("ambiguous_unit")
        if flags:
            flag_log.log_all(stage, element_id, flags, detail=scale_result.scale_context)
        value = FactValue(
            raw=scale_result.raw,
            normalized=scale_result.normalized,
            unit=scale_result.unit,
            value_type=value_type,
            scale_multiplier=scale_result.scale_multiplier,
            scale_source=scale_result.scale_source,
        )

    bbox = span.line_bboxes or [span.bbox]
    return Fact(
        entity=entity,
        attribute=attribute,
        value=value,
        location=PdfLocation(
            file=file,
            page=span.page,
            char_start=span.char_start,
            char_end=span.char_end,
            bbox=bbox,
            document_id=document_id,
            document_name=document_name,
        ),
        confidence="extracted",
        section=section,
        flags=flags,
    )


def emit_pdf_table_cell_fact(
    entity: str,
    attribute: str,
    table: TableRecord,
    cell: TableCellRecord,
    page: PageIndex,
    *,
    value_type: ValueType,
    file: str,
    flag_log: FlagLog,
    section: str | None = None,
    document_id: str | None = None,
    document_name: str | None = None,
    stage: str = _STAGE_CLAIM_EMISSION,
) -> Fact:
    """Emit a fact for one table cell. A cell with no resolvable bbox (neither
    Docling-native nor DS-2's reconstruction fallback located it) is written
    missing outright -- citing a cell whose own region is unknown would be a
    partial citation, which all-or-nothing provenance forbids."""
    if cell.bbox_source is None:
        element_id = f"pdf:{file}:p{page.page}:table:r{cell.row}c{cell.col}:{attribute}"
        flag_log.log(
            stage,
            element_id,
            "ambiguous_region_bounds",
            detail=f"cell ({cell.row},{cell.col}) has no resolvable source bbox",
        )
        return _missing_pdf_fact(
            entity,
            attribute,
            cell.text_normalized,
            value_type,
            page,
            file,
            ["ambiguous_region_bounds"],
            document_id=document_id,
            document_name=document_name,
            section=section,
        )

    return emit_pdf_fact(
        entity,
        attribute,
        cell.text_normalized,
        page,
        value_type=value_type,
        file=file,
        flag_log=flag_log,
        table=table,
        cell=cell,
        section=section,
        document_id=document_id,
        document_name=document_name,
        stage=stage,
    )


def emit_xlsx_fact(
    entity: str,
    attribute: str,
    sheet: XlsxSheetRecord,
    cell: XlsxCellRecord,
    *,
    value_type: ValueType,
    file: str,
    flag_log: FlagLog,
    section: str | None = None,
    document_id: str | None = None,
    document_name: str | None = None,
    stage: str = _STAGE_CLAIM_EMISSION,
) -> Fact:
    """Emit one XLSX-sourced fact. Provenance always resolves to
    `cell.merged_anchor_ref`, per DS-W3-5, so a non-anchor merged cell still
    points somewhere real. openpyxl stores a merged range's value only on its
    anchor cell -- a non-anchor cell's own `value`/`formula` are always None --
    so a non-anchor `cell` is resolved to its anchor's record before reading
    any value data; only the anchor ever actually holds one.

    A formula cell is written `confidence="stub"`: its true value is only
    knowable once HyperFormula re-executes it outside this service, and the
    file's own cached result is never trusted as a substitute (the exact
    anti-pattern DS-W3-5 replaces)."""
    if not cell.is_merged_anchor:
        cell = next(c for c in sheet.cells if c.cell_ref == cell.merged_anchor_ref)

    element_id = f"xlsx:{file}:{sheet.name}!{cell.cell_ref}:{attribute}"
    location = XlsxLocation(
        file=file,
        sheet=sheet.name,
        cell_ref=cell.merged_anchor_ref,
        document_id=document_id,
        document_name=document_name,
    )

    if cell.formula is not None:
        return Fact(
            entity=entity,
            attribute=attribute,
            value=FactValue(raw=cell.formula, normalized=None, unit=None, value_type=value_type),
            location=location,
            confidence="stub",
            section=section,
            flags=[],
        )

    if value_type == "text":
        raw = "" if cell.value is None else str(cell.value)
        found = cell.value is not None
        return Fact(
            entity=entity,
            attribute=attribute,
            value=FactValue(raw=raw, normalized=None, unit=None, value_type=value_type),
            location=location,
            confidence="extracted" if found else "missing",
            section=section,
            flags=[] if found else ["quote_unresolved"],
        )

    scale_result = determine_xlsx_scale(sheet, cell, value_type=value_type)
    flags = list(scale_result.flags)
    if (
        value_type == "currency"
        and scale_result.unit is None
        and scale_result.scale_source
        in (
            "column_header",
            "page_header",
        )
    ):
        flags.append("ambiguous_unit")
    if flags:
        flag_log.log_all(stage, element_id, flags, detail=scale_result.scale_context)

    value = FactValue(
        raw=scale_result.raw,
        normalized=scale_result.normalized,
        unit=scale_result.unit,
        value_type=value_type,
        scale_multiplier=scale_result.scale_multiplier,
        scale_source=scale_result.scale_source,
    )
    return Fact(
        entity=entity,
        attribute=attribute,
        value=value,
        location=location,
        confidence="extracted",
        section=section,
        flags=flags,
    )


def log_table_element_flags(
    flag_log: FlagLog,
    file: str,
    table_element: TableElement,
    stage: str = _STAGE_ELEMENT_PROCESSING,
) -> None:
    """Record a TableElement's own flags (e.g. ragged_table_rows) to the
    structured per-run log, independent of whether any cell in it became a
    claim -- the monitoring scope is the interpreted element, not just what
    got cited."""
    element_id = f"pdf:{file}:p{table_element.page}:table"
    flag_log.log_all(stage, element_id, table_element.flags)


def log_chart_element_flags(
    flag_log: FlagLog,
    file: str,
    chart_element: ChartElement,
    stage: str = _STAGE_ELEMENT_PROCESSING,
) -> None:
    """Record a ChartElement's own flags (always chart_data_not_extracted)."""
    element_id = f"pdf:{file}:p{chart_element.page}:chart"
    flag_log.log_all(stage, element_id, chart_element.flags)
