# Codebase Assessment — R&D Claim Evidence Hub

**Date:** 2026-08-13
**Commit assessed:** `0dbcb12` on `codex/company-setup-readiness`
**Mode:** Read-only. No source file was created, edited or deleted. The application was never started. `data/rdec_hub.db` was verified byte-identical before and after.
**Method:** Eleven specialist roles with disjoint file ownership, plus independent re-verification of every headline claim by the orchestrator.

---

## How to read this

Findings are ranked by **what a reader hits first**, not by module. The scale runs from "this produces a wrong number in a document someone relies on" down to "this is untidy". Every claim is marked **CONFIRMED** (read from source, computed, or executed) or **INFERRED** (reasoned from indirect evidence). Where a role's claim did not survive re-verification, the correction is stated.

Scope of the codebase: 11,058 lines across 21 application modules, 93 routes, 43 templates, 11 YAML rule files, 6 Approved ADRs, 13,388 lines of tests. 97 commits ahead of `origin/main`.

---

# 1. What is genuinely good

This section is first because it is the accurate headline. This is a well-engineered product. The problems in Section 2 are real and some are serious, but they sit on top of work that is materially above the standard for a codebase of this size and age.

### 1.1 The test suite is excellent, and that is a measured statement

**859 of 859 tests pass.** Three independent full runs — alphabetical (200.9s), reverse order (157.9s), and under a line tracer — plus all 34 modules run individually. Zero failures, zero errors, zero flakes, no order dependency. **89.4% measured line coverage** (6,830/7,640 statements).

What makes it good is not the numbers:

- **Zero mocking.** `unittest.mock`, `MagicMock`, `patch(`, `assert_called` return *nothing* across all 34 files. The single largest vacuity class — "the test verifies the mock" — is structurally absent.
- **The suite argues with itself about vacuity, in 12 of 34 files.** These are assertions, not comments: `assert capped, "no seeded project reaches the warning-cap branch; this assertion is vacuous"` (`test_scoring_golden_output.py:470`); `assert len(seen) > 1, f"only one band is exercised ({seen}); this assertion is nearly vacuous"` (`:506`).
- **Non-vacuity proven by revert, and recorded.** `test_export_jargon_leak.py:23` documents rebuilding with `reports.py` reverted to `01c3f70` and re-running: *8 failed, 2 passed*. That is the correct G3 question answered by execution.
- **Forbidden vocabulary is derived, not listed.** `test_export_jargon_leak.py` extracts enum values from the **AST** of `models.py`/`services.py` and table names from `SQLModel.metadata`. An enum added tomorrow is forbidden in an export tomorrow. Its header states why: *"A hardcoded list of the four strings this epic found would pass today and miss the fifth."*
- **A fixture built to defeat its own pre-satisfaction.** `test_read_route_determinism.py` notes that the shared seeded fixture would pre-satisfy the condition under test and make the module vacuous — so it builds an unrendered hub instead, and adds a positive control at `:577` proving the row counter sees a write that *should* happen.

Estimated load-bearing fraction: **~95%**. One genuinely weak test out of 859, and it is honestly named "smoke" with its own docstring conceding the limit.

### 1.2 The commit discipline is real, not cosmetic — verified by diff sampling

A random 12-commit sample was checked message-against-diff. Scope matched every time: `E6-HEADERS` touches only `main.py` + `test_security_headers.py`; `G5-L3` touches only `.gitignore`; `E2-alias` touches 8 lines. One concern per commit, consistently, across 97 commits.

Multi-commit arcs confirm genuine incrementalism: `E7-5c: pin missing audit events as strict xfail` → `E7-3: record business unit create and rename` → `E3-CLEANUP: retire the xfail markers now the gap is closed`. Red-first, implement, retire. That shape is not fabricated after the fact.

### 1.3 Comments explain *why*, with 89 inline ADR citations

10.7% of `main.py` is explanatory comment, and the quality is unusual. `docker-compose.yml:1-2` explains why the environment block is shared ("so the settings the tests run under cannot drift from the settings the app runs under"). `requirements.txt:10-14` explains why `starlette` is pinned despite being transitive, naming CVE-2026-48818 and "19 advisories hidden behind a green suite". `.gitignore:31-42` explains why deny-by-default was needed and why the second line is load-bearing.

This is the single largest maintainability asset in the repository and it substantially offsets file length.

### 1.4 The ADR corpus is genuinely governed

Six Approved ADRs written as reasoned prose with measured numbers and named line references. ADR-0006 contains a section titled *"Record corrections noted at G3"* in which the architect corrects their own earlier statements. Rulings exist where the EA rules the **clause** wrong and the **code** right.

Conformance is not asserted — it is cited at the point of implementation. `data_management.py:681-686` names ADR-0004 D1.2 and explains the reasoning. `database.py:23-26` names D1.1/D1.3 and says which step is "easy to omit and silently defeats the whole control".

**37 of 43 checked ADR Decision clauses are HONOURED.** That is a high number.

### 1.5 The decision-support boundary is enforced in the engine, not just the UI

`CAVEAT` is appended unconditionally to every `ScoreResult.recommended_next_actions` (`services.py:697`), carried into memo, pack and evidence index, and enforced by a word-independent test that also bans band vocabulary and verdict vocabulary from advisory prose. For a product whose entire liability posture rests on one sentence, this is a control rather than a disclaimer.

### 1.6 The security investment is real where it exists

The SSRF allowlist was re-derived over **25 URL edge cases — zero bypasses**, fail-closed on userinfo, explicit port, trailing dot, IDN, backslash, IPv6 and scheme. Per-hop redirect validation (`knowledge_agent.py:129-141`) validates `str(request.url)` and then sends *that same object* with `follow_redirects=False`, so there is no re-parse between check and send. This is the failure mode most implementations get wrong, and it is right here.

