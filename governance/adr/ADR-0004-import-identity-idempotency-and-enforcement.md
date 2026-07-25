# ADR-0004: Import Identity, Idempotency and Enforcement

- ID: ADR-0004
- Status: Approved
- Date: 2026-07-25
- Epic: EPIC-RDEC-2026-07-VERIFIED-FIXES
- Owner: Enterprise Architect
- Amends: ADR-0002 (lines 27 and 30)

## Context

Approved under G1 authority by the Enterprise Architect. This ADR amends ADR-0002 line 27 and
line 30. Both amendments are reproduced verbatim in ADR-0002 under `## Amendments and G1 Rulings`.
Where this ADR and ADR-0002 conflict, this ADR governs from 2026-07-25.

Two of the proven defects are not deviations from the approved design. They are the faithful
implementation of ADR-0002's own decisions. That is the reason this needs an ADR rather than a
patch: fixing them means changing a signed-off decision, and a Principal who "fixes the bug" without
that change is silently overriding approved architecture.

**Finding C2 is ADR-0002 line 30 working as designed.** Line 30 reads "Match by record identifier
first and a conservative natural key second." `app/data_management.py:459 _find_existing` implements
exactly that: `:460-467` tries `session.get(spec.model, int(item_id))` before any natural key. A CSV
carrying `id=1` overwrote an unrelated live contract wholesale
(`"Passenger Insight Framework - Work Order 7"` became `"TOTALLY DIFFERENT CONTRACT"`). The preview
never disclosed which record would be destroyed, because `_display_name` (`:476`) is called at
`:556` with the **incoming** `values`, so the preview shows the attacker's name, not the victim's.

**Finding C5 is ADR-0002 line 27 working as designed.** Line 27 requires "Neutralise spreadsheet
formula prefixes in CSV exports." `_safe_csv_value` (`:339`) prefixes an apostrophe to any value
starting with `= + - @ TAB CR`. Because `-` is in that set, `-500` exports as `'-500` and Excel
treats it as text. CSV is the stated Finance/Ayming review format (line 27), so every negative
figure in a review pack is unusable for arithmetic. Narrowing the rule deliberately weakens a
signed-off security control, so it is ruled on here and the residual risk is stated for G5.

Four further findings sit in the same subsystem: an in-file identifier admitted to the valid-foreign-key
set (C1), a replayable preview payload (C3), an unenforced `importable` flag (C4), and the question
of whether post-import entitlement resync breaches ADR-0002 line 40 (C6).

## Decision

### D1. Import identity — ADR-0002 line 30 is amended

An identifier in an uploaded file is an assertion of identity in the **exporting** database's
namespace. It is interpreted in the **live** database's namespace. There is no shared identifier
space between them, and the realistic round trip (export to Finance or Ayming, edit, return) crosses
that boundary every time. Identifier-first matching is therefore not "conservative"; it is the least
conservative option available, and C2 is its inevitable consequence.

**Ruled: natural key first. Identifier matching becomes an explicit, operator-enabled mode that is
off by default.**

Amended ADR-0002 line 30, verbatim:

> Match by conservative natural key first. A record identifier present in an uploaded file is
> treated as untrusted data, not as identity: it never selects a record to update and is never
> written to the database, unless the operator explicitly chooses the separate "restore by
> identifier" mode. Restore by identifier is disabled by default, requires the same deliberate
> operator enablement as purge, and its preview must name the live record that each incoming
> identifier would overwrite. Datasets without a natural key can be created but are never updated by
> a default import. Revalidate fields and links immediately before applying changes.

Implementation, precisely:

**D1.1** `_find_existing` (`:459`): the identifier branch (`:460-467`) is removed from the default
path. Add a parameter `allow_identifier_match: bool = False`, plumbed from the import mode. Only
that branch may call `session.get`.

