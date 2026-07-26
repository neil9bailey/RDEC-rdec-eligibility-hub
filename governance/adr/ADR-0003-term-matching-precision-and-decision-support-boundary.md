# ADR-0003: Term-Matching Precision and the Decision-Support Boundary

Status: Approved
Date: 2026-07-25
Epic: EPIC-RDEC-2026-07-VERIFIED-FIXES
Owner: Enterprise Architect

## Context

Approved under G1 authority by the Enterprise Architect. This ADR is design-altering: it changes
how free-text signals are classified and it changes what the Hub is permitted to assert about a
project. G2 implementation may begin within the bounds set here.

Two proven findings share one root cause: naive substring containment used as a classification
decision.

- `app/services.py:118` defines `contains_any(value, terms)` as `term.lower() in lowered`. It is
  called at `:306` (advance) and `:317` (uncertainty). A hit appends to `blockers`.
  `app/services.py:401` then reads `if blockers: rating = red` **unconditionally**, overriding the
  numeric score. A substring hit is therefore not an indicator; it is a verdict.
  Proven: 3 of 4 realistic R&D descriptions were hard-blocked. `"no commercially available product"`
  matches the term `commercial`; `"procurement-available"` matches the term `procurement`.
- `app/framework_intelligence.py:838 requirement_themes_for_text` uses the same unanchored
  containment against `REQUIREMENT_PATTERNS` (`:55`). Proven: `"Station platform resurfacing and
  drainage works"` is classified as `software development` because the pattern `platform` (`:64`)
  appears in it.

The obvious fix is wrong. Word-boundary anchoring cures only the first case. `\bprocurement\b`
still matches `procurement-available`, because a hyphen **is** a word boundary. And `\bplatform\b`
still matches `Station platform`, because there the token is a genuine standalone word used in a
different sense. A naive `\b` change would pass a test written against the first case, close the
finding, and leave two release-blocking behaviours live. This ADR exists to prevent that outcome.

Baseline position. README states the tool "does not decide whether a claim is valid" and every
output carries `Requires competent professional and tax review.` ADR-0001 (`:70`) requires
"confidence/strength labels expressed as indicators, not conclusions". ADR-0002 (`:59`) forbids
presenting a workflow stage as a tax, accounting, or eligibility approval. An automatic,
unappealable RED derived from a substring is the same category error as an automatic approval,
pointed the other way.

## Decision

### D1. What a blocker is allowed to be

**A blocker may only assert an objective, machine-checkable fact about record completeness. A
blocker may never assert a judgement about the technical merit, character, or wording of free
text.**

This is the governing rule. Apply it to every existing and future entry in
`app/rules/blockers.yml`.

Remain blockers (all are absence-of-record facts): `missing_field`, `missing_advance`,
`missing_uncertainty`, `missing_competent_professional_signoff`, `missing_evidence`,
`missing_costs`, `missing_accounting_period`, `entitlement_blocked`, `aif_timing_risk`.

Cease to be blockers (both are judgements about wording): `non_technical_advance`,
`non_technical_uncertainty`. They become **review flags** surfaced as warnings.

### D2. Consequence for scoring — do not add new capping code

When a negative term matches, the narrative text exists, so the existing `else` branches at
`app/services.py:308-313` and `:319-328` run normally and award their points. The review flag is
appended to `warnings`.

The existing cap at `app/services.py:398-400`
(`if warnings and not blockers and score >= green_min: score = green_min - 1`) then guarantees that
a project whose only defect is commercial-flavoured language **can never present as green**; it is
capped at amber, `review required`. This is the correct decision-support posture and it requires no
new capping logic. Do not write any.

### D3. The canonical matcher

One new module, `app/text_matching.py`. Standard library only. It must not import from
`app.services` or `app.framework_intelligence` (`framework_intelligence` already imports `services`
at `:33`; the reverse would create a cycle). Dependency direction is one-way into `text_matching`.