CSV formula neutralisation is complete and correctly anchored (`\Z` *and* `.fullmatch()`, both sides repaired, each pinned by its own test). **Zero `|safe`, zero `Markup`, zero `autoescape false`** across all 43 templates. **Secret scan clean** across four independent passes including all 543 git blobs.

### 1.7 Code health metrics are strong

Zero bare `except:` in 7,981 lines. Zero TODO/FIXME/HACK. Zero commented-out code. Zero dead definitions across 246 checked. N+1 queries deliberately engineered out with a documented batching layer. **Max cyclomatic complexity in `main.py` is 6** across 116 functions.

All four destructive-purge guarantees in `AGENTS.md` verified individually, with dependency ordering machine-checked against the real 37-edge foreign-key graph across all four scopes — no ordering violation, no orphan-maker, `AuditEvent` in no scope.

### 1.8 The architecture choice is correct

Server-rendered Jinja + htmx + one stylesheet + no build step is the right amount of technology for a single-user local tool. Only **3 of 63 forms** use htmx, and all three carry `method="post" action="..."` alongside `hx-post` — with JavaScript off, the entire product works. The frontend recomputes no decision; score, rating, blockers and warnings all arrive computed from the backend.

---

# 2. Findings, ranked by what a reader hits first

## Tier A — Produces a wrong number in a document someone relies on

### A1. A stale entitlement can keep a project green when current facts say blocked — and the export vouches for it (HIGH, CONFIRMED)

`sync_entitlement_for_project` is called on project create (`main.py:2419`), project update (`:2454`), assessment (`:2553`), and from the import path (`data_management.py:1495`). It is called from **neither `update_contract` nor `update_customer`** — both of which write entitlement-bearing facts. The import path declares `ENTITLEMENT_FACT_DATASETS = ("projects", "solutions", "customers", "contracts")` and resyncs all four. The UI resyncs one.

**Failure path:** A reviewer opens a contract and correctly ticks "technical uncertainty described". Current facts now resolve to `blocked`. But scoring reads the stored row, so the project keeps +5, raises no blocker, and stays green. `reports.py:60-63` then prefers the stored value and labels it **`(recorded assessment)`** — *higher* apparent provenance than the correct resolved value.

This finding is engineered, accidentally, to survive audit: the reader sees a plausible rationale and a provenance label asserting it was recorded.

It also **falsifies a stated premise of an Approved ADR**. ADR-0006 D4.1 guarantees recorded and resolved wordings "can never disagree for the same facts" — true only if the stored row reflects current facts.

### A2. A corporation-tax status nobody asserted drives the only status that permits green (HIGH, CONFIRMED)

An unasserted CT status is substituted from `entitlement_rules.yml` at save time (`main.py:445-451`) **and again at scoring time** (`services.py:431-432`), then stored in the same column an asserted value would occupy. Five public customer types map to `"no"` — including `transport authority`, which is the **model default** (`models.py:64`).

Status `"no"` → `supplier_likely` → +5 with **no warning**. Every other entitlement status appends a warning or blocker, and green requires zero warnings — so `supplier_likely` is the **only status permitting a green rating**.

Disclosure exists on exactly one HTML page (`customers.html:71`). It is absent from every export, from the memo, and from the claim-period pack. The audit trail records **the output of the fallback, not that a fallback occurred** — `after_json` is byte-identical to a reviewer having asserted it.

### A3. Editing the operator-facing rules files can invert the scoring (MED-HIGH, CONFIRMED)

The YAML rule files are presented as the configurable source of truth. Partial credit is hardcoded in Python: `boundary_points = 6` against YAML `qualifying_project_boundary: 10`, plus `score += 8`, `+= 5`, `+= 2`, `+= 1`.

Lower a weight below its hardcoded partial credit and a **thinner answer scores higher than a complete one**, *and* its review flag stops firing (the warning is gated on `partial < full`). Verified by simulation.

Two further reads-then-ignores: AIF `minimum_qualifying_expenditure_percentage` is read from YAML but the readiness check hardcodes `coverage < 50`; `four_to_ten.minimum_described` is read but the slice and index are the literal `3`. Both produce a permanently not-ready claim period, one with a warning that contradicts the rule that caused it.

`validate_rule_file` checks key *presence* only. Nothing validates that weights sum to 100, that bands partition the range, or that YAML full credit exceeds hardcoded partial credit.

### A4. Money is `float` (MED, CONFIRMED)

All monetary columns are `float` (`models.py:184-190`). `round()` is half-to-even over binary representations:

| gross | apportionment | exact | stored |
|---|---|---|---|
| 2.5 | 1% | 0.025 | **0.03** (up) |
| 7.5 | 1% | 0.075 | **0.07** (down) |

Two structurally identical half-penny inputs rounding in **opposite directions**, decided by binary representation rather than a stated convention.

*Checked and cleared:* float addition-order dependence in `project_qualifying_spend` survived a ~288M-ordering search and appears genuinely unreachable. Nobody should spend time on it.

### A5. Claim-notification deadline is 1–3 days early on month-end period ends (MED, CONFIRMED)

Verified across all 36 month-end period ends 2024–2026: **wrong on 15**. Every period of account ending on the last day of a 30-day month or February is affected (2025-02-28 → returns 2025-08-28, statute says 2025-08-31). Always in the safe direction, but stated to the user as a specific date with no hedging: *"Claim notification deadline appears to have passed on 2025-08-28."*

### A6. Green is reachable at exactly one score (LOW, CONFIRMED)

Exhaustive enumeration of all 46,656 scoring-branch combinations: green is reachable only at **100**. The operator-editable `green.min: 80` threshold is decorative — the operative test is "zero warnings and zero blockers". An operator who edits it will believe they changed something.

## Tier B — Security, under the actual threat model

The threat model is stated first, because severity without one is theatre: single-user local tool, loopback-bound, no authentication *by design*. That posture is coherent and the README is honest about it.

### B1. The one control that makes no-auth acceptable lives in one line of one file (HIGH, CONFIRMED)