**D1.2** In the default path, `_clean_row` (`:433`) **drops the `id` key entirely**, extending the
existing empty-id skip at `:443-444` to all values. `id` then never reaches `model_validate`
(`:514`), never reaches `session.add(candidate)` (`:621`), and cannot be inserted as an explicit
primary key. This single change closes the id-preservation hazard at its source.

**D1.3** Datasets with an empty `natural_key` — `opportunity_requirements` (`:207`),
`opportunity_signals` (`:215`), `quality_questions` (`:232`), and `opportunity_documents` — become
create-only under a default import. They can never be matched, therefore never updated. This is an
accepted consequence and must be stated in the preview UI for those datasets: "Records in this area
are always added as new. Importing the same file twice will create duplicates."

**D1.4 — mandatory preview disclosure.** This is what C2 actually proved missing, and it applies to
**both** modes. Every row with status `update` must carry, in the preview payload and in the
rendered table:

- `existing_id` — the live record's identifier
- `existing_display` — the natural-key display of the **live record being changed**, computed from
  `existing`, **not** from the incoming `values`. `_display_name` must be split into
  `_incoming_display(spec, values)` and `_existing_display(spec, record)`. One function taking
  ambiguous input is the direct cause of the non-disclosure; do not fix this with a flag argument.
- `changed_fields` — a list of `{"field": <label>, "before": <str>, "after": <str>}` for every field
  where `existing` differs from the merged candidate, each value truncated to 120 characters.

An `update` row whose `changed_fields` is empty renders as `no change` and is not counted as an
update in the summary.

**D1.5 — disclosure is enforced, not merely displayed.** `apply_import` must refuse to update any
row whose preview payload does not carry an `existing_id` equal to the identifier of the record it
is about to write. Mismatch raises `DataOperationError` and the whole import rolls back.

### D2. C1 — the split is confirmed, with one correction

**C1a confirmed as a pure invariant restoration, no ADR needed.** The invariant:

> `_known_ids` may contain only identifiers already committed in the database at the time the plan
> is built.

Delete `app/data_management.py:491-496` (the loop that adds identifiers declared in the file). The
proven defect — preview reporting zero errors while apply created a contract referencing customer
906, which does not exist — follows directly from those six lines: the declared id satisfies the
foreign-key check at `:537`, then the parent is matched by natural key or skipped, its declared id is
discarded, and the child dangles.

**Correction to the split.** Deleting those lines with no replacement makes multi-dataset bundle
restore impossible: a file containing a customer and its contract has no way to express "this
contract belongs to that new customer", because under natural-key-first the child row carries only an
integer. That link-resolution rule **is** design-altering, so it is pulled into this ADR rather than
left to G2 to invent. C1a remains no-ADR only for the deletion itself.

**D2.1 — link resolution.** `build_import_plan` already iterates `for spec in DATASETS` (`:506`) in
dependency order. Maintain an in-memory `id_remap: dict[tuple[str, int], int]` keyed on
`(dataset_key, declared_id)`.

- **L1 (in-file link).** A child's foreign-key integer is matched against the `id` values declared by
  in-file parent rows. If exactly one in-file parent row declares that identifier, the link resolves
  to whatever database identifier that parent row itself resolves to — its natural-key match if it
  matched, or the identifier assigned when it is created. Resolution completes at apply time, after
  the parent is flushed.
- **L2 (never dangle).** A foreign-key integer matching neither a live record nor exactly one in-file
  declared identifier is a row **error**. Preview message:
  `"<Field label> does not match a record in this file or in the Hub."`
- **L3 (live reference).** A foreign-key integer matching a live record's identifier is accepted as a
  *reference*. Referencing an existing parent is safe and is categorically different from using an
  identifier as the identity of the row being written. **In-file declared identifiers take precedence
  over live identifiers**, and where both are possible the preview must disclose, by name, which
  parent was chosen.
- **L4 (apply-time re-check).** Immediately before each `session.add`, `apply_import` resolves every
  foreign key through `id_remap` and asserts the target row exists. Failure raises; the existing
  `try/except` at `:596-635` rolls back.

