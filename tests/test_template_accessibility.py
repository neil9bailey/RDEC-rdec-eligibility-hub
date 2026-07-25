"""Accessibility conformance tests for the Jinja templates and the stylesheet.

Epic EPIC-RDEC-2026-07-VERIFIED-FIXES, increment E5-1.

These tests parse the template *sources* with the standard library only
(``html.parser``); ADR-0002 line 54 forbids introducing a new dependency, so no
HTML/CSS library is used.

Two properties are asserted:

E5-1(a) every form control has a programmatic accessible name.
E5-1(b) no ``id`` that is emitted inside a repeated context (a ``{% for %}``
        block, including the per-row partials ``_cost_lines.html`` and
        ``_evidence_items.html``) is a constant, because a constant ``id`` in a
        loop emits N duplicates. Duplicate ids break the label/control
        association *and* any HTMX ``hx-target`` selector.

Both detectors are proved non-vacuous against pinned pre-fix fixtures below, so
they cannot silently degrade into assertions that can never fail.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"

#: Controls that must carry an accessible name.
CONTROL_TAGS = {"input", "select", "textarea", "button"}
#: ``input`` types that are not exposed to assistive technology as named controls.
EXEMPT_INPUT_TYPES = {"hidden"}
#: ``input`` types whose ``value`` attribute supplies the accessible name.
VALUE_NAMED_INPUT_TYPES = {"submit", "reset", "button"}

JINJA_STATEMENT = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", re.S)
LABEL_FOR = re.compile(r"<label[^>]*?\bfor=\"([^\"]*)\"", re.I)
ID_ATTR = re.compile(r"\bid=\"([^\"]*)\"", re.I)
FOR_TAG = re.compile(r"\{%-?\s*(for|endfor)\b")


def template_files() -> list[Path]:
    files = sorted(TEMPLATE_DIR.glob("*.html"))
    assert files, f"no templates found under {TEMPLATE_DIR}"
    return files


class ControlCollector(HTMLParser):
    """Collect form controls and note whether each sits inside a ``<label>``.

    Jinja delimiters survive parsing as ordinary text or as junk attributes,
    neither of which affects the two properties under test.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[dict] = []
        self._label_depth = 0
        self._open_buttons: list[int] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag == "label":
            self._label_depth += 1
        if tag in CONTROL_TAGS:
            self.controls.append(
                {
                    "tag": tag,
                    "line": self.getpos()[0],
                    "attrs": attr_map,
                    "inside_label": self._label_depth > 0,
                    "has_text": False,
                }
            )
            if tag == "button":
                self._open_buttons.append(len(self.controls) - 1)

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "label" and self._label_depth:
            self._label_depth -= 1
        if tag == "button" and self._open_buttons:
            self._open_buttons.pop()

    def handle_data(self, data: str) -> None:
        if not self._open_buttons:
            return
        if JINJA_STATEMENT.sub("", data).strip():
            self.controls[self._open_buttons[-1]]["has_text"] = True


def accessible_name_sources(control: dict, label_for_ids: set[str]) -> list[str]:
    """Return the techniques that give ``control`` an accessible name."""
    attrs = control["attrs"]
    tag = control["tag"]
    input_type = attrs.get("type", "").strip().lower()
    found: list[str] = []

    if tag == "input" and input_type in EXEMPT_INPUT_TYPES:
        return ["exempt-type"]
    if tag == "input" and input_type in VALUE_NAMED_INPUT_TYPES and attrs.get("value", "").strip():
        found.append("value")
    if attrs.get("aria-label", "").strip():
        found.append("aria-label")
    if attrs.get("aria-labelledby", "").strip():
        found.append("aria-labelledby")
    if attrs.get("title", "").strip():
        found.append("title")
    if tag == "button" and control["has_text"]:
        found.append("button-text")
    if control["inside_label"]:
        found.append("wrapping-label")
    control_id = attrs.get("id", "").strip()
    if control_id and control_id in label_for_ids:
        found.append("label-for")
    return found


def unnamed_controls(source: str) -> list[dict]:
    """Controls in ``source`` that a screen reader would announce unnamed."""
    parser = ControlCollector()
    parser.feed(source)
    label_for_ids = set(LABEL_FOR.findall(source))
    return [c for c in parser.controls if not accessible_name_sources(c, label_for_ids)]


def ids_in_repeated_context(source: str) -> list[tuple[int, str]]:
    """Return ``(line, id)`` for every constant ``id`` emitted inside a loop.

    An ``id`` inside a ``{% for %}`` block must vary per iteration, i.e. it must
    interpolate a Jinja expression. A constant one emits N identical ids.
    """
    offenders: list[tuple[int, str]] = []
    for match in ID_ATTR.finditer(source):
        depth = 0
        for tag in FOR_TAG.finditer(source, 0, match.start()):
            depth += 1 if tag.group(1) == "for" else -1
        if depth > 0 and "{{" not in match.group(1):
            offenders.append((source.count("\n", 0, match.start()) + 1, match.group(1)))
    return offenders