`docker-compose.yml:28` binds `127.0.0.1:8080:8080`. But `Dockerfile:39` is `CMD [... "--host", "0.0.0.0" ...]` with `EXPOSE 8080`. A plain `docker run -p 8080:8080` — the obvious thing to do given `EXPOSE` — publishes **93 unauthenticated routes, 15 of them destructive, to the entire LAN**.

There is no runtime assertion. The middleware stack is exactly one entry: `SecurityHeadersMiddleware`. No `TrustedHostMiddleware`, no CORS, no session, no cookie, no CSRF token, no `Origin`/`Sec-Fetch` check.

Eight deployment-drift scenarios were enumerated. Four of the highest-impact — direct `docker run`, a same-host tunnel (ngrok/VS Code auto-forwarding, against which loopback is *no defence at all*), cross-origin CSRF, and DNS rebinding — are all defeated by **two small middlewares**: a `Host` allowlist and a `Sec-Fetch-Site` check on unsafe methods.

### B2. No URL scheme validation anywhere (HIGH, CONFIRMED)

Twelve `href` sinks render stored URLs. No scheme validation exists in `main.py`, `form_utils.py` or `schemas.py`. Write paths store raw form strings (`main.py:1259`, `:1296`, `:1585`).

The aggravating factor: `opportunity.source_url` is populated from **scraped remote pages** via `urljoin`, which preserves `javascript:` and `data:text/html,` **verbatim** — joining to a trusted https base sanitises nothing.

**The chain:** hostile or compromised procurement source → stored → rendered clickable → operator clicks → script executes in the app origin → drives all 93 unauthenticated endpoints including the 15 destructive ones.

This is the finding the clean `|safe` grep hides. Every conventional XSS check on this codebase comes back clean.

### B3. The audit table is a permanent, un-purgeable PII reservoir (HIGH, CONFIRMED)

`audit.py:11-15` stores full `model_dump` snapshots into `before_json`/`after_json` — carrying UTR, PAYE reference, senior R&D contact name/email/phone, agent contact details, supplier names with costs, professional names.

`AuditEvent` appears in **no purge scope**, there is **no `session.delete` of `AuditEvent` anywhere**, and the widest purge scope explicitly preserves change history. Deleting a customer leaves a complete pre-delete snapshot of their personal and tax data in the database forever.

**GDPR Art. 17 erasure cannot be satisfied through the application** — only by hand-editing SQLite or destroying the file.

### B4. The repo's own security narrative is wrong in both directions (MED, CONFIRMED)

`.gitignore:30-42` states that `.playwright-mcp/` "holds real customer identifiers in 23 non-image files, so that was a live disclosure path."

**Verified false.** Zero of the 13 commit refs contain `.playwright-mcp/`, `data/`, `*.db`, `*.sqlite` or `.env`. Nothing from it was ever committed. The only tracked file matching any sensitive pattern is a UX rubric with no customer data.

**But an unclaimed exposure exists.** A 14th ref — `refs/codex/turn-diffs/checkpoints/…` — is a **tree object, not a commit**, which is why `git log --all` and every commit-walking command are blind to it. It holds 16 PNGs rendering real customer names, plus 12 further dangling blobs. It survives `.gitignore`, survives branch deletion, and sits outside the default push refspec — so it is a local-disk exposure, not a GitHub one.

### B5. `ux-loop-artifacts/` is gitignored but not dockerignored (MED, CONFIRMED)

| Path | `.gitignore` | `.dockerignore` | On disk |
|---|---|---|---|
| `.playwright-mcp/` | ignored | **line 12** | 23 files, 297 KB |
| `ux-loop-artifacts/` | deny-by-default, hardened 2026-07-26 | **absent** | 29 files, **6,068 KB** |

`COPY . .` bakes 28 screenshots taken against the live database into both images. `.dockerignore` was last touched 2026-06-07; the hardening never came across. Harmless while the image stays local; a disclosure the moment anyone runs `docker save` or pushes.

### B6. Lower-severity, recorded

Audit trail is append-only **by convention only** — no hash chain, no tamper evidence, `actor` is the constant `"local-user"`. No response-size cap on outbound fetches. CSP carries no `default-src`/`script-src`. `/docs`, `/redoc`, `/openapi.json` enabled (a stated sponsor decision). Orphan destructive endpoint `POST /framework-intelligence/opportunities/{id}/delete` at `main.py:1517`, referenced nowhere else in the repository.

## Tier C — Governance record vs reality

### C1. Two Approved ADR Decision clauses are not implemented (HIGH/MED, CONFIRMED)

**ADR-0005 D4 — entirely absent.** `require_parent` exists nowhere in the repo. No `@app.exception_handler`, no `add_exception_handler`, no `IntegrityError` handling outside two test files. ADR-0005 D2 explicitly forbade skipping it: *"the application-layer check in D4 is not defence in depth — it is the **only** control."*

*Mitigating, found on re-verification:* `PRAGMA foreign_key_check` on the live database returns **0 violations**, and the schema does carry the FK. So today's residual is a 500-on-bad-input, not corruption. **The record being wrong is more urgent than the code being absent.**

**ADR-0004 D6 guardrail 4 — computed then discarded.** `data_management.py:1647-48` sets `entitlement_reviews` and `entitlement_reviews_failed` under a comment reading *"whose count is returned so the recalculation is never silent."* The tests assert both. `main.py:983-984` builds the user notice from `created`/`updated`/`skipped` only. It is silent.

This is a textbook **seam defect** — engine side correct and tested, route side never consumes it, tests only ever check the engine side. Found independently by two roles working disjoint file sets.

### C2. Three documentation claims are contradicted by the code (HIGH/MED, CONFIRMED)