**C1b answered.** Identifier-preserving import remains supported, but only as the opt-in,
operator-enabled "restore by identifier" mode of D1, where `id_remap` becomes the identity map. It is
the same line-30 question and is resolved by the same amendment.

### D3. C5 — CSV neutralisation, ADR-0002 line 27 is amended

Amended ADR-0002 line 27, verbatim:

> Treat JSON as the restore/re-import format. Neutralise spreadsheet formula prefixes in CSV exports
> by prefixing an apostrophe to any value beginning with `=`, `+`, `@`, TAB, CR, or LF, and to any
> value beginning with `-` that is not a plain signed number matching
> `^-(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$`. Signed numeric values export as numbers so that
> Finance and Ayming reviewers can total them. Describe CSV as a review format, never as a restore
> format.

Note LF (`\n`) is **added** to the neutralised set; `_safe_csv_value:345` covers TAB and CR but not
LF today. The narrowing is not a net reduction in coverage.

**Residual CSV-injection risk — stated explicitly for G5 review.**

1. The `-` exemption is a deliberate, documented reduction in defence depth. It is not exploitable as
   specified, because the exemption is decided by a fully anchored match on the **whole** cell: a
   value that satisfies `^-(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$` contains no letters and none of
   `! ( ) , : ; \ | + = @ "`, so it cannot carry formula syntax, a DDE payload, or a cell reference.
   The risk lives entirely in future relaxation of that regex.
   **Binding guardrail:** the pattern is anchored `^...$`; it must never be changed to a `search`;
   it must never permit leading or trailing whitespace, thousands separators, currency symbols, or
   Unicode minus signs. Required negative tests, each asserting the value **is** neutralised:
   `-1+cmd|'/c calc'!A0`, `-500,=1+1`, `- 500`, `-5-5`, `-1e`, `-`, `−5` (Unicode minus).
   Required positive tests, each asserting the value is **not** neutralised: `-500`, `-1.5`,
   `-0.25`, `-1.5e3`, `-.5`.
2. Neutralisation is prefix-only. Formula content after a separator inside a quoted field is not
   neutralised. This is inherent to the CSV-injection model — spreadsheet applications evaluate on
   the leading character — and remains accepted.
3. The export writes `utf-8-sig` (`:369`), so a BOM precedes only the first header cell. Header names
   come from `spec.model.model_fields` (`:363`) and are code-controlled, so a BOM-shadowed payload is
   unreachable. Accepted.
4. The compensating control is that CSV is a **review** format, never a restore format. That sentence
   must survive in amended line 27 and in `csv_export_zip_bytes`'s manifest `purpose` (`:356`).
5. Data fidelity for text remains reduced: a company genuinely named `=Formula Limited` still exports
   as `'=Formula Limited`. Accepted and already asserted.

**Test ruling.** `tests/test_data_management.py:34
test_selected_json_and_csv_exports_are_review_safe` asserts at `:50` that `=Formula Limited` exports
as `'=Formula Limited`. That is the `=` case, which this amendment leaves **completely unchanged**.
The test therefore requires **no edit**, must continue to pass unmodified, and serves as the positive
control. The Delivery Lead's constraint that it may not be touched until line 27 is amended is
satisfied without touching it at all. New coverage goes in a new test alongside it.

### D4. C3 — single-use preview, without schema

ADR-0002 `:54` forbids schema migration, so the used-set cannot be a table. `encode_import_payload`
(`:566`) currently returns unauthenticated base64 of the plan, posted back as a plain form field, so
re-posting re-applies. Proven: one preview applied three times created three rows.

**Ruled: an in-process nonce set with a content hash and an expiry. No database, no new dependency,
no persisted secret.**

- The payload JSON gains `"nonce"` (`secrets.token_hex(16)`) and `"issued_at"` (integer Unix time).
- Module-level `_ISSUED_PREVIEWS: dict[str, tuple[int, str]]` maps nonce to
  `(issued_at, sha256 of the canonical payload JSON)`. Bounded at 32 entries, oldest evicted;
  entries older than 30 minutes are dropped on every access.