# --------------------------------------------------------------------------
# E5-1(a) accessible names
# --------------------------------------------------------------------------


@pytest.mark.parametrize("template", template_files(), ids=lambda p: p.name)
def test_every_form_control_has_an_accessible_name(template: Path) -> None:
    offenders = unnamed_controls(template.read_text(encoding="utf-8"))
    detail = "\n".join(
        f"  {template.name}:{c['line']} <{c['tag']} "
        f"name={c['attrs'].get('name', '?')!r}>"
        for c in offenders
    )
    assert not offenders, (
        f"{len(offenders)} control(s) in {template.name} have no accessible "
        f"name (no label[for], wrapping <label>, aria-label, aria-labelledby "
        f"or title):\n{detail}"
    )


def test_zero_controls_without_an_accessible_name_across_all_templates() -> None:
    """The headline number this increment exists to move: 314 -> 0."""
    total = sum(
        len(unnamed_controls(t.read_text(encoding="utf-8"))) for t in template_files()
    )
    assert total == 0, f"{total} form controls across the templates are unnamed"


# --------------------------------------------------------------------------
# E5-1(b) no duplicate id emitted by a repeated partial or loop
# --------------------------------------------------------------------------


@pytest.mark.parametrize("template", template_files(), ids=lambda p: p.name)
def test_no_constant_id_is_emitted_inside_a_loop(template: Path) -> None:
    offenders = ids_in_repeated_context(template.read_text(encoding="utf-8"))
    detail = "\n".join(f"  {template.name}:{line} id={value!r}" for line, value in offenders)
    assert not offenders, (
        f"{len(offenders)} constant id(s) in {template.name} are emitted inside a "
        f"{{% for %}} block and will render as duplicates:\n{detail}"
    )


@pytest.mark.parametrize(
    "partial", ["_cost_lines.html", "_evidence_items.html"], ids=lambda n: n
)
def test_row_partials_scope_every_id_to_the_row(partial: str) -> None:
    """The per-row partials are the top duplicate-id risk in this codebase."""
    source = (TEMPLATE_DIR / partial).read_text(encoding="utf-8")
    row_var = re.search(r"\{%-?\s*for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s", source)
    assert row_var, f"{partial} is expected to render one row per record"
    scope = "{{ %s.id }}" % row_var.group(1)
    unscoped = [
        (source.count("\n", 0, m.start()) + 1, m.group(1))
        for m in ID_ATTR.finditer(source)
        if scope not in m.group(1) and "{{" not in m.group(1)
    ]
    # The container id of the partial itself is rendered once, outside the loop.
    unscoped = [(line, value) for line, value in unscoped if line > source.count("\n", 0, source.index("{% for")) + 1]
    assert not unscoped, f"{partial} emits row ids that are not scoped to {scope}: {unscoped}"


def test_label_for_targets_resolve_to_a_control_in_the_same_template() -> None:
    """A ``for=`` that points at nothing is worse than no label at all."""
    dangling: list[str] = []
    for template in template_files():
        source = template.read_text(encoding="utf-8")
        declared = set(ID_ATTR.findall(source))
        for target in LABEL_FOR.findall(source):
            if target not in declared:
                dangling.append(f"{template.name}: for={target!r}")
    assert not dangling, "label[for] values with no matching id:\n" + "\n".join(dangling)


# --------------------------------------------------------------------------
# Non-vacuity: both detectors must fire on the pinned pre-fix shapes.
# --------------------------------------------------------------------------

#: The pre-fix shape, copied verbatim from app/templates/companies.html:76 at
#: tag ``pre-fix-baseline``. Visually labelled, programmatically unnamed.
PRE_FIX_UNNAMED_CONTROL = (
    '<div class="field"><label>Legal company name</label>'
    '<input name="company_name" required></div>'
)

#: The naive fix for the per-row partials: a constant id inside the row loop.
NAIVE_DUPLICATE_ID_ROW = (
    "{% for cost in context.costs %}"
    '<div class="field"><label for="hours">Hours</label>'
    '<input id="hours" name="hours"></div>'
    "{% endfor %}"
)


def test_unnamed_detector_fires_on_the_pre_fix_shape() -> None:
    offenders = unnamed_controls(PRE_FIX_UNNAMED_CONTROL)
    assert [c["attrs"].get("name") for c in offenders] == ["company_name"]


def test_duplicate_id_detector_fires_on_the_naive_row_fix() -> None:
    assert ids_in_repeated_context(NAIVE_DUPLICATE_ID_ROW) == [(1, "hours")]
    # ...and does not fire once the id is row-scoped.
    assert ids_in_repeated_context(
        NAIVE_DUPLICATE_ID_ROW.replace('"hours"', '"hours-{{ cost.id }}"')
    ) == []
