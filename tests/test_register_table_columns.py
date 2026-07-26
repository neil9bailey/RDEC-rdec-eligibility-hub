"""E5-COLUMNS: the project register's columns are sized to the values they carry.

Epic EPIC-RDEC-2026-07-VERIFIED-FIXES.

G4 measured the register live in Chrome on a 1707px desktop and found the Period
column rendered at 56px, so an accounting period broke across three lines
("UAT / FY2 / 6") and two header labels broke as "PERI OD" and "SC OR E". On a
claim tool the accounting period is how the whole claim is scoped, so a period a
reviewer cannot read at a glance is a real defect, not cosmetics.

Measured here in headless Chrome against the built image, seeded period label
"FY2025/26", ``Range.getClientRects()`` counting real line boxes:

    viewport     Period width       header line boxes   period line boxes
                 before -> after    before -> after     before -> after
    1707x1000     60.64 -> 95.98    1,1,2,1,3 -> all 1        3 -> 1
    1480x900      60.64 -> 95.98    1,1,2,1,3 -> all 1        3 -> 1
    1280x800      52.94 -> 95.98    1,1,2,1,3 -> all 1        3 -> 1
    1100x900      46.02 -> 95.98    1,2,3,1,4 -> all 1        5 -> 1
     900x900      67.08 -> 95.98    1,1,2,1,2 -> all 1        2 -> 1
     681x900      51.47 -> 95.98    1,1,2,1,3 -> all 1        3 -> 1
     360x640      35.19 -> 95.98    6,7,6,1,5 -> all 1        9 -> 1

``document.documentElement.scrollWidth == clientWidth`` at every one of those
widths, before and after (ADR-0002 Ruling R1: no page-level horizontal overflow).

The browser is what proves the pixels; these tests pin the three decisions that
produced them so none can be removed silently:

  (a) the register's cells carry the column classes the stylesheet sizes by;
  (b) the stylesheet holds the atomic-label columns and the header labels on one
      line, and releases the rating badge from the global ``.badge`` nowrap;
  (c) the table itself carries NO unconditional ``min-width``. That is the
      obvious fix and the sibling ``.table-wrap table`` uses it, but it was
      measured and rejected: ``.table-scroll`` is ``max-width: 100%`` while its
      ``.panel`` ancestor is only reset to ``min-width: 0`` inside the 680px
      media query, so at 681px a table minimum pushed the panel out and
      ``documentElement.scrollWidth`` (680) exceeded ``clientWidth`` (599) --
      page-level horizontal overflow. (c) is the guard on that measurement.

Stylesheet assertions strip CSS comments first: the comment explaining a rule
names the very declarations under test, so an unstripped source would let a
deleted rule keep passing on the strength of the note describing it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import get_session
from app.main import app

STYLES = Path(__file__).resolve().parent.parent / "app" / "static" / "styles.css"
PROJECTS_TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "templates" / "projects.html"

CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)

#: The register's columns, in document order, and whether the column's value is an atomic label
#: that must never be broken across lines.
COLUMNS = [
    ("col-project", False),
    ("col-solution", False),
    ("col-period", True),
    ("col-rating", False),
    ("col-score", True),
]


def stylesheet_without_comments() -> str:
    return CSS_COMMENT.sub("", STYLES.read_text(encoding="utf-8"))


def rules(css: str) -> list[tuple[str, str, str | None]]:
    """Return ``(selector, declarations, enclosing at-rule or None)`` for every rule block."""
    found: list[tuple[str, str, str | None]] = []
    at_rule: str | None = None
    depth = 0
    i = 0
    while i < len(css):
        brace = css.find("{", i)
        if brace == -1:
            break
        prelude = css[i:brace].strip()
        if prelude.startswith("@"):
            at_rule = prelude
            depth += 1
            i = brace + 1
            continue
        close = css.find("}", brace)
        if close == -1:
            break
        found.append((" ".join(prelude.split()), css[brace + 1 : close], at_rule))
        i = close + 1
        while i < len(css) and css[i : i + 1].strip() == "":
            i += 1
        if depth and css[i : i + 1] == "}":
            depth -= 1
            at_rule = None
            i += 1
    return found


def declarations_for(css: str, selector: str) -> list[tuple[str, str, str | None]]:
    """Every ``(property, value, at-rule)`` declared by a rule whose selector list names *selector*."""
    out: list[tuple[str, str, str | None]] = []
    for prelude, body, at_rule in rules(css):
        if selector not in [" ".join(part.split()) for part in prelude.split(",")]:
            continue
        for declaration in body.split(";"):
            if ":" not in declaration:
                continue
            prop, _, value = declaration.partition(":")
            out.append((prop.strip(), value.strip(), at_rule))
    return out


@pytest.fixture()
def client(seeded_session):
    def override_session():
        yield seeded_session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --- (a) the markup the stylesheet sizes by ---------------------------------------------------


def test_register_table_is_tagged_and_still_inside_the_local_scroll_container(client):
    body = client.get("/projects").text
    assert '<div class="table-scroll">' in body
    scroller = body.split('<div class="table-scroll">', 1)[1]
    table = scroller.split("</table>", 1)[0]
    # ADR-0002 Ruling R1: wide content scrolls in a local container, never the page.
    assert '<table class="register-table">' in table


@pytest.mark.parametrize("column, _atomic", COLUMNS, ids=[c for c, _ in COLUMNS])
def test_every_register_row_carries_the_column_class(client, column, _atomic):
    body = client.get("/projects").text
    table = body.split('<table class="register-table">', 1)[1].split("</table>", 1)[0]
    row_count = table.count('<td class="col-period">')
    assert row_count >= 1, "the seeded register must have at least one row to measure"
    # One header cell plus one cell per project row.
    assert table.count(f'class="{column}"') == row_count + 1, (
        f"{column} appears {table.count(f'class=\"{column}\"')} times for {row_count} rows"
    )


def test_the_row_editor_cell_is_not_tagged_as_a_column(client):
    """The ``colspan="5"`` editor row is not a column and must not be sized like one."""
    body = client.get("/projects").text
    table = body.split('<table class="register-table">', 1)[1].split("</table>", 1)[0]
    for editor_cell in re.findall(r'<td colspan="5"[^>]*>', table):
        assert "col-" not in editor_cell


# --- (b) the sizing rules ---------------------------------------------------------------------


def test_header_labels_are_held_on_one_line():
    css = stylesheet_without_comments()
    assert ("white-space", "nowrap", None) in declarations_for(css, ".register-table th"), (
        "without this the header cells break as 'PERI OD' and 'SC OR E'"
    )


@pytest.mark.parametrize("column", [c for c, atomic in COLUMNS if atomic])
def test_atomic_value_columns_are_held_on_one_line(column):
    css = stylesheet_without_comments()
    declared = declarations_for(css, f".register-table td.{column}")
    assert ("white-space", "nowrap", None) in declared, (
        f"{column} carries an atomic label; broken across lines it cannot be read at a glance"
    )


def test_the_rating_badge_is_released_from_the_global_badge_nowrap():
    css = stylesheet_without_comments()
    assert ("white-space", "nowrap", None) in declarations_for(css, ".badge"), (
        "the premise of the next assertion: .badge is globally nowrap"
    )
    assert ("white-space", "normal", None) in declarations_for(
        css, ".register-table td.col-rating .badge"
    ), "held on one line the rating pill took 219.30px of a 620px panel and starved the rest"


# --- (c) the page-overflow guard --------------------------------------------------------------


def test_the_register_table_has_no_unconditional_minimum_width():
    """Measured: an unconditional ``min-width`` overflows the page at 681px.

    ``.panel`` is only reset to ``min-width: 0`` inside ``@media (max-width: 680px)``, so above
    that a table minimum propagates out through ``.table-scroll`` (``max-width: 100%`` cannot
    shrink a child below its own minimum) and widens the document. Any minimum for this table
    must therefore sit inside a ``max-width`` media query at or below 680px.
    """
    css = stylesheet_without_comments()
    minimums = [
        (value, at_rule)
        for selector in (".table-scroll .register-table", ".register-table")
        for prop, value, at_rule in declarations_for(css, selector)
        if prop == "min-width"
    ]
    assert minimums, "the narrow-screen minimum is what keeps the 360px row 92.50px tall, not 947.50px"
    for value, at_rule in minimums:
        assert at_rule is not None, (
            f"min-width: {value} is unconditional; measured at 681px that gives "
            "documentElement.scrollWidth 680 against clientWidth 599 -- page overflow"
        )
        breakpoint_px = re.search(r"max-width:\s*(\d+)px", at_rule)
        assert breakpoint_px, f"min-width: {value} is inside {at_rule!r}, not a max-width query"
        assert int(breakpoint_px.group(1)) <= 680, (
            f"min-width: {value} applies up to {breakpoint_px.group(1)}px, above the 680px "
            "breakpoint where .panel stops being reset to min-width: 0"
        )