- A new `consume_import_payload(encoded) -> tuple[str, dict]` replaces the direct
  `decode_import_payload` call on the apply route. It decodes, requires the nonce to be present,
  requires `sha256(payload) == stored hash`, requires age <= 30 minutes, and **pops** the nonce
  before returning. A second POST of the same payload raises
  `DataOperationError("This import preview has already been applied. Preview the file again.")`.
- **The pop is unconditional and happens before any write**, including when the subsequent
  `apply_import` raises. A preview is single-use whether or not it succeeded. This is the fail-closed
  choice; restoring the nonce on failure reopens the race and is prohibited.
- An application restart invalidates outstanding previews. That is acceptable and correct for a local
  single-user MVP, and it degrades fail-closed. The user-facing message must be the same expired
  message already used at `:579`.

Rejected alternatives, recorded so they are not re-litigated:

- **Signed payload with a used-set** — still needs the used-set, and adds a persisted secret to
  manage, which ADR-0001 `:110` ("No secrets") rules out for this MVP.
- **Content hash alone** — detects tampering but not replay of an untampered payload, which is
  precisely the proven defect. Insufficient by construction.

### D5. C4 — `importable` enforcement and defence-in-depth layering

`spec.importable` is checked in `parse_import_file` (`:390`, `:419`) but nowhere else. A forged
payload posted straight to the apply route bypasses parsing entirely and wrote into `audit_events`
(`:240`, `importable=False`) — the table the purge scopes deliberately preserve (ADR-0002 `:39`).

**Confirmed: the fix itself needs no new architectural decision.** But the invariant does, so it is
recorded here:

> `importable` is a property of the dataset, enforced at every boundary that can write, not at the
> parse boundary.

Four layers, all required:

1. `parse_import_file` — keep the existing checks.
2. `decode_import_payload` (`:577`) — currently validates only `mode` and `isinstance(datasets, dict)`
   (`:584-588`). It must additionally reject unknown dataset keys and any key whose spec has
   `importable=False`.
3. `build_import_plan` — inside `for spec in DATASETS` (`:506`), a non-empty payload for a
   non-importable dataset emits an **error row**, not an exception, so the preview *shows* the
   rejection instead of returning a 500.
4. `apply_import` — after re-planning, before the write loop, a hard
   `raise DataOperationError` for any non-importable dataset key. Reaching this point means layers
   1-3 were bypassed, so it fails loudly.

**Additional defect found while ruling, in scope for the same increment:** `build_import_plan`
iterates `DATASETS`, not the payload, so a payload with a mistyped dataset key is silently ignored and
the import reports success having written nothing. Layer 2 closes this — unknown keys become an error
at decode.

Regression test must forge the payload directly, bypassing `parse_import_file` exactly as the finding
did, and assert both that `DataOperationError` is raised and that the `AuditEvent` row count is
unchanged.

### D6. C6 — post-import entitlement resync does not breach ADR-0002 line 40

Line 40: "No import, cleanup, or purge operation may submit data externally or change RDEC
eligibility logic."

**Ruled: no breach. Resync is required, not merely permitted. Line 40 is not amended.**

- The clause governs **logic**, not **facts**. `sync_entitlement_for_project`
  (`app/services.py:216`) reads facts and applies `assess_entitlement` (`:150`), which is unchanged
  code driven by unchanged `entitlement_rules.yml`. No rule version moves. No logic changes.
- The opposite reading is the dangerous one. If an import may change
  `Customer.corporation_tax_status` or `Contract.customer_intended_or_contemplated_rd` but must not
  recompute the derived `EntitlementAssessment`, then the Hub knowingly displays an assessment
  computed from facts that no longer exist. A decision-support tool presenting a conclusion it knows
  to be unsupported is a far graver breach of ADR-0002 `:59` and of the README boundary than
  recomputation could ever be.

Mandatory guardrails on the resync:

1. It runs **after** the import transaction commits, as a separate, explicitly audited step. It must
   never be the reason an import partially applies.
2. Each resync writes an `AuditEvent` — `sync_entitlement_for_project` already does at `:257` — and
   the summary must carry the words `following previewed import`.
3. It is confined to projects whose own facts, or whose customer or contract facts, the import
   touched. It must not walk the whole table.
4. The import result shown to the user must state how many entitlement reviews were recalculated. The
   recalculation is never silent.

**Related defect routed to G2, not an ADR change:** `calculate_project_score:369` calls
`sync_entitlement_for_project`, which **commits** (`:266`), from inside a read-only GET dashboard
render (`app/main.py:313` -> `services.py:601`). A GET that commits is both a performance cost and a
correctness hazard once foreign-key enforcement is live. Ruled in ADR-0005 D6.

### D7. Import-preview error shape — ratified before either side builds

```python
@dataclass(frozen=True)
class ImportIssue:
    field: str      # user-facing field label, or "" for a row-level issue
    message: str    # plain business language, one sentence, ends with a full stop
    code: str       # stable machine code, snake_case
```

Preview row shape:

```
{"dataset_key", "dataset_label", "row_number", "display", "existing_id",
 "existing_display", "status", "changed_fields", "issues": [ImportIssue, ...], "values"}
```

- `errors: list[str]` (`:558`) is replaced by `issues: list[ImportIssue]`. Both producers —
  `_clean_row` (`:436`) and `build_import_plan` (`:517`, `:527`, `:541`) — return `ImportIssue`.
- **Raw Pydantic text and database column names must never reach `message`.** Line `:517-519`
  currently emits `f"{'.'.join(loc)}: {msg}"`, which leaks both. Replace with a translation table
  keyed on the Pydantic error `type`, with a safe fallback of
  `"<Field label> could not be understood."` Field labels come from a new additive
  `DatasetSpec.field_labels: dict[str, str] = field(default_factory=dict)`.
- `code` is the contract a frontend may branch on. `message` is display-only; the frontend must never
  parse it. `field` carries a label, never a column name.
- **Escaping is mandatory.** `app/form_utils.py:85` builds `f"<li>{error}</li>"` with **no escaping**,
  and issue messages are derived from user-uploaded content (`data_management.py:436` interpolates an
  uploaded column name into `"Unknown column '<name>'."`). This is a live HTML-injection sink today.
  Every issue message must render through Jinja autoescaping in templates, and
  `validation_error_response` must apply `html.escape()` to each item. Non-negotiable; G5 will check
  it.
- Contract test: a fixture list of `ImportIssue` renders in the preview template without key errors;
  and a Pydantic failure induced on every field type of one dataset produces zero messages containing
  `"Input should be"`, `"validation error"`, or any raw column name.

## Architecture Baseline

Unchanged from ADR-0002. **No schema migration.** Every mechanism here is in-memory, in-code, or an
additive dataclass field. No new dependency, no new table, no new column, no persisted secret, no
background worker.

## Guardrails

- Preserve `Requires competent professional and tax review.` in every export bundle and manifest
  (`data_management.py:328`, `:357`).
- Imports still never delete records absent from the file (ADR-0002 `:60`).
- Purge remains disabled by default and still preserves audit history (ADR-0002 `:37`, `:39`).
- Uploaded content is never executed and uploaded paths are never opened (ADR-0002 `:31`).
- Size and row limits stay (`MAX_IMPORT_BYTES`, `MAX_IMPORT_ROWS`, `:51-52`).
- Every applied import remains audited (ADR-0002 `:32`).
- Restore-by-identifier requires the same deliberate operator enablement as purge, and must never be
  a release default.

## Consequences

Positive:

- An uploaded identifier can no longer silently destroy an unrelated live record.
- The operator sees, before applying, exactly which live record changes and which fields move.
- A preview can be applied once. Replay is structurally impossible, not merely unlikely.
- Non-importable datasets are protected at four layers rather than one.
- Bundle restore keeps working because links resolve explicitly instead of by coincidence.