- **`operating_procedure.md:205`** tells the operator the Hub "matches by record identifier first and a conservative record key second." ADR-0004 explicitly reversed this — identifier-first is *"the least conservative option available"* — and the code implements the ADR. **The document written for the non-engineer operator still teaches the superseded, dangerous rule.** ADR-0004:21 records that this behaviour destroyed a live contract.
- **`README.md:196`** says the restore-by-identifier mode "does not yet render a control". `data_management.html:148` renders it.
- **`README.md:198`** says the startup log is "the only place" the orphan report appears. There is a banner on every page and a dedicated `/data-integrity` route.

Both README claims were introduced at commit 61 of 97 and falsified by later commits **in the same epic**.

### C3. `StrictUndefined` is claimed but not configured (MED, CONFIRMED)

`_score_panel.html:12` states "the templates render under StrictUndefined, where reading an unset name raises". That sentence is the **only** occurrence of `StrictUndefined` in the repository. `main.py:170` is a bare `Jinja2Templates(directory=...)`.

A typo in `score.rating_label` or `score.blockers` renders blank and tests falsy — a silently omitted blocker list on a decision-bearing surface, and the page still renders so no test catches it.

## Tier D — Delivery and platform

### D1. Nothing has ever been released (HIGH, CONFIRMED)

HEAD is **97 commits ahead of `origin/main`, 0 behind**. Local `main` is 14 behind. `CHANGELOG.md` stops at 2026-06-22. `VERSION` reads `intelligence-effectiveness 0.1`. No tag covers the work. Two full epics — including SSRF hardening, security headers, required-field validation and an import-identity redesign — are invisible to anyone reading `main`.

The 97 commits ran in a single ~9h15m window on 2026-07-25/26 and exist **on one disk**.

### D2. No CI, anywhere (HIGH, CONFIRMED)

All seven conventional config paths absent: `.github`, `.gitlab-ci.yml`, `azure-pipelines`, `Jenkinsfile`, `.circleci`, `.drone.yml`, `bitbucket-pipelines.yml`. Also no `Makefile`, `tox.ini`, `pyproject.toml`, `pre-commit`.

Every guarantee in six ADRs and 859 tests is enforced by whoever remembers to run them.

### D3. No backup, behind a purge that already destroyed live data once (HIGH, CONFIRMED)

`data/rdec_hub.db` is a single 508 KB file, `journal_mode=delete` (not WAL). No backup job, no `VACUUM INTO`, no copy-on-start. Meanwhile `settings.py:43-46` records in a comment that *"that is exactly how finding C2 destroyed a live contract"*, and `README.md:181` says an enabled purge "requires backup acknowledgement" — **the application asks the operator to confirm a backup the platform gives them no way to take.**

### D4. Migration is `create_all` plus a hardcoded 4-table ALTER dict (HIGH, CONFIRMED)

`create_all` only creates *missing tables* — it never alters an existing one. The entire migration capability is a literal dict covering additive columns on four tables. `PRAGMA user_version` on the live database is **0** — there is no schema version anywhere, so nothing can detect drift and nothing can refuse to start on it.

Any model change outside that dict silently does nothing, then fails at first query with `no such column`. There is no down-path. Combined with D3, a botched schema change is **unrecoverable**.

### D5. Supply chain and operability (MED, CONFIRMED)

20 of 28 runtime distributions float unpinned, including `certifi`, `h11` and `httpcore` — all on the TLS/SSRF path. No lockfile. Base image is a floating tag. Container runs as **root**. No `HEALTHCHECK`, no `restart:` policy. Health endpoints exist but **neither touches the database**, so the Hub returns `200 {"status":"ok"}` with the database missing or corrupt — and nothing consumes them anyway.

Logging is effectively unconfigured: no `basicConfig` anywhere, so `app.*` records fall to Python's `lastResort` handler at WARNING with no timestamp and no level. **Every INFO record is dropped** — including the ADR-0005 guardrail announcing that link-checking was withheld, and the setting that promises it "is logged loudly at startup".

## Tier E — What the user sees

### E1. The screens that produce the deliverable are the stale ones (HIGH, CONFIRMED)

This is the sharpest structural finding in the product layer. Template last-revision dates split the codebase cleanly in two:

| Template | Last revised |
|---|---|
| `claim_period_pack.html` | **2026-05-07** |
| `evidence_index.html` | **2026-05-07** |
| `framework_report_detail.html` | **2026-05-08** |
| every workflow screen | 2026-07-25 / 07-26 |

The three stale files are precisely the artefacts that **leave the building** — the Finance and Ayming handover deliverables the product exists to produce.

### E2. Reports render raw Markdown source (HIGH, CONFIRMED)

All four report pages are `<pre class="report-pre">{{ markdown }}</pre>`, and `requirements.txt` contains no Markdown renderer among its eight packages. A Finance reviewer opening the Claim Period Pack reads literal `**Decision-support caveat:**` with asterisks, `#` heading marks and pipe-table syntax in a monospace block.

### E3. The claim pack identifies projects by database ID (HIGH, CONFIRMED)

`claim_period_pack.html:19` renders **`Selected IDs: 3, 7`**. There is no project name anywhere on that page to resolve them. `services.py:842` compounds it with a user-facing warning: *"Selected project descriptions missing for: 3, 7."*

This is the page that determines which projects enter an HMRC Additional Information Form.

### E4. The most decision-bearing component carries no caveat (MED, CONFIRMED)

`_score_panel.html` renders a score out of 100, a colour-coded rating badge, and lists headed "Blockers" and "Warnings". It carries **no caveat of any kind**, on any of the five pages showing it, nor in the htmx-swapped fragment. One panel away, the *lesser* entitlement signal carries "Indicator for review only. It is not an entitlement decision."

Footer caveat coverage is 100% of full-page surfaces — this is about placement at the point of decision, not absence.

### E5. Interaction and accessibility (MED, CONFIRMED markup / INFERRED rendering)

