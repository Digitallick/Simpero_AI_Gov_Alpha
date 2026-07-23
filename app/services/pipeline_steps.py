# Ported from Simpero_AI_Gov_Web's src/shared/pipelineSteps.ts — must stay in
# sync with that file's PIPELINE_STEPS list and AnalysisJobPhase union.
# Phase 1 has no job model yet, so the only caller here is the "no_job" shape
# (computeStepStatuses(None, False) equivalent, i.e. every step "pending").
# Phase 2's real job model reuses this list once `phase` values start moving.

PIPELINE_STEPS: list[dict[str, str]] = [
    {
        "phase": "parsing",
        "title": "Parsing document",
        "detail": "Reading structure and extracting text",
    },
    {
        "phase": "classify",
        "title": "Classifying document",
        "detail": "Identifying document type and sections",
    },
    {
        "phase": "pass1",
        "title": "Verifying claims",
        "detail": "Extracting and verifying claims against the source",
    },
    {
        "phase": "pass2",
        "title": "Cross-checking sources",
        "detail": "Deeper source verification for unresolved claims",
    },
    {
        "phase": "governance",
        "title": "Governance review",
        "detail": "Checking compliance flags and policy signals",
    },
    {
        "phase": "ofac",
        "title": "OFAC screening",
        "detail": "Sanctions and watchlist checks",
    },
    {
        "phase": "pass3_compose",
        "title": "Drafting analysis",
        "detail": "Composing memo sections from verified evidence",
    },
    {
        "phase": "pass4_score",
        "title": "Scoring deal",
        "detail": "Running mandate fit and investment scoring",
    },
    {
        "phase": "finalize",
        "title": "Finalising",
        "detail": "Saving the analysis and memo",
    },
]


def no_job_steps() -> list[dict[str, str]]:
    """computeStepStatuses(null, false) equivalent: every step "pending"."""
    return [{**step, "status": "pending"} for step in PIPELINE_STEPS]