Negative and risks:

- Datasets without a natural key become create-only, so repeat imports of those areas duplicate.
  Disclosed in the UI (D1.3).
- In-memory nonces do not survive a restart, so a preview held open across a restart must be redone.
  Fail-closed and acceptable for a local single-user MVP.
- Restore-by-identifier still carries the C2 hazard by design; it is gated behind deliberate operator
  enablement and mandatory disclosure rather than removed.
- The `-` narrowing is a real reduction in CSV defence depth. Bounded by an anchored regex and eight
  named tests; residual risk stated above for G5.

Migration and rollback: no data migration. Rollback is a code revert plus reverting the two amended
ADR-0002 lines. No stored record changes shape.

## Verification

1. `docker compose run --rm app pytest -q` green; count reported; no test deleted or weakened.
2. **C2 regression:** a CSV carrying `id=1` and a different contract name, applied against a seeded
   database, leaves `"Passenger Insight Framework - Work Order 7"` byte-identical and creates a new
   contract instead. Assert on the live record, not on the preview.
3. **Disclosure:** an update-mode import produces a preview row whose `existing_display` is the
   **live** record's name and whose `changed_fields` names every field that moves. A test asserting
   `existing_display != incoming display` for a deliberately renaming row.
4. **C1 regression, both modes:** the exact proven file — a contract referencing customer 906
   declared in-file, where the customer matches an existing record by natural key. Preview must
   report an error or resolve the link correctly; apply must never create a contract whose
   `customer_id` has no row. Assert by querying for orphans after apply.
5. **C3 regression:** capture one preview payload, POST it three times. First returns applied counts;
   second and third raise the already-applied error; the row count after all three equals the row
   count after the first.
6. **C4 regression:** forge a payload for `audit_events` and POST it directly to the apply route.
   Assert `DataOperationError` and an unchanged `AuditEvent` count. Repeat for a mistyped dataset key
   and assert an error rather than a silent success.
7. **C5:** `tests/test_data_management.py:34` passes **unmodified**; new test covers the five positive
   and seven negative cases named in D3.
8. **C6:** an import that changes a customer's corporation-tax status produces a recalculated
   `EntitlementAssessment`, an `AuditEvent` whose summary contains `following previewed import`, and
   a user-visible count. A project untouched by the import is not recalculated.
9. **Error shape:** the D7 contract test, plus a test that an uploaded column named
   `<img src=x onerror=alert(1)>` renders escaped in both the preview template and
   `validation_error_response`.
10. `docker compose run --rm app python -m compileall app` passes.
11. **UAT path (user-facing):** a live end-user session covering export to CSV, opening it in Excel
    and confirming negative figures total correctly; and a preview-then-apply import where the
    operator confirms the disclosed record name matches what they expected. Synthetic capture is
    insufficient; G4 requires a real end-user pass before G6.
12. **G5 conditional:** this ADR narrows a signed-off security control and adds an escaping mandate.
    G5b runtime security review is required before G6 and must cover the D3 residual-risk statement
    and the D7 escaping fix.

## ARB checklist

- Traces to epic: yes — EPIC-RDEC-2026-07-VERIFIED-FIXES, findings C1-C6 and the preview error shape.
- Baseline updated: yes — ADR-0002 lines 27 and 30 amended verbatim, with an amendment note added to
  ADR-0002.
- NFRs preserved: yes — no schema change, no dependency, no secret, no persisted state. Nonce set is
  bounded at 32 entries.
- Consumers identified: `app/data_management.py`, `app/main.py` (import preview/apply routes),
  `app/templates/data_management.html`, `app/form_utils.py`, `app/services.py`
  (`sync_entitlement_for_project`), `tests/test_data_management.py`.
- Cross-cutting: partly. It amends an approved ADR and weakens a security control, so G5 must review
  the ruling. No CTO escalation required; the change stays inside the local data-management domain
  that ADR-0002 already owns.