- **Evidence Gaps panel goes stale after an htmx save.** Commit `ff0c7e5` fixed the score panel and left its neighbour. Adding the first evidence item updates the score while the panel one column away still asserts *"No evidence is linked. This is an automatic blocker."*
- **The destructive restore-by-identifier radio shares a radio group with the safe options.** The visual separation is deliberate and documented — but arrow keys move *and select* within a group, so a keyboard user arrows straight into the mode the code describes as able to let "an uploaded row replace a live record it never names". Two tests assert the shared group and the visual separation; neither tests the keyboard consequence of holding both.
- **Input borders fail WCAG 1.4.11** at 1.76:1 (`#c8c0d3` on `#ffffff`) — every text input, select and textarea. Computed arithmetic, no rendering needed.
- Successful htmx saves produce no feedback near the control, no form reset, no double-submit guard — on a claim product, that risks a duplicate cost line.
- No `aria-current` anywhere; 94 of 99 `<th>` elements lack `scope`.

### E6. Test coverage gaps concentrate in mutation routes (MED, CONFIRMED)

Three core mutation routes have **zero body coverage** — no test issues the request at all: `update_company`, `update_accounting_period` (period dates drive the claim window and AIF readiness), `update_evidence_item`. Twelve further mutating POSTs have no literal path in any test, **three of them deletes**.

Note: the brief's hypothesis that `data_management.py` would be under-tested was **wrong** — at 93.4% it is one of the better-covered modules, and its destructive functions are nearly fully exercised. `main.py` at 78.9% is the real gap.

---

# 3. The outside-in read

## 3.1 The auditor / HMRC-adjacent reviewer

*They do not read code. They read outputs.*

**Impressed within ten minutes:** the caveat discipline, and the fact that the tool declines to decide eligibility and says so. Most claim preparation is undocumented spreadsheets; this has a posture, and the posture is correct. They will value the audit trail with before/after snapshots and — if anyone explains it — the determinism guarantee. *The same period rendered twice produces the same document* is exactly the property an auditor wants from evidence and almost never gets.

**Alarmed, in this order:**
1. **"Selected IDs: 3, 7"** — a document determining AIF content that cannot be tied to any external record. Not cosmetic: an **unverifiable control**.
2. **Raw Markdown in `<pre>`.** They will conclude the process is uncontrolled before reading a single number.
3. **The corporation-tax default.** When they ask where a value came from — and they always ask — the answer is "a YAML file, stored in the column an asserted value would occupy, disclosed on one HTML page and in no export, driving the only status permitting green." That is a materially undisclosed assumption in a document supporting a tax claim. **This is what escalates a review into an enquiry.**
4. **`(recorded assessment)` with no "as at" date.** They cannot age it.

**What they miss — the important half:** every code-quality signal, invisible and worthless to them. Every security finding. And **A1 entirely**, because its nature is invisibility — they see a green rating with a plausible rationale and a label asserting it was recorded. A1 is the one finding accidentally engineered to survive audit.

**Verdict:** *"A useful evidence-capture tool. Not yet a controlled source of claim positions."* They would accept the evidence index and narrative capture; refuse to rely on ratings or computed statutory dates without independent recomputation; and once the CT default surfaces, require re-review of every claim the Hub touched.

## 3.2 The new maintainer, day one, cold

**Impressed — more than they expect:** six ADRs as reasoned prose with measured numbers; an architect correcting their own earlier statements on the record; 859/859 green on first run with no flakes and zero mocking; zero TODOs, zero dead code; commit messages matching their diffs; `# ADR-0006 D1/D2` at the call site, the highest-value comment style there is. They will realise within twenty minutes that somebody is actually governing this, which is rarer than good code.

**Alarmed by:** no CI, so nothing enforces any of it and today that is them, without a net. No lockfile with 20 floating distributions — their `pip install` may not reproduce the tree the tests passed on, and their first red test might not be their fault. 97 unreleased commits and a `VERSION` reading `0.1` — they cannot answer "what is in production?" The answer is *nothing, ever, anywhere*. And a first schema change that silently does nothing, then fails at first query, against a database with no copy.

`main.py` alarms them for about twenty minutes and then stops, because the routes are 20 lines each. **That is direct evidence for the ARB ruling below.** What genuinely costs them is the 11 near-identical create/update pairs — they will fix a bug in one and not know there are ten more.

**What they miss:** A1 (needs enough RDEC domain knowledge to know a contract field is entitlement-bearing); A2 (looks like a sensible config default); A3, because they will assume the YAML is authoritative — *and that assumption is precisely the trap*; and C3, because a comment tells them `StrictUndefined` is on and they will believe it.

**Verdict:** *"The best-governed small codebase I've joined, and the most frightening place to make my first change."*

## 3.3 The technical diligence engineer

**What they conclude is the asset:** the 11 YAML rule files, the entitlement resolver, and the contracted-out / irrelievable-client treatment. That required someone who understands both RDEC and this client's contracting shape. **Everything else is commodity** — FastAPI, Jinja, htmx, SQLite are a weekend. They will also mark the ADR corpus as a positive signal on team quality, which diligence normally cannot assess at all.

**Alarmed, in deal-affecting order:**
1. **Bus factor 1, and the asset is on one disk.** 97 unpushed commits. Changes the deal structure, not the price.
2. **Zero releases, ever.** No deployment history, no rollback record, no incident record — they cannot assess operational maturity because there are no operations.
3. **Single-user and single-tenant *by construction*, not by omission.** No auth on 93 routes, no tenancy column anywhere, `actor` hardcoded. They will price this as *"prototype validating a rules engine"*, not *"SaaS six months from market."*
4. **The audit PII reservoir.** A GDPR Art. 17 gap that cannot be satisfied through the product goes on the disclosure schedule. **This one costs money at closing.**
5. **16 real-customer screenshots in a non-branch ref**, invisible to commit-walking commands. Data-room contamination; they will want the repo scrubbed and re-verified before transfer.
6. The three terminal templates 11 weeks staler than every workflow screen — read as *"built to demo, not to deliver."*