Public surface:

```python
@dataclass(frozen=True)
class TermMatch:
    term: str        # the configured term that matched
    start: int       # index into the normalised text
    end: int
    excerpt: str     # matched span with up to 40 chars of context each side

def normalise(text: str | None) -> str
def find_matches(text: str | None, terms: Sequence[str], stop_phrases: Sequence[str] = ()) -> list[TermMatch]
def matched_terms(text: str | None, terms: Sequence[str], stop_phrases: Sequence[str] = ()) -> list[str]
```

**Stage 0 — normalise.** Lowercase, Unicode NFKC, collapse runs of whitespace to a single space,
strip, then pad with one leading and one trailing space. **Do not strip or replace hyphens.**
Replacing `-` with a space would turn `procurement-available` into `procurement available` and make
the false positive *worse*.

**Stage 1 — whole-token match with hyphen-compound awareness.** For a term whose whitespace-separated
words are `w1..wn`, the compiled pattern is exactly:

```
(?<![a-z0-9-]) re.escape(w1) \s+ re.escape(w2) ... \s+ re.escape(wn) (?![a-z0-9-])
```

The hyphen inside both lookarounds is the load-bearing detail. It is what makes this different from
`\b`, and it resolves both proven cases:

| Text | Term | `\b` result | Stage 1 result | Correct? |
|---|---|---|---|---|
| `no commercially available product` | `commercial` | no match | no match | yes |
| `procurement-available option` | `procurement` | **match (wrong)** | no match | yes |
| `Commercial implementation planning` | `commercial` | match | match | yes, true positive |
| `procurement of standard items` | `procurement` | match | match | yes, true positive |

Compiled patterns are cached with `functools.lru_cache` keyed on the term string.

**Stage 2 — stop-phrase suppression.** A correct whole-token hit can still be a false positive when
the term sits inside a phrase that means the opposite, e.g. `"no commercial solution existed"` or
`"beyond commercial off-the-shelf capability"`. Both produce a Stage 1 hit on `commercial` and both
are false positives.

Stop phrases are matched with the identical Stage 1 rule. **A term hit is discarded if any
stop-phrase match span contains it** (`stop.start <= hit.start and hit.end <= stop.end`).

