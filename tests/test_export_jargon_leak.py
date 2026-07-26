"""E5-EXPORT-2: no Markdown export may hand a reviewer the machine's vocabulary.

Why this module exists rather than another assertion inside the report tests.

The G4 rejection was not "the memo has a bug". It was that the jargon was stripped from the
HTML templates and not from the export builders, so a browser walkthrough passed while the
artefacts that actually go to HMRC and to Ayming still read ``ambiguous_tax_review`` and
``red / 7``. The defect class is *a surface nobody walked*, so the control has to be a sweep
over every generated export, not a per-string check on the two that were reported.

The forbidden vocabulary is DERIVED, never listed:

- the stored enum values come from the AST of the modules that define them, so an enum added
  to ``app/models.py`` or ``app/services.py`` tomorrow is forbidden in an export tomorrow;
- the database column and table names come from ``SQLModel.metadata``, i.e. the schema itself;
- the Python class names come from the ``RECORD_LABELS`` map in ``app/templates/_labels.html``,
  which ``tests/test_template_accessibility.py`` already proves covers every model class;
- the rating values come from ``app.domain.RADAR_STATUSES`` and from the ``score_bands`` labels
  in ``app/rules/eligibility_weights.yml``.

A hardcoded list of the four strings this epic found would pass today and miss the fifth.

Non-vacuity was proven by rebuilding the image with ``app/reports.py`` reverted to 01c3f70
(the tree the UAT Lead rejected) and re-running this module: 8 failed, 2 passed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from app import domain
from app.framework_intelligence import generate_framework_intelligence_report_markdown
from app.models import AccountingPeriod, RDProject
from app.reports import (
    generate_claim_period_pack_markdown,
    generate_evidence_index_markdown,
    generate_project_memo_markdown,
)
from app.rules_engine import get_rules


REPO_ROOT = Path(__file__).resolve().parents[1]
LABELS_TEMPLATE = REPO_ROOT / "app" / "templates" / "_labels.html"

# The modules that define what the Hub STORES. Their snake_case string literals are the enum
# vocabulary: statuses, input types, rule keys and flags.
VOCABULARY_MODULES = (
    REPO_ROOT / "app" / "services.py",
    REPO_ROOT / "app" / "models.py",
    REPO_ROOT / "app" / "domain.py",
)

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
SNAKE_CASE_IN_TEXT = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
JINJA_RECORD_LABELS = re.compile(r"\{%\s*set\s+RECORD_LABELS\s*=\s*(\{.*?\})\s*%\}", re.DOTALL)

# The one section where a machine identifier is the point. ADR-0001 line 76 requires a rule
# version summary, and a reviewer reproducing a score needs the exact ruleset id that produced
# it -- "Eligibility weights: 2026-07-25" would not identify the file. The exemption is scoped
# to this section by heading, so the same token anywhere else in the document still fails.
PROVENANCE_SECTIONS = {"Rule versions used"}

# Rendered as a boolean repr, these are Python, not English. Kept separate from the enum sweep
# because they are keyword constants rather than stored values.
PYTHON_LITERALS = re.compile(r"\b(?:True|False)\b")

# Two scoring sentences name the band by its colour in prose. This sweep found them; E5 cannot
# fix them. Both are pinned byte-for-byte in tests/test_scoring_golden_output.py, and the epic's
# must-not-regress list keeps the golden scoring output unchanged, so changing the wording here
# would mean re-baselining a golden in order to make a new test pass. They are recorded, not
# exempted silently, and they leak on the HTML score panel too -- the frontend humanisation did
# not catch them either. test_the_known_colour_prose_is_still_golden_pinned makes the exception
# self-expiring: it fails the moment either sentence changes or stops being golden-pinned, so the
# exemption cannot outlive its justification.
GOLDEN_PINNED_COLOUR_PROSE = (
    "Warnings cap the current rating at amber until review points are resolved.",
    "Obtain signed competent professional opinion before treating the project as green.",
)
BULLET = re.compile(r"^\s*-\s*")


def _string_literals(path: Path) -> set[str]:
    return {
        node.value
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def stored_enum_tokens() -> set[str]:
    """Every snake_case value the application can store, read from its own source."""
    tokens: set[str] = set()
    for path in VOCABULARY_MODULES:
        tokens |= {value for value in _string_literals(path) if SNAKE_CASE.match(value)}
    assert "ambiguous_tax_review" in tokens, "the AST sweep has stopped seeing entitlement statuses"
    return tokens


def database_identifiers() -> set[str]:
    """Table and column names, from the schema metadata itself.

    Restricted to multi-word names. Single-word columns (``status``, ``notes``, ``strength``,
    ``outcome``) are ordinary English and appear legitimately in these documents as prose.
    """
    identifiers: set[str] = set()
    for table in SQLModel.metadata.tables.values():
        if "_" in table.name:
            identifiers.add(table.name)
        identifiers |= {column.name for column in table.columns if "_" in column.name}
    assert identifiers, "no schema metadata: the models were not imported before this ran"
    return identifiers


def python_class_names() -> set[str]:
    """Model class names that are not also their own human label.

    ``RECORD_LABELS`` maps every model class to the text a reviewer should see. Where the two
    are equal (``Company``, ``Customer``, ``Contract``, ``Solution``) the word is ordinary
    English and is expected in these documents. Where they differ (``RDProject`` -> "R&D
    project", ``CostLine`` -> "Cost line") the class name is a Python identifier and must
    never reach a reviewer.
    """
    match = JINJA_RECORD_LABELS.search(LABELS_TEMPLATE.read_text(encoding="utf-8"))
    assert match, "RECORD_LABELS is no longer declared as a literal in _labels.html"
    record_labels: dict[str, str] = ast.literal_eval(match.group(1))
    names = {name for name, label in record_labels.items() if label != name}
    assert "RDProject" in names and "Company" not in names
    return names


def rating_values() -> set[str]:
    """The colour values. ``RADAR_STATUSES`` is literally ["green", "amber", "red"]."""
    return set(domain.RADAR_STATUSES)


def score_band_values() -> set[str]:
    """The stored ``ScoreResult.rating`` values, from the rules file that defines the bands.

    This is a superset of the colours: ``weak`` is a band label too, and it is also a legitimate
    evidence strength, so it is only forbidden where it is presented AS a status.
    """
    return {str(band["label"]) for band in get_rules().score_bands().values()}


def status_position(value: str) -> re.Pattern[str]:
    """A rating value presented as a status, e.g. ``rating red`` or ``Current status: red``."""
    return re.compile(rf"\b(?:rating|status|current view)\b[:\s]+{re.escape(value)}\b", re.IGNORECASE)


def scannable_lines(export: str) -> list[tuple[int, str]]:
    """Every line of an export, minus the sections where a machine identifier is required."""
    section = ""
    lines: list[tuple[int, str]] = []
    for number, line in enumerate(export.splitlines(), start=1):
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section in PROVENANCE_SECTIONS:
            continue
        lines.append((number, line))
    return lines


def exports_under_scan(session) -> dict[str, str]:
    """Every Markdown artefact the Hub hands to a human, generated for real.

    Generated, not sampled: an assertion about a string this test built itself would prove
    nothing about what the route returns.
    """
    projects = list(session.exec(select(RDProject)))
    periods = list(session.exec(select(AccountingPeriod)))
    assert projects and periods, "the seeded dataset produced nothing to export"
    exports = {
        "evidence index": generate_evidence_index_markdown(session),
        "framework intelligence report": generate_framework_intelligence_report_markdown(session),
    }
    for project in projects:
        exports[f"project memo: {project.project_title}"] = generate_project_memo_markdown(
            session, project.id
        )
    for period in periods:
        exports[f"claim period pack: {period.label}"] = generate_claim_period_pack_markdown(
            session, period.id
        )
    return exports


@pytest.fixture()
def exports(seeded_session) -> dict[str, str]:
    return exports_under_scan(seeded_session)


def test_every_markdown_generator_in_app_reports_is_covered(seeded_session):
    """A new export builder must not be able to arrive unscanned.

    Without this, the sweep below silently stops being a sweep the first time someone adds
    ``generate_something_markdown`` -- which is the exact shape of the defect being fixed.
    """
    declared = {
        node.name
        for node in ast.walk(ast.parse((REPO_ROOT / "app" / "reports.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name.startswith("generate_") and node.name.endswith("_markdown")
    }
    scanned = {
        "generate_project_memo_markdown",
        "generate_claim_period_pack_markdown",
        "generate_evidence_index_markdown",
    }

    assert declared == scanned, (
        f"app/reports.py declares Markdown generators this module does not scan: "
        f"{sorted(declared - scanned)}. Add them to exports_under_scan()."
    )


def test_no_export_contains_a_stored_enum_token(exports):
    tokens = stored_enum_tokens()
    leaks = []
    for name, export in exports.items():
        for number, line in scannable_lines(export):
            for found in SNAKE_CASE_IN_TEXT.findall(line):
                if found in tokens:
                    leaks.append(f"{name} line {number}: {found!r} in {line.strip()!r}")

    assert not leaks, "raw stored values reached a reviewer:\n" + "\n".join(leaks)


def test_no_export_contains_a_database_identifier(exports):
    identifiers = database_identifiers()
    leaks = []
    for name, export in exports.items():
        for number, line in scannable_lines(export):
            for found in SNAKE_CASE_IN_TEXT.findall(line):
                if found in identifiers:
                    leaks.append(f"{name} line {number}: {found!r} in {line.strip()!r}")

    assert not leaks, "database column or table names reached a reviewer:\n" + "\n".join(leaks)


def test_no_export_contains_a_python_class_name(exports):
    names = python_class_names()
    leaks = []
    for name, export in exports.items():
        for number, line in scannable_lines(export):
            for class_name in names:
                if re.search(rf"\b{re.escape(class_name)}\b", line):
                    leaks.append(f"{name} line {number}: {class_name!r} in {line.strip()!r}")

    assert not leaks, "Python class names reached a reviewer:\n" + "\n".join(leaks)


def test_no_export_contains_a_bare_colour_rating(exports):
    """The colour values have no other meaning in these documents, so any occurrence is a leak."""
    colours = rating_values()
    leaks = []
    for name, export in exports.items():
        for number, line in scannable_lines(export):
            if BULLET.sub("", line).strip() in GOLDEN_PINNED_COLOUR_PROSE:
                continue
            for colour in colours:
                if re.search(rf"\b{re.escape(colour)}\b", line):
                    leaks.append(f"{name} line {number}: {colour!r} in {line.strip()!r}")

    assert not leaks, "a colour name was shown to a reviewer:\n" + "\n".join(leaks)


def test_the_known_colour_prose_is_still_golden_pinned():
    """The only two exempted sentences, and the reason they are exempt, must both still hold.

    If either sentence is reworded, or stops being pinned by the golden scoring output, the
    exemption's justification is gone and it must be deleted rather than carried forward.
    """
    services = (REPO_ROOT / "app" / "services.py").read_text(encoding="utf-8")
    golden = (REPO_ROOT / "tests" / "test_scoring_golden_output.py").read_text(encoding="utf-8")

    for sentence in GOLDEN_PINNED_COLOUR_PROSE:
        assert sentence in services, (
            f"{sentence!r} is no longer produced by app/services.py -- delete it from "
            f"GOLDEN_PINNED_COLOUR_PROSE so the colour sweep covers that line again"
        )
        assert sentence in golden, (
            f"{sentence!r} is no longer pinned by the golden scoring output, so the reason it "
            f"was exempted has gone. Humanise the sentence and delete the exemption."
        )


def test_no_export_presents_a_score_band_value_as_a_status(exports):
    """Covers ``weak``, which is a band label and also a legitimate evidence strength.

    ``weak`` as an evidence strength is correct; ``rating weak`` is the leak. Matching on the
    status position is what separates the two without exempting the word outright.
    """
    patterns = {value: status_position(value) for value in score_band_values()}
    leaks = []
    for name, export in exports.items():
        for number, line in scannable_lines(export):
            for value, pattern in patterns.items():
                if pattern.search(line):
                    leaks.append(f"{name} line {number}: {value!r} as a status in {line.strip()!r}")

    assert not leaks, "a stored rating value was presented as a status:\n" + "\n".join(leaks)


def test_no_export_contains_a_python_boolean_repr(exports):
    leaks = []
    for name, export in exports.items():
        for number, line in scannable_lines(export):
            if PYTHON_LITERALS.search(line):
                leaks.append(f"{name} line {number}: {line.strip()!r}")

    assert not leaks, "a Python boolean repr reached a reviewer:\n" + "\n".join(leaks)


def test_every_export_still_carries_the_preserve_clause(exports):
    """ADR-0002 line 58. Humanising copy must not cost the caveat on any surface."""
    missing = [
        name
        for name, export in exports.items()
        if "Requires competent professional and tax review." not in export
    ]

    assert not missing, f"exports missing the decision-support caveat: {missing}"


def test_the_scan_reaches_the_lines_the_g4_rejection_named(exports):
    """A control that scans nothing passes everything.

    The rejection quoted four lines. This asserts the sweep actually visits each of them, so a
    future change to headings or to ``scannable_lines`` cannot quietly empty the sweep while
    leaving every assertion above green.
    """
    packs = [export for name, export in exports.items() if name.startswith("claim period pack")]
    memos = [export for name, export in exports.items() if name.startswith("project memo")]
    assert packs and memos

    pack_text = "\n".join(line for pack in packs for _, line in scannable_lines(pack))
    memo_text = "\n".join(line for memo in memos for _, line in scannable_lines(memo))

    assert ", spend GBP" in pack_text, "the project list line is not being scanned"
    assert ", blockers " in pack_text, "the project readiness line is not being scanned"
    assert "(recorded assessment)" in pack_text or "no assessment recorded yet" in pack_text, (
        "the entitlement note line is not being scanned"
    )
    assert "- Status: " in memo_text, "the memo entitlement status line is not being scanned"
    assert "- Rating: " in memo_text, "the memo rating line is not being scanned"