**What they miss:** almost every correctness finding. Diligence at one hour does not read scoring logic. They take **89.4% coverage as a proxy for correctness**, which here is exactly the wrong inference — the tests are excellent at proving the code does what the code does, and structurally cannot see that a *rule* is wrong. They would most regret missing A3, because it means the YAML is not really the source of truth, so the "configurable rules engine" story — which is the valuation story — is partly false.

**Verdict:** *"Real IP in the rules layer; a well-built prototype around it; a named data-protection liability; bus factor one."*

---

# 4. Where to invest

Assumes one engineer plus AI assistance. Costs are estimates, ±50%.

## Tier 0 — This week. Hours each. No ADR needed.

| # | Action | Cost | Payback |
|---|---|---|---|
| **T0.1** | **Push the 97 commits. Automate a `VACUUM INTO` snapshot.** | 2–4h | Converts *total unrecoverable loss* into *inconvenience*. Every other item assumes the asset still exists. This is custody, not engineering. |
| **T0.2** | **One CI workflow: `pytest` on push.** | 2–4h | The highest-leverage hours in this plan. 859 tests, no flakes, no order dependency — green on day one, cheapest possible adoption. Converts six ADRs of conformance from *remembered* to *enforced*. |
| **T0.3** | **Project names, not database IDs, in the claim pack.** | ~1h | Fixes the worst line in the product. Highest auditor-confidence-per-hour available anywhere. |
| **T0.4** | **URL scheme allowlist at the render sink** (`http`/`https`/`mailto`), as a template filter across the 12 sinks. | 4–6h | Closes the only chain reaching full application control. Must be **at the sink**, not at input — hostile data may already be stored. |
| **T0.5** | **Startup assertion on bind address**; `Dockerfile` default to `127.0.0.1`. | 2–4h | Turns the loopback precondition from convention into an enforced invariant. |
| **T0.6** | `ux-loop-artifacts/` into `.dockerignore`; decide custody of the `refs/codex` tree. | 30m | Removes 6 MB of live-customer screenshots from both images and a transfer-time liability. |
| **T0.7** | Remove the orphan `opportunities/{id}/delete` endpoint. | 15m | An unreferenced, unauthenticated, destructive endpoint is pure liability. |
| **T0.8** | Honour or delete `green.min`. Fix the `#c8c0d3` border token. Move the destructive radio out of the safe group. | 0.5d | The radio grouping is a **safety** issue, not an accessibility one. |

**Then cut a tag.** One release beats a versioning policy.

## Tier 1 — Weeks 2–4. Each needs an ADR at G1.

| # | Action | Cost | Payback |
|---|---|---|---|
| **T1.1** | **Provenance-at-render.** Never emit `(recorded assessment)` without resolving current facts and comparing; on divergence, say so in the document. | 3–5d incl. ADR | **The core fix.** See §5. |
| **T1.2** | **Disclose the CT fallback** in every export, the pack, and the audit event — record *that a fallback occurred*. | ~1d | Removes the undisclosed assumption that escalates an audit into an enquiry. |
| **T1.3** | **Rules-file load-time guard.** Refuse to boot when weights don't sum to 100 or any hardcoded partial credit ≥ its YAML full credit. | 1–2d | Converts a *silently wrong score* into a *startup refusal*. |
| **T1.4** | **Test the three uncovered mutation routes and the 12 unreferenced POSTs.** | 1–2d | These are the routes that write the claim record. Target list already known. |
| **T1.5** | **Governance correction on ADR-0005 D4** — implement `require_parent`, or return to G1 and amend D2's rationale. | 0.5d amendment / 1–2d code | **The record being wrong is more urgent than the code being absent.** An ADR corpus this good loses its value the moment it stops describing reality. |
| **T1.6** | Hedge or fix the claim-notification deadline. | 0.5d | Errs safe today, so low urgency — but very cheap and currently stated without hedging. |

## Tier 2 — Next quarter.

| # | Action | Cost | Payback |
|---|---|---|---|
| **T2.1** | **Adopt Alembic.** | 2–3d | Unblocks T2.2 and every future schema change. With T0.1, converts *unrecoverable* into *routine*. |
| **T2.2** | **`Decimal` money.** Blocked on T2.1. | ~1w | Removes the opposite-direction rounding. |
| **T2.3** | Lockfile (`uv`/`pip-tools`); pin the 20 floats. | 2h — do with T0.2 | Reproducibility; closes drift on the TLS path. |
| **T2.4** | Render Markdown to HTML; refresh the three terminal templates **for content**; then a real G4 UAT on the pack. | 2–3d | Do **after** T1.1, never before — otherwise you redesign around wrong numbers. |
| **T2.5** | Caveat on `_score_panel.html`. | 30m | Rank higher if the sponsor is showing the tool to anyone. |
| **T2.6** | Framework Intelligence router lift + bounded-context ADR. | — | **Only when an epic touches FI.** See §5. |

## Enhancements worth having

`assessment as-at` timestamp beside every derived value (the first thing an auditor asks for) · provenance columns in exports (`asserted` / `assumed` / `derived`) · surface `entitlement_reviews_failed` at `main.py:983-984`, closing ADR-0004 D6 g4 · configure `StrictUndefined` for real, or delete the comment claiming it.

## What to deliberately NOT do

This section matters as much as the two above.

**N1. Do not refactor `main.py` generally.** Regression risk across 93 routes with no CI exceeds the benefit at the current change rate.

**N2. Do not de-duplicate the 11 create/update pairs into a generic CRUD abstraction.** This is the most tempting item on the list — 149 identical lines, 17% duplication, textbook DRY — and the most dangerous. A generic handler across 11 entity types with different validation, different audit summaries and different entitlement side effects is **precisely where A1's class of bug goes to hide**. The duplication is currently *legible*. That three of these routes are untested is an argument for **tests**, not abstraction.