**Stage 3 — return evidence, not a boolean.** `find_matches` returns `TermMatch` objects. The
user-facing flag must name the matched term and quote the excerpt. This is what converts a verdict
into a reviewable signal and it satisfies ADR-0001 `:67` ("signal rationale and matched
terms/themes"). A generic label listing all five configured terms, as `blockers.yml:10` does today,
does not tell a reviewer which term fired or where; that is not acceptable for a review flag.

### D4. One shared matcher — mandatory, both call sites

Both consumers use `app.text_matching.find_matches`. No parallel implementation, no wrapper that
re-implements normalisation.

- `app/services.py`: **delete `contains_any` (`:118`) entirely.** It must not survive as a private
  helper. Conformance proof: a grep over `app/` returns zero occurrences of `contains_any`.
- `app/framework_intelligence.py:838 requirement_themes_for_text`: replace the `pattern in lower`
  test with `find_matches`.
- Anti-drift test (structural, not behavioural): assert the two modules resolve to the *same*
  function object, and add a parity test running one string through both call paths and asserting
  identical match spans.

### D5. Framework theme matching

`\b` and Stage 1 both fail the proven case, because `platform` in `"Station platform resurfacing"`
is a genuine standalone token in the wrong sense. Semantic disambiguation is not available:
ADR-0002 `:54` forbids an external LLM. Three deterministic rules instead:

**D5.1** Theme matching uses the shared matcher (Stages 0-2), so compounds behave consistently.

**D5.2** Theme-level stop phrases, held in code beside `REQUIREMENT_PATTERNS`. Initial required set
for `software development` / pattern `platform`: `station platform`, `platform resurfacing`,
`platform edge`, `platform extension`, `passenger platform`, `platform lengthening`.

**D5.3 — required corroboration.** Designate a `WEAK_PATTERNS` set of generic single-word patterns:
`platform`, `data`, `asset`, `security`, `network`, `incident`, `interface`, `transport`. If a
theme's only matched pattern is a single member of `WEAK_PATTERNS`, then:

- the `ExtractedRequirement` is created with `confidence="low"` (not `"medium"`, `:872`), and
- **no `RDECOpportunitySignal` is created** — the `theme in RDEC_SIGNAL_THEMES` branch at `:888` is
  additionally gated on corroboration.

Applied to the proven case, `"Station platform resurfacing and drainage works"` matches no other
`software development` pattern and no pattern of any other theme, so it yields exactly one
low-confidence requirement and **zero** R&D signals. That is the required conformance outcome.

`REQUIREMENT_PATTERNS` stays a code constant. It is procurement heuristics, not RDEC rule logic, so
ADR-0001 `:108` does not require it in YAML. Moving it is out of scope.

### D6. Rule file structure

`app/rules_engine.py:10-16 REQUIRED_RULE_KEYS` is enforced at startup by `validate_all_rules()`.
Renaming or removing `negative_advance_terms` / `negative_uncertainty_terms` raises `ValueError`
before the app serves a request.

**Keep both keys, their names, and their list-of-strings shape. All additions are additive and
optional.**

Add to `app/rules/eligibility_weights.yml`:

```yaml
review_flag_stop_phrases:
  advance:
    - "no commercial"
    - "not commercial"
    - "beyond commercial"
    - "beyond existing commercial"
    - "no procurement"
    - "beyond procurement"
    - "not merely project management"
  uncertainty:
    - "not only commercial"
    - "beyond commercial"
    - "more than implementation planning"
```

Add to `app/rules/blockers.yml`: remove the two entries `non_technical_advance` and
`non_technical_uncertainty` from `automatic_blockers`, and add a new optional top-level list:

```yaml
review_flags:
  - code: "non_technical_advance_review"
    label: "Advance wording may describe commercial, aesthetic, project management, procurement, or internal-learning work. Review with a competent professional."
  - code: "non_technical_uncertainty_review"
    label: "Uncertainty wording may describe commercial, budgetary, resourcing, customer-adoption, or implementation-planning risk. Review with a competent professional."
```

Binding constraints on this change:

- **Neither `review_flag_stop_phrases` nor `review_flags` may be added to `REQUIRED_RULE_KEYS`.** A
  missing key must degrade to "no stop phrases" / a generated fallback label, never a startup crash.
  An operator editing a rule file must not be able to brick the application (ADR-0001 `:108`).
- New accessors `Rules.review_flag_stop_phrases(kind)` and `Rules.review_flag_label(code)` follow the
  existing defaulting pattern at `rules_engine.py:41` and `:52` — `.get(...)` with a fallback, never
  a `KeyError`.
- Bump `version:` in `eligibility_weights.yml:1` and `blockers.yml:1` to `"2026-07-25"` and update
  `review_status:`. `rules_version_summary()` is surfaced in the UI (`app/main.py:152`); changing
  classification semantics under an unchanged version string is a traceability defect.
- Grep `app/` and `tests/` for `non_technical_advance` and `non_technical_uncertainty` and update
  every call site. Do not assume `services.py` is the only one.

### D7. Exact expected outcome for the seeded red project

Seed record: `app/seed.py:406` `"Standard Dashboard Migration to Managed Cloud"`.
`field_of_science_or_technology=""`;
`advance_sought="Internal learning and standard cloud migration delivery for a public sector reporting dashboard."`;
`scientific_or_technological_uncertainties="Commercial implementation planning, resourcing, and customer adoption risks."`;
`wider_field_explanation="No wider-field advance identified."` (33 chars);
`competent_professionals_could_not_resolve=""`; `experiments_prototypes_tests=""`;
`accounting_period_id` is set; `rd_start_date`, `rd_end_date`, `boundary_explanation` all set.

After implementation, `calculate_project_score` for this project must produce:

**Blockers — exactly these four, and no others:**

1. `No field of science or technology.`
2. `No signed competent professional opinion.`
3. `No evidence linked to the project.`
4. `No linked costs for a claimed project.`

**Blockers that must NOT be present:**

- `Advance appears only commercial, aesthetic, project management, procurement, or internal learning.`
- `Uncertainty appears only commercial, budgetary, resourcing, customer adoption, or implementation planning.`
- `Missing accounting period.` (the period is linked; it is not a blocker today either)

**Warnings must include a review flag naming each of these matched terms:**

- advance: `internal learning`
- uncertainty: `commercial`, `implementation planning`, `resourcing`, `customer adoption`

`budgetary` must **not** match. All five above are true positives and must survive the fix; a change
that silences them has over-corrected.

**Warnings that must remain unchanged:**
`Explain more clearly why the advance is in the wider field, not only internal knowledge.`;
`Competent professional uncertainty explanation is thin.`;
`Experiments, prototypes, tests, or iterations are not yet described.`

**Rating and score:** `rating == "red"` (blockers are non-empty, `services.py:401`).
`score` must be **strictly greater** than the pre-fix score (advance and uncertainty points are now
awarded) and **strictly less than 40** (`eligibility_weights.yml:39`, red band max). The arithmetic
ceiling is 10 boundary + 10 advance + 10 uncertainty + 5 entitlement = 35.

Entitlement-derived score and warnings are out of scope for this ADR and must not be altered.

### D8. Test rulings — read this before editing any test

**`tests/test_rules_engine.py:25 test_score_calculation_for_seed_projects` must still pass,
unmodified.** The Delivery Lead's expectation that it breaks is not supported by the seed data:
the red project retains four blockers so `rating == "red"` holds, and the green and amber seed
projects (`app/seed.py:349`, `:376`) contain no negative terms in their advance or uncertainty
text, so they are untouched.

**Treat this test as a negative control.** If it fails, the implementer has changed more than this
ADR authorises. Do not adjust the test to make it pass — return to G1.

**`tests/test_rules_engine.py:41 test_automatic_blockers_for_red_project` also still passes** (it
uses `in` assertions on four blockers that all survive), but it is now insufficient because it
never asserts absence. Strengthen it additively — remove no existing assertion — by adding:

```python
assert len(score.blockers) == 4
assert not any("Advance appears only" in b for b in score.blockers)
assert not any("Uncertainty appears only" in b for b in score.blockers)
assert any("internal learning" in w.lower() for w in score.warnings)
assert any("customer adoption" in w.lower() for w in score.warnings)
assert score.rating == "red"
assert score.score < 40
```

New file `tests/test_text_matching.py`, with the proven strings as named regression cases:

Negative controls (must NOT match):
- `"There was no commercially available product that met the latency requirement."` vs
  `negative_advance_terms` → no match on `commercial`
- `"The team needed a procurement-available option beyond existing catalogue items."` vs
  `negative_advance_terms` → no match on `procurement`
- `"Station platform resurfacing and drainage works"` → `requirement_themes_for_text` returns
  `["software development"]` at `confidence="low"` and creates zero `RDECOpportunitySignal` rows

Positive controls (must still match):
- `"Commercial implementation planning, resourcing, and customer adoption risks."` →
  `{commercial, implementation planning, resourcing, customer adoption}`
- `"Internal learning and standard cloud migration delivery for a public sector reporting dashboard."`
  → `{internal learning}`
- the fourth realistic R&D description from the adversarial review that was correctly *not* blocked,
  retained verbatim as a control

## Architecture Baseline

Unchanged from ADR-0002. Python 3.12, FastAPI, SQLModel/SQLAlchemy, SQLite, Jinja2 + HTMX, pytest,
Docker Desktop. No schema migration, frontend framework, background worker, cloud service, external
LLM, portal login, or HMRC integration.

Additions permitted by this ADR: one new stdlib-only module `app/text_matching.py`; two new optional
YAML keys. No new dependency, no schema change, no new route.

## Guardrails

- Preserve `Requires competent professional and tax review.`
- The Hub must not decide eligibility. No automatic RED may originate from a judgement about wording.
- Vocabulary is fixed by ADR-0001 `:72`: `R&D candidate`, `review required`, `strong indicators`,
  `blocked`, `pending competent professional and tax review`. Do not invent new terminology.
- No LLM, no external call, no network access in the matcher. Pure functions over strings.
- Rule files remain operator-editable and a malformed optional key must never prevent startup.
- Matching is case-insensitive and must never mutate stored project text.

## Consequences

Positive:

- Realistic R&D narratives stop being hard-blocked by an accident of substring containment.
- The Hub stops asserting a conclusion it is not permitted to assert.
- Reviewers see which term fired and where, so they can overrule it with evidence.
- One matcher means `services.py` and `framework_intelligence.py` cannot drift apart.

Negative and risks:

- Genuinely non-technical projects now reach amber rather than red. Mitigated: they can never reach
  green (D2), and the review flag names the matched term.
- Stop-phrase lists are a maintenance surface and will be incomplete. Mitigated: they are optional,
  operator-editable, and fail open to current-after-Stage-1 behaviour.
- `WEAK_PATTERNS` corroboration will suppress some true single-signal opportunities. Accepted: those
  signals are advisory review prompts (`framework_intelligence.py:864`), and a false signal in a
  review queue costs more trust than a missed one costs opportunity.
- Rule-file version strings change, so any stored comparison against `"2026-05-07"` must be checked.

Migration and rollback: no data migration. Rollback is a code revert plus reverting the two YAML
files; no stored record changes shape.

## Verification

A Principal proves conformance by producing all of the following.

1. `docker compose run --rm app pytest -q` — full suite green, count reported. The 63-test baseline
   plus the new cases; no test deleted, no assertion weakened.
2. `tests/test_rules_engine.py:25` passes **unmodified** (negative control for over-reach).
3. `tests/test_rules_engine.py:41` passes with the D8 additions.
4. `tests/test_text_matching.py` covers all three negative controls and all three positive controls
   from D8, each named after the finding it closes.
5. Anti-drift test asserting `services` and `framework_intelligence` resolve to the same
   `find_matches` object, plus a span-parity test.
6. `grep -rn "contains_any" app/` returns nothing.
7. A test asserting the app starts with `review_flag_stop_phrases` and `review_flags` **absent** from
   the YAML files (fail-open proof), and that `validate_all_rules()` does not raise.
8. `docker compose run --rm app python -m compileall app` passes.
9. UAT path (user-facing): a live end-user session on the R&D review page for the seeded red project
   and for one realistic non-blocked narrative, confirming the flag reads as a review prompt and not
   as a verdict, and that the caveat is present. Synthetic browser capture alone is insufficient;
   G4 requires a real end-user pass before G6.

## ARB checklist

- Traces to epic: yes — EPIC-RDEC-2026-07-VERIFIED-FIXES, findings on `services.py:118/306/317` and
  `framework_intelligence.py:64`.
- Baseline updated: yes — this ADR; ADR-0002 baseline unchanged and not amended by this ADR.
- NFRs preserved: yes — no dependency, no schema change, matcher is O(terms x text) with cached
  compiled patterns.
- Consumers identified: `app/services.py`, `app/framework_intelligence.py`,
  `app/rules/eligibility_weights.yml`, `app/rules/blockers.yml`, `app/rules_engine.py`,
  `tests/test_rules_engine.py`, project assessment templates, Markdown reports.
- Cross-cutting: no. Contained within the rules/scoring domain. No CTO escalation required.

## Amendments and G1 Rulings

Amended 2026-07-26 by the Enterprise Architect under G1 authority, on an escalation raised from G2 by
the Principal implementing E6-4. One Decision clause is amended. The original text is retained so the
change is auditable. Where this amendment and the original Decision section conflict, the amendment
governs from 2026-07-26.

### Amendment A1 — D5.1 (stop phrases apply to corroboration, not to theme presence)

**The escalation is upheld. This ADR was internally inconsistent and D5.1 was the clause that was
wrong.**

D5.1 as written directs theme matching through matcher Stages 0-2, and Stage 2 discards a term hit
whose span sits inside a stop-phrase span. The required stop phrase `station platform` (D5.2)
contains the only `software development` pattern that `"Station platform resurfacing and drainage
works"` matches. Under the literal mechanism that string therefore yields **no theme at all**. D5.3
and D8 both state the required outcome for that same string as exactly one `software development`
requirement at `confidence="low"` with zero `RDECOpportunitySignal` rows. Both cannot hold. The
conflict is in this ADR, not in the implementation.

Original text:

> **D5.1** Theme matching uses the shared matcher (Stages 0-2), so compounds behave consistently.

Amended text, effective 2026-07-26:

> **D5.1** Theme matching uses the shared matcher, and it uses it at two different strengths, which
> must not be conflated:
>
> - **Theme presence** — whether the theme is surfaced for a human at all — is decided by Stages 0-1
>   only (normalise, then whole-token match with hyphen-compound awareness). Stop phrases are **not**
>   applied here. A theme that a human would see mentioned in the text is still reported.
> - **Corroboration** — whether the evidence is strong enough to raise an `RDECOpportunitySignal` and
>   to record the requirement at `confidence="medium"` — is decided by Stages 0-2, so a hit lying
>   inside a theme stop phrase (D5.2) does not corroborate, and by D5.3, so a lone member of
>   `WEAK_PATTERNS` does not corroborate either.
>
> A theme with no corroborating evidence yields one `ExtractedRequirement` at `confidence="low"` and
> zero `RDECOpportunitySignal` rows. Suppression removes the *conclusion*, never the *observation*.

This is the reading already delivered and proven:
`"Station platform resurfacing and drainage works"` -> `themes=['software development']`,
`matched=('platform',)`, `corroborating=()`, `confidence=low`, `requirements=1`, signal rows `0`;
control `"Bespoke software development platform"` -> `confidence=medium`, one signal. D5.2 and D5.3
are **confirmed unchanged**, and D5.2's stop-phrase list stays load-bearing — it is what stops
`platform` corroborating.

The amended reading is also the one this ADR's own guardrails require. Suppressing the theme
entirely would delete a mention from a review queue that ADR-0001 line 67 requires to show "signal
rationale and matched terms/themes". Recording the mention while withholding the signal is the
decision-support posture; silently seeing nothing is not.

### Ruling R1 — the Principal's handling is confirmed as the standard

The implementer chose the clause stated as the required **testable outcome** (D5.3/D8), kept the
other clause load-bearing by re-scoping it rather than deleting it, wrote the reconciliation into the
`requirement_theme_matches` docstring (`app/framework_intelligence.py:945-952`) naming both clauses,
and escalated to G1 instead of closing the question silently. **That is correct and it becomes the
standard.** The general form of the rule is recorded as ADR-0002 Ruling R7.

The docstring's "Raised for EA at G1" sentence is now stale. Replace it with a reference to this
amendment — for example, `ADR-0003 Amendment A1 (2026-07-26) confirms this reading and amends D5.1.`
The rest of the docstring stays: it is the explanation of a non-obvious two-strength rule, not a
temporary note.

No code change is required by this amendment. `tests/test_text_matching.py`'s D8 cases remain the
conformance proof and must not be weakened.

