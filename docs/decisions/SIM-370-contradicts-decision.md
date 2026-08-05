# SIM-370: within-page CONTRADICTS — keep the parser's emission, fix the doc

**Decision: (a)** — update the design doc's §4 lock to accept within-page
`CONTRADICTS` from the parser's E1 reducer. No parser code change.

**Rationale:**

- The E1 reducer's within-page `contradicts` emission
  (`extract_service.py` building `type="contradicts"`; `emit.py`'s
  `EdgeType = Literal["same_fact", "contradicts"]`) is deliberate, per its
  own docstring: the disagreement between tiers on the same page is signal
  worth keeping, not noise to suppress.
- E3 period resolution (SIM-345) already makes the within-page grouping
  safe — the failure mode the doc's original lock was guarding against
  (grouping claims from different periods as if they contradicted) is
  handled by a control that didn't exist when the lock was written.
- The doc's §4 "confirmed against the code / there is no cross-claim logic"
  note predates the reducer's contradicts pass — it was accurate against an
  earlier commit (`main @ f5d60fe`) and is now stale, not a considered
  restriction the code violates.
- Changing the code instead (option b) would mean retiring working,
  deliberately-written tests and narrowing CONTRADICTS to alpha-only for no
  functional gain — the doc is what's out of date here, not the parser.

**Consequence for SIM-371 (3a reconciliation):** the cross-page reconciliation
pass must not double-count a `contradicts` edge the E1 reducer already wrote
within-page for the same claim pair — it needs to check for an existing
within-page edge before writing its own cross-page one. Implemented as part
of SIM-371 (see `app/services/reconciliation.py`).

**What this decision does NOT do:**

- **Does not edit the actual design doc.** The design doc ("Plan for
  everything downstream of parsing") is external to both
  `Simpero_AI_Gov_Alpha` and `Simpero_Gov_AI_Services` — no file for it
  exists in either repo, and no Linear document is linked from SIM-368 or
  its children. This record is the decision; the team needs to apply it to
  the doc itself by hand.
- **Does not touch parser code.** `extract_service.py` / `emit.py` live in
  the sibling repo `Simpero_Gov_AI_Services`, out of scope for this session
  (see the branch's scope note). Decision (a) requires no parser change
  anyway, so there is nothing pending there beyond the doc update above.