**N3. Do not add authentication, multi-user or RBAC.** Not now. The single-user loopback threat model is coherent. Half-authentication is *worse* than no authentication behind an enforced loopback bind, and this is 4–6 weeks buying the current sponsor nothing.

**N4. Do not build a hash-chained tamper-evident audit log.** It sounds like exactly what a claim-evidence system needs. It is ceremony: the threat model has one trusted user with direct filesystem access, and a hash chain that same user can recompute is not tamper evidence. Real tamper evidence needs an external anchor — a hosting decision. Spend the effort on the correctness of the content, not the integrity of a record of incorrect content.

**N5. Do not migrate off SQLite.** 508 KB, one user. Postgres buys nothing and costs an operational story that does not exist.

**N6. Do not run a WCAG remediation programme.** Fix the border token and the radio grouping; skip the full audit. One internal user, no procurement requirement, no public deployment.

**N7. Do not "fix" the float problem with more rounding.** Either do the real `Decimal` migration after T2.1 or leave it and document the convention. Sprinkling `round()` at output sites hides the divergence and makes the eventual migration impossible to verify.

**N8. Do not redesign the three stale terminal templates.** Very tempting. But the defects are *content* — names not IDs, rendered Markdown, a missing caveat — not layout. A redesign is a G4 UAT event and it must follow T1.1, or you will polish a document that prints stale numbers.

**N9. Do not build a versioning or CHANGELOG policy.** Version numbers on a never-released product are theatre. The fix is not process — it is to *release*.

---

# 5. Two rulings worth recording

## 5.1 ARB ruling — is `main.py` a problem?

Two roles reached opposite conclusions on evidence. The CTO chaired this on primary sources.

- **The EA's causal claim is REJECTED.** The EA cited ADR-0006 R1 as "a concrete governance failure caused by size". Read in full, R1's failure was **a textual proxy standing in for a behavioural invariant** — a grep over a 200-line module also cannot distinguish a write phase from a render phase, because that distinction is a runtime property. The remedy actually applied was not decomposition but Amendment A2, replacing the grep with behavioural properties. The EA's strongest evidence does not support its conclusion.
- **The PE's refusal of general decomposition is RATIFIED** — 116 functions at max complexity 6, routes averaging 20 lines, business logic genuinely elsewhere.
- **The EA's *remedy* is APPROVED on different, stronger grounds.** The 31 Framework Intelligence routes occupy a **contiguous** block (`main.py:1062–1760`), make **zero calls** to any RDEC-domain function, and share only `save_with_audit` and `log_event` — infrastructure, not domain. And Framework Intelligence **is governed by no ADR at all** despite performing live outbound HTTP. That is the real governance gap, mislabelled as a file-size problem.
- **Priority: low.** The lift ranks below every correctness finding and gets no epic of its own.

*What would change this:* two increments measurably blocked on the `main.py` serial baton in one quarter; FI growing a scheduler or background execution; or CI landing, which weakens the risk side of the argument.

## 5.2 The one thing

**Precondition, not strategy:** push the 97 commits and snapshot the database. Two to four hours, this afternoon.

**The one engineering investment: make every derived value that reaches a claim-facing document state its currency.**

Concretely, `reports.py` must not emit `(recorded assessment)` for a stored `EntitlementAssessment` without resolving current facts and comparing. Agree → `(recorded assessment)`. Diverge → say so, loudly, in the document.

Why this over the other twenty-odd findings:

1. It is an **ADR conformance failure**, not merely a bug — A1 falsifies a stated premise of ADR-0006 D4.1.
2. It is **a wrong number wearing a badge**. The stale value is labelled with *higher* provenance than the correct one would carry.
3. **The obvious fix is the wrong one.** Adding `sync_entitlement_for_project` to the two missing routes is forbidden by ADR-0006 D3 without returning to G1 — and D4.4 states the principle: *"Consistency comes from resolving, not from writing."* Chasing cache coherence across every write path is an unbounded obligation. Resolving at the point of consequence is bounded, cheap and fail-loud.
4. **It generalises.** A1, A2, A6 and C1 are one defect class: **the data model has no provenance dimension.** It cannot distinguish *asserted* from *assumed* from *derived* from *stale*.
5. **It is the multi-user prerequisite.** Today the stale class is "one user forgot to reopen a page". With two users it becomes "two users disagree about the score, and the document picks one without saying which."

## 5.3 What breaks first if this succeeds

Not scale, not SQLite, not performance. **Identity breaks first.** `actor` is the constant `"local-user"` on every audit event. The moment a second person uses this, the audit trail — the product's entire claim to being *evidence* — becomes worthless, and **the whole accumulated history becomes retrospectively unattributable**. It breaks silently and irreversibly.

If one architectural action is taken ahead of any multi-user decision: make `actor` a real value plumbed from a request context, even if configured rather than authenticated. Days now versus an unrecoverable history later.

Correct sequence: **identity → provenance → concurrency → tenancy.** Tenancy is the expensive one and the one people reach for first, because it is the one that sounds like architecture.

---

# 6. What was NOT checked, and why

Stated explicitly so gaps are not mistaken for coverage.

## Not checked because the assessment was read-only

- **The application was never started.** No HTTP response was observed by any role. Every statement about *rendered* or *runtime* behaviour is INFERRED.
- **No acceptance verdict exists.** The UAT gate could not be met — its standard is driving the real running build end-to-end, and that was forbidden. The evidence value is **`none`**, not "synthetic". This is not new: the repo's own `ux-loop-artifacts/2026-07-20/scorecard.md:78` records that real human end-user acceptance remains outstanding.
- **G5b runtime security is NOT ASSESSED** — which is different from "passed" and materially different from "failed". No target was probed, no packet left the machine. Specifically unverified: actual response headers as served (there is a **hypothesis**, from reading Starlette 1.3.1 source, that an unhandled-exception 500 carries none of the four headers, because `ServerErrorMiddleware` sits outside the header-injecting middleware and the test suite cannot see this path); real error-page contents; CORS, cookie and transport behaviour; real redirect and SSRF behaviour on the wire; DNS rebinding inbound or outbound; concurrency, locking and timeout behaviour; and whether the loopback binding holds as actually deployed.
- **A 4-hour DAST subset would settle most of it** — binding A/B with a positive control, a DB-delta sweep of all 37 GETs, header reality-check on real uvicorn, and a cross-origin CSRF probe. A full plan of 13 tests (~17h) exists in the working notes.

## Not checked because it needs network access

- **No live dependency audit.** `pip-audit` is installed but was not run. Priority order when it is: `starlette 1.3.1` (carried 9 of 19 advisories on the previous pin set, and parses every request), `python-multipart 0.0.32` (on the untrusted upload path), `Jinja2 3.1.6`, then `uvicorn`/`httpx`/`fastapi` — all past the assessing model's knowledge cutoff, so no advisory claim is made either way.
- **GitHub repository visibility was not determined.** This is the single input that most changes the rating of the data-exposure findings, and it needs a human to confirm.

## Not checked because it was out of scope or too expensive

- **No image was built**, so the `ux-loop-artifacts` finding derives from `.dockerignore` semantics plus a file listing, not layer inspection. One command settles it.
- **No lint or type check was run in the QA pass** — `ruff` and `mypy` are absent from `requirements-dev.txt` and installing them would have mutated the environment. A separate role ran them from a local venv: **80 mypy errors across 8 files**, mostly two systemic SQLModel/SQLAlchemy typing patterns, with genuine signal inside (a real key-type unsoundness in the import remap).
- **Branch/condition coverage is unmeasured** — the 89.4% figure is *line* coverage from a purpose-built PEP 669 tracer, not coverage.py.
- **No mutation testing.** Non-vacuity relies on the authors' recorded revert evidence, not independent reproduction.
- **`data/rdec_hub.db` contents were never opened** by any role, by instruction. Findings about it derive from schema, exposure, and a read-only copy that has since been deleted.
- **Statutory correctness was checked at one point only** — the claim-notification deadline against FA98 Sch18 83B(2). AIF selection thresholds, cost-category qualification and the entitlement decision tests were **taken at face value** from their `review_status: "verified against official GOV.UK/HMRC guidance on 2026-05-07"`. That verification is now **98 days old**, and the Knowledge Agent exists to prevent exactly that staleness.
- **Concurrency was reasoned about, not tested.** One race is analytical: `delete_accounting_period` commits a submission-status deletion before a dependency check, so a concurrent link could destroy CT600/AIF dates while the period survives. Race-only on a single-user app.
- **The four non-HEAD branches** were checked only for sensitive-path presence, not reviewed for code defects.

## Corrections made during the assessment

Recorded because they affect figures quoted elsewhere:

- **`main.py` is 2,943 lines, not 2,670.** The orchestrator's initial count used a tool that silently excludes blank lines. All initial line figures were undercounts; the EA's raw counts are correct.
- **The test-to-code ratio is 1.21:1** (13,388 : 11,058) — ordinary and healthy, not the anomaly the initial brief implied.
- **`codex/framework-intelligence-agent` was merged by ancestry, not squash** — it is 0 ahead / 1 behind `origin/main`. The delivery-lead's squash inference was wrong in mechanism, right in conclusion.
- **Templates number 43, not 44.** Routes number 93.
- **`data_management.py` is well covered (93.4%)**, contradicting the brief's hypothesis. `main.py` (78.9%) is the real gap.

---

# 7. Decisions required

| # | Decision | Why it needs you |
|---|---|---|
| 1 | **Is the GitHub repository public or private?** | The single input that most changes the data-exposure rating. Cannot be determined without network access. |
| 2 | **Merge-or-abandon `codex/company-setup-readiness`** (97 commits) and the orphan `codex/source-health-triage-pack` (1 commit). | Release decisions are yours. Nothing is merged or pushed without your say-so. |
| 3 | **Authorise a live UAT session.** | No acceptance verdict can exist without it, and the repo's own scorecard has recorded it outstanding since 2026-07-20. |
| 4 | **Authorise the 4-hour DAST subset**, or accept and document that runtime security is unassessed. | G5b cannot be honestly cleared from static evidence. |
| 5 | **Custody of the `refs/codex` screenshot tree** — delete and `gc`, or retain deliberately. | It holds real customer imagery and survives ordinary cleanup. |
| 6 | **Build-vs-buy on the product itself.** | Whether to keep building versus adopting a commercial R&D claim platform. The differentiator is genuine, but a market scan should precede any multi-customer investment. |
| 7 | **Re-run the Knowledge Agent guidance check.** | The rules carry a `verified against HMRC guidance` date of 2026-05-07 — 98 days old — and the product's value depends on it. |

---

## Appendix — assessment integrity

Read-only was honoured and verified: `git status --porcelain` empty at start and end; zero tracked files changed; `data/rdec_hub.db` SHA-256 `0B24CE4D…5EA9BD` with mtime and size identical before and after 859 tests × 3 full runs; the application was never started; no role made an outbound network request.

One incident, disclosed: a role copied the live database to a session scratchpad to inspect its schema without touching the original. A second role detected the copy across a role boundary and flagged it. The orchestrator verified the hash matched, then deleted the copy; the original was confirmed intact.

Test execution was gated on first reading `tests/conftest.py` to confirm the unconditional `DATABASE_URL` override precedes all app imports, with a throwaway database path exported as belt-and-braces, and the live database hashed before and after.

Roles: delivery-lead, enterprise-architect, principal-platform, principal-governance-engine, principal-frontend, principal-engineer, qa-engineer, security-reviewer, uat-lead, security-pentest, cto. File sets were disjoint. Every headline claim was independently re-verified by the orchestrator with git, grep or direct file reads before entering this report.
