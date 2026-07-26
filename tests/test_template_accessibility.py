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


# ==========================================================================
# E5-2: focus indicator, skip link, sticky navigation, dropdown anchoring
# ==========================================================================

STYLESHEET = Path(__file__).resolve().parent.parent / "app" / "static" / "styles.css"
BASE_TEMPLATE = TEMPLATE_DIR / "base.html"

#: Colours the focus ring is drawn against in this palette.
ADJACENT_COLOURS = {
    "page white": "#ffffff",
    "panel background": "#fafbfc",
    "paper background": "#f3f5f7",
    "control border": "#c8c0d3",
}
#: WCAG 2.2 SC 1.4.11 Non-text Contrast.
MINIMUM_FOCUS_CONTRAST = 3.0


def parse_hex(colour: str) -> tuple[float, float, float]:
    value = colour.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(rgb) -> float:
    """WCAG 2.x relative luminance."""

    def channel(raw: float) -> float:
        srgb = raw / 255.0
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def css_custom_property(name: str, stylesheet: str) -> str:
    match = re.search(rf"--{re.escape(name)}\s*:\s*([^;]+);", stylesheet)
    assert match, f"custom property --{name} is not defined in styles.css"
    return match.group(1).strip()


@pytest.fixture(scope="module")
def stylesheet() -> str:
    return STYLESHEET.read_text(encoding="utf-8")


def test_focus_ring_token_meets_wcag_non_text_contrast(stylesheet: str) -> None:
    """SC 1.4.11: the ratio is computed from the token, never eyeballed."""
    token = css_custom_property("focus-ring", stylesheet)
    assert re.fullmatch(r"#[0-9a-fA-F]{3,6}", token), (
        f"--focus-ring must be an opaque hex colour so its contrast is "
        f"computable without compositing; got {token!r}"
    )
    ring = parse_hex(token)
    measured = {
        name: round(contrast_ratio(ring, parse_hex(bg)), 2)
        for name, bg in ADJACENT_COLOURS.items()
    }
    failing = {n: r for n, r in measured.items() if r < MINIMUM_FOCUS_CONTRAST}
    assert not failing, (
        f"--focus-ring {token} fails WCAG 2.2 SC 1.4.11 (>= {MINIMUM_FOCUS_CONTRAST}:1) "
        f"against {failing}; all measurements: {measured}"
    )


def test_form_controls_use_the_focus_ring_token(stylesheet: str) -> None:
    rule = re.search(
        r"input:focus,\s*select:focus,\s*textarea:focus\s*\{([^}]*)\}", stylesheet
    )
    assert rule, "the form-control focus rule is missing from styles.css"
    body = rule.group(1)
    assert "var(--focus-ring)" in body, (
        "the form-control focus outline must use --focus-ring so the asserted "
        f"contrast ratio is the one actually rendered; got: {body.strip()!r}"
    )
    assert "rgba(" not in body, (
        "a translucent outline composites against the background and loses "
        f"contrast; that is the pre-fix defect. Got: {body.strip()!r}"
    )


#: The pre-fix focus outline at tag ``pre-fix-baseline``, styles.css:1013.
PRE_FIX_FOCUS_OUTLINE = ((0, 160, 181), 0.18)


def composite_over(rgb, alpha: float, backdrop):
    """Alpha-composite ``rgb`` at ``alpha`` over an opaque ``backdrop``."""
    return tuple(rgb[i] * alpha + backdrop[i] * (1 - alpha) for i in range(3))


def test_pre_fix_focus_colour_fails_the_same_assertion() -> None:
    """Non-vacuity: the pinned pre-fix outline colour must fail this check.

    A translucent outline is composited by the browser before it is seen, so the
    declared colour is not the rendered one. This is exactly why the defect was
    invisible to inspection of the stylesheet alone.
    """
    rgb, alpha = PRE_FIX_FOCUS_OUTLINE
    for name, backdrop in ADJACENT_COLOURS.items():
        rendered = composite_over(rgb, alpha, parse_hex(backdrop))
        ratio = contrast_ratio(rendered, parse_hex(backdrop))
        assert ratio < MINIMUM_FOCUS_CONTRAST, (
            f"the pre-fix focus colour is expected to fail SC 1.4.11 against "
            f"{name}; measured {ratio:.2f}:1. If this passes, the contrast "
            f"maths is wrong and the positive assertion proves nothing."
        )


def test_overflow_x_hidden_is_not_reinstated_on_html_or_body(stylesheet: str) -> None:
    """ADR-0002 Ruling R1.

    ``overflow-x: hidden`` on html/body hides overflow instead of preventing it
    and disables ``position: sticky`` on the workflow navigation.
    """
    offenders = []
    for selector in ("html", "body"):
        rule = re.search(rf"(?m)^{selector}\s*\{{([^}}]*)\}}", stylesheet)
        assert rule, f"no top-level {selector} rule found in styles.css"
        if re.search(r"overflow(-x)?\s*:\s*hidden", rule.group(1)):
            offenders.append(selector)
    assert not offenders, (
        f"{offenders} still set overflow-x: hidden; ADR-0002 R1 removed it and "
        f"line 70 is now proven by scrollWidth measurement instead"
    )


def test_local_scroll_container_exists_for_wide_content(stylesheet: str) -> None:
    """R1's permitted remedy: wide content scrolls locally, the page does not."""
    rule = re.search(r"\.table-scroll\s*\{([^}]*)\}", stylesheet)
    assert rule, ".table-scroll container is missing from styles.css"
    assert re.search(r"overflow-x\s*:\s*auto", rule.group(1)), rule.group(1)


def test_sticky_topbar_is_not_disabled_by_an_ancestor_overflow(stylesheet: str) -> None:
    """position: sticky is inert if any scrollable ancestor clips it."""
    topbar = re.search(r"(?m)^\.topbar\s*\{([^}]*)\}", stylesheet)
    assert topbar and "position: sticky" in topbar.group(1)
    for ancestor in (".app-shell",):
        rule = re.search(rf"(?m)^{re.escape(ancestor)}\s*\{{([^}}]*)\}}", stylesheet)
        if rule:
            assert not re.search(r"overflow[^:]*:\s*(hidden|auto|scroll)", rule.group(1)), (
                f"{ancestor} clips its overflow, which disables sticky on .topbar"
            )


def test_dropdown_panel_is_anchored_to_the_header_not_a_fixed_offset(
    stylesheet: str,
) -> None:
    """The panel detached from its button between 681px and 1100px.

    ``position: fixed; top: 132px`` assumed one header height. The header wraps
    to 225px in that band, so the panel rendered 93px *above* its own button.
    """
    rule = re.search(r"\.nav-menu-panel\s*\{([^}]*)\}", stylesheet)
    assert rule, ".nav-menu-panel rule is missing"
    body = rule.group(1)
    assert "position: absolute" in body, (
        f"the panel must be anchored to the sticky header, not the viewport: {body.strip()!r}"
    )
    top = re.search(r"top\s*:\s*([^;]+);", body)
    assert top and "%" in top.group(1), (
        f"top must track the real header height, not a hard-coded pixel offset; got {top}"
    )
    assert not re.search(r"top\s*:\s*\d+px", stylesheet[rule.start() : rule.end()])
    # ...and no media query may reinstate a hard-coded top for the panel.
    for extra in re.finditer(r"\.nav-menu-panel\s*\{([^}]*)\}", stylesheet):
        assert not re.search(r"top\s*:\s*\d+px", extra.group(1)), (
            f"a hard-coded panel top survives: {extra.group(1).strip()!r}"
        )


# --------------------------------------------------------------------------
# Skip link
# --------------------------------------------------------------------------


class FocusableCollector(HTMLParser):
    """Focusable elements, in document order, and where <main> starts."""

    FOCUSABLE = {"a", "button", "select", "textarea", "summary", "input"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.focusables: list[tuple[str, dict]] = []
        self.main_index: int | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag == "main" and self.main_index is None:
            self.main_index = len(self.focusables)
            return
        if "disabled" in attr_map:
            return
        if attr_map.get("tabindex", "").strip() == "-1":
            return
        if tag == "input" and attr_map.get("type", "").lower() == "hidden":
            return
        if tag == "a" and "href" not in attr_map:
            return
        if tag in self.FOCUSABLE or attr_map.get("tabindex", "").strip() not in ("", "-1"):
            self.focusables.append((tag, attr_map))


def focusables_before_main(source: str):
    parser = FocusableCollector()
    parser.feed(source)
    assert parser.main_index is not None, "template has no <main> element"
    return parser.focusables[: parser.main_index]


def tab_stops_to_reach_main(source: str) -> int:
    """Tab presses a keyboard user needs before they can enter <main>.

    A skip link as the first focusable element makes this 1 regardless of how
    many header controls sit in front of <main> in the DOM. Reordering the DOM
    so that <main> literally precedes the header would make the raw count 1 too,
    but it decouples focus order from visual order (CSS ``order`` does not move
    tab order), which breaks WCAG SC 2.4.3 Focus Order. So the metric under test
    is the traversal cost, not the DOM position.
    """
    preceding = focusables_before_main(source)
    if not preceding:
        return 0
    tag, attrs = preceding[0]
    main = re.search(r"<main\b([^>]*)>", source)
    target = attrs.get("href", "").lstrip("#")
    if tag == "a" and target and main and f'id="{target}"' in main.group(1):
        return 1
    return len(preceding)


def test_one_tab_stop_is_enough_to_reach_the_main_landmark() -> None:
    """Was 17: brand link, 2 header actions, 6 workflow steps, More, 7 menu items.

    A keyboard or screen-reader user had to traverse every one of them, on every
    page, before reaching the content.
    """
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    assert tab_stops_to_reach_main(source) == 1, (
        "the first focusable element must be a skip link that targets <main>"
    )
    preceding = focusables_before_main(source)
    tag, attrs = preceding[0]
    assert tag == "a" and "skip-link" in attrs.get("class", ""), preceding[:1]


def test_skip_link_metric_is_not_vacuous() -> None:
    """Without the skip link the same metric reports the full header traversal."""
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    without_skip_link = re.sub(r'\s*<a class="skip-link".*?</a>', "", source, flags=re.S)
    assert 'class="skip-link"' not in without_skip_link
    assert tab_stops_to_reach_main(without_skip_link) == 17, (
        "the pre-fix header is expected to cost 17 tab stops; if this number "
        "changes, update it deliberately rather than weakening the assertion"
    )


def test_skip_link_targets_the_main_landmark() -> None:
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    link = re.search(r'<a class="skip-link" href="#([^"]+)"', source)
    assert link, "no skip link found as the first element of the app shell"
    target = link.group(1)
    main = re.search(r"<main\b([^>]*)>", source)
    assert main, "base.html has no <main> element"
    assert f'id="{target}"' in main.group(1), (
        f"skip link points at #{target} but <main> is {main.group(1).strip()!r}"
    )
    assert 'tabindex="-1"' in main.group(1), (
        "<main> needs tabindex=-1 so the skip link moves focus, not just scroll "
        "position; tabindex=-1 keeps it out of the tab order"
    )


def test_skip_link_is_visible_when_focused(stylesheet: str) -> None:
    """A skip link that never becomes visible is a trap for sighted keyboard users."""
    hidden = re.search(r"\.skip-link\s*\{([^}]*)\}", stylesheet)
    shown = re.search(r"\.skip-link:focus\s*\{([^}]*)\}", stylesheet)
    assert hidden and shown, "skip link needs both a resting and a :focus rule"
    assert "display: none" not in hidden.group(1), (
        "display: none removes the skip link from the tab order entirely"
    )
    assert re.search(r"top\s*:\s*-?\d", hidden.group(1)), hidden.group(1)
    assert re.search(r"top\s*:\s*0", shown.group(1)), shown.group(1)


# ==========================================================================
# E5-3: no internal terminology reaches a Finance or Ayming reviewer
# ==========================================================================

APP_DIR = Path(__file__).resolve().parent.parent / "app"
LABELS_TEMPLATE = TEMPLATE_DIR / "_labels.html"

#: ADR-0002 line 59: replacement copy must not read as an approval, a rejection
#: or a verdict. "not currently supportable" is explicitly acceptable.
FORBIDDEN_VERDICT_WORDS = ("not eligible", "rejected", "fails", "approved", "qualifies")

#: ADR-0002 line 58 preserve-clause. Ruling R2 confirms it protects exactly this
#: one string and the mechanism that renders it.
CAVEAT = "Requires competent professional and tax review."


@pytest.fixture(scope="module")
def labels_source() -> str:
    return LABELS_TEMPLATE.read_text(encoding="utf-8")


def label_map(name: str, source: str) -> dict[str, str]:
    block = re.search(rf"{name}\s*=\s*\{{(.*?)\}}\s*%\}}", source, re.S)
    assert block, f"{name} is not defined in _labels.html"
    return dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"', block.group(1)))


def test_record_labels_cover_every_audited_record_type(labels_source: str) -> None:
    """A new model must not be able to leak its Python class name silently."""
    mapping = label_map("RECORD_LABELS", labels_source)
    models = set(
        re.findall(r"^class (\w+)\(", (APP_DIR / "models.py").read_text(encoding="utf-8"), re.M)
    )
    literals = set()
    for module in APP_DIR.glob("*.py"):
        literals.update(
            re.findall(r'entity_type\s*=\s*["\']([A-Za-z]\w*)["\']', module.read_text(encoding="utf-8"))
        )
    required = (models | literals) - {"AuditEvent"} | {"AuditEvent"}
    missing = sorted(required - set(mapping))
    assert not missing, (
        f"these record types would render their Python class name in the change "
        f"history: {missing}. Add them to RECORD_LABELS in app/templates/_labels.html."
    )


def test_no_label_reads_as_a_verdict(labels_source: str) -> None:
    """ADR-0002 line 59: indicators, never approvals or rejections."""
    offenders = []
    for map_name in ("RECORD_LABELS", "ENTITLEMENT_LABELS"):
        for key, value in label_map(map_name, labels_source).items():
            for word in FORBIDDEN_VERDICT_WORDS:
                if word in value.lower():
                    offenders.append(f"{map_name}[{key}] = {value!r} contains {word!r}")
    assert not offenders, offenders


def test_audit_page_shows_business_labels_but_submits_stored_values() -> None:
    """The filter value is the query parameter the route matches on.

    Translating the *value* would silently break the filter; only the option
    text may change.
    """
    source = (TEMPLATE_DIR / "audit.html").read_text(encoding="utf-8")
    assert '{% import "_labels.html" as labels %}' in source
    option = re.search(r"<option value=\"\{\{ item \}\}\"[^>]*>([^<]*)</option>", source)
    assert option, "the record-type filter option was not found"
    assert "labels.record_label(item)" in option.group(1), (
        f"option text must be the business label; got {option.group(1)!r}"
    )
    assert 'value="{{ item }}"' in source, "the stored value must still be submitted"
    assert not re.search(r"\{\{ event\.entity_type \}\}", source), (
        "the change-history table still renders the raw Python class name"
    )
    assert "labels.record_label(event.entity_type)" in source


@pytest.mark.parametrize(
    "template", ["_score_panel.html", "projects.html", "project_report.html"], ids=lambda n: n
)
def test_rating_colour_name_is_never_the_displayed_status(template: str) -> None:
    """ADR-0002 R2: a colour is not a status, but the value is load-bearing.

    ``score.rating`` must survive in the class attribute (CSS class and
    ``dashboard_metrics`` key) while the visible text uses ``rating_label``.
    """
    source = (TEMPLATE_DIR / template).read_text(encoding="utf-8")
    assert 'class="badge {{ score.rating }}"' in source, (
        f"{template} must keep score.rating as the CSS class; changing the value "
        f"would break the score-band lookup, the rating counts and the stylesheet"
    )
    displayed = re.findall(r'class="badge \{\{ score\.rating \}\}"\s*>\s*\{\{ ([^}]+) \}\}', source)
    assert displayed, f"no rating badge found in {template}"
    for expression in displayed:
        assert expression.strip() == "score.rating_label", (
            f"{template} displays {expression.strip()!r} as the rating; it must be "
            f"score.rating_label"
        )


@pytest.mark.parametrize(
    "template", ["project_assessment.html", "project_detail.html"], ids=lambda n: n
)
def test_entitlement_enum_is_never_displayed_raw(template: str) -> None:
    source = (TEMPLATE_DIR / template).read_text(encoding="utf-8")
    assert 'class="badge {{ context.entitlement.status }}"' in source, (
        "the status value must stay in the class attribute: .badge.ambiguous_tax_review "
        "and its siblings are real CSS classes"
    )
    assert not re.search(
        r'class="badge \{\{ context\.entitlement\.status \}\}"\s*>\s*\{\{ context\.entitlement\.status \}\}',
        source,
    ), f"{template} still renders the raw entitlement enum as the badge text"
    assert "labels.entitlement_label(context.entitlement.status)" in source
    assert '{% import "_labels.html" as labels %}' in source


def test_relabelled_options_still_submit_the_stored_value() -> None:
    """An <option> with no value= submits its own text.

    Relabelling such an option silently changes what the form writes to the
    database. Every option whose text is a derived label must therefore carry an
    explicit value attribute.
    """
    offenders = []
    for template in template_files():
        source = template.read_text(encoding="utf-8")
        for match in re.finditer(r"<option\b([^>]*)>\s*\{\{([^}]+)\}\}", source):
            attrs, text = match.group(1), match.group(2).strip()
            if "labels." not in text:
                continue
            if not re.search(r'\bvalue="', attrs):
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(f"{template.name}:{line} <option{attrs}> renders {text}")
    assert not offenders, (
        "these relabelled options would submit the label instead of the stored "
        "value:\n" + "\n".join(offenders)
    )


def test_no_internal_abbreviation_reaches_the_user() -> None:
    """"BU" is internal shorthand a Finance or Ayming reviewer will not know."""
    offenders = []
    for template in template_files():
        source = template.read_text(encoding="utf-8")
        # Jinja comments are not rendered, so they are not user-facing.
        rendered = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
        for match in re.finditer(r"\bBU\b", rendered):
            offenders.append(f"{template.name}:{rendered.count(chr(10), 0, match.start()) + 1}")
    assert not offenders, f"internal abbreviation 'BU' is still shown to users at {offenders}"


def test_raw_enum_shapes_are_not_displayed_as_badge_text() -> None:
    """Guard against the general shape, not just the four reported instances.

    A badge whose visible text is the same expression as its CSS class is
    rendering a stored identifier, which is the defect class E5-3 fixes.
    """
    offenders = []
    for template in template_files():
        source = template.read_text(encoding="utf-8")
        for match in re.finditer(
            r'class="badge \{\{ ([^}]+?) \}\}"\s*>\s*\{\{ ([^}]+?) \}\}', source
        ):
            css_expression, text_expression = (g.strip() for g in match.groups())
            if css_expression == text_expression:
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(f"{template.name}:{line} renders {css_expression} as its own label")
    assert not offenders, "\n".join(offenders)


# --------------------------------------------------------------------------
# Regression guards for the states verified good before this increment
# --------------------------------------------------------------------------


# ==========================================================================
# E2-FE-3: the import-preview disclosure markup holds the same standard
#
# The detectors above glob every template, so app/templates/data_management.html
# and its new preview markup are already inside every one of them. The tests
# below add what those detectors cannot see: that the detectors actually reach
# the new markup, that the disclosure ADR-0004 D1.3/D1.4 mandates is present,
# that meaning is never carried by a glyph alone, and that amber is reserved for
# states that need attention.
# ==========================================================================

DATA_MANAGEMENT_TEMPLATE = TEMPLATE_DIR / "data_management.html"

#: ADR-0004 D1.4 and D1.3. Each of these is a disclosure the preview must render.
REQUIRED_PREVIEW_DISCLOSURE = (
    "row.existing_display",
    "row.existing_id",
    "row.changed_fields",
    "row.no_change",
    "import_preview.notices",
)


@pytest.fixture(scope="module")
def data_management_source() -> str:
    """The template with Jinja comments stripped.

    A comment is not rendered and, more to the point here, a comment that
    *names* the thing under test would satisfy or defeat a source-level
    assertion without any markup existing.
    """
    source = DATA_MANAGEMENT_TEMPLATE.read_text(encoding="utf-8")
    return re.sub(r"\{#.*?#\}", "", source, flags=re.S)


def test_preview_renders_the_ratified_issue_shape(data_management_source: str) -> None:
    """ADR-0004 D7. ``issues`` is the contract; ``errors`` was a temporary mirror.

    The mirror existed only so the un-wired template would not print dataclass
    reprs at users. While the template still reads it, it cannot be deleted.
    """
    assert "row.issues" in data_management_source, (
        "the preview must render row.issues, the ratified ImportIssue shape"
    )
    assert "issue.message" in data_management_source, (
        "the message is the only part of an issue a person reads"
    )
    assert not re.search(r"\brow\.errors\b", data_management_source), (
        "the preview still reads the derived row['errors'] mirror, so the "
        "governance-engine Principal cannot delete it"
    )


def test_preview_never_parses_an_issue_message(data_management_source: str) -> None:
    """D7: ``code`` is the contract a frontend may branch on, ``message`` is not."""
    offenders = re.findall(
        r"issue\.message\s*(?:\||\.)\s*(\w+)", data_management_source
    )
    assert not offenders, (
        f"the template applies {offenders} to issue.message; message is display "
        f"only and must never be parsed. Branch on issue.code instead."
    )


def test_preview_discloses_the_live_record_it_would_change(
    data_management_source: str,
) -> None:
    """ADR-0004 D1.4, and the reason finding C2 was destructive.

    The preview showed the uploaded record's name. The live record it was about
    to overwrite was never named, so nobody could see the substitution.
    """
    missing = [name for name in REQUIRED_PREVIEW_DISCLOSURE if name not in data_management_source]
    assert not missing, (
        f"the import preview does not render {missing}; ADR-0004 D1.4 requires the "
        f"live record, its identifier and every field that moves to be disclosed "
        f"before an operator can apply, and D1.3 requires the create-only notice"
    )
    assert "row.display" in data_management_source, (
        "the uploaded record's own name must stay on the row beside the live one; "
        "seeing both together is what makes a substitution visible"
    )


def test_preview_disclosure_detector_fires_on_the_pre_fix_markup() -> None:
    """Non-vacuity: the pinned pre-fix row must fail the assertion above.

    Copied from app/templates/data_management.html:136-142 as it stood before
    this increment.
    """
    pre_fix = (
        "{% for row in import_preview.rows[:100] %}"
        "<tr><td>{{ row.dataset_label }}</td><td>{{ row.display }}</td>"
        '<td><span class="badge">{{ row.status }}</span></td>'
        "<td>{{ row.errors|join(' ') if row.errors else 'Checks passed' }}</td>"
        "</tr>{% endfor %}"
    )
    assert [name for name in REQUIRED_PREVIEW_DISCLOSURE if name not in pre_fix] == list(
        REQUIRED_PREVIEW_DISCLOSURE
    ), "the pre-fix row is expected to disclose none of it"
    assert re.search(r"\brow\.errors\b", pre_fix), (
        "the pre-fix row is expected to read the derived mirror"
    )


def test_amber_is_never_used_for_a_state_that_needs_no_attention(
    data_management_source: str,
) -> None:
    """Amber means "needs attention" in this interface.

    A row that changes nothing, and a row deliberately left alone because the
    operator chose add-new-only, are neutral facts. Colouring them amber trains
    a reviewer to ignore amber on the rows that really do overwrite live data.
    """
    badge = re.search(
        r"<span class=\"badge \{\{([^}]*?row\.status[^}]*?)\}\}\">\{\{([^}]+?)\}\}</span>",
        data_management_source,
    )
    assert badge, "the preview result badge was not found"
    colour_expression, text_expression = (g.strip() for g in badge.groups())
    assert "'amber' if row.status == 'update'" in colour_expression, (
        f"amber must be reserved for a real overwrite; got {colour_expression!r}"
    )
    for neutral in ("row.no_change", "'skip'"):
        assert f"'amber' if {neutral}" not in colour_expression, (
            f"{neutral} is a neutral state and must not be ambered"
        )
    assert "no change" in text_expression, (
        "a row whose changed_fields are empty must be marked so a reviewer can "
        f"skip it; got {text_expression!r}"
    )


def test_the_detectors_actually_reach_the_new_preview_markup(
    data_management_source: str,
) -> None:
    """Guard against a green that means "nothing was inspected".

    ``unnamed_controls`` and ``ids_in_repeated_context`` return an empty list
    both when the markup is clean and when there is no markup. This pins that
    the preview block is present and is inside a loop the id detector walks.
    """
    assert 'class="import-preview"' in data_management_source
    assert "{% for row in import_preview.rows" in data_management_source
    assert len(ControlCollectorCount(data_management_source)) >= 15, (
        "the data-management page is expected to carry the export, import, "
        "cleanup and purge controls; a sudden drop means the parser stopped early"
    )
    # The loop the preview rows are emitted from must be visible to the
    # constant-id detector, i.e. FOR_TAG must match it.
    loops = [m.group(1) for m in FOR_TAG.finditer(data_management_source)]
    assert loops.count("for") == loops.count("endfor") >= 5, loops


def ControlCollectorCount(source: str) -> list[dict]:  # noqa: N802 - test helper
    parser = ControlCollector()
    parser.feed(source)
    return parser.controls


def test_no_meaning_is_carried_by_a_decorative_glyph_alone() -> None:
    """SC 1.1.1. An arrow hidden from assistive technology needs a text twin.

    The before/after arrow in the preview is ``aria-hidden``, so a screen-reader
    user would otherwise hear "Sector Transport operator Rail operations" with
    no indication of which value replaces which.
    """
    offenders: list[str] = []
    inspected = 0
    for template in template_files():
        source = template.read_text(encoding="utf-8")
        for match in re.finditer(r'aria-hidden="true"[^>]*>([^<]*)<', source):
            glyph = match.group(1).strip()
            if not glyph:
                continue
            inspected += 1
            window = source[match.end() : match.end() + 400]
            if "visually-hidden" not in window:
                line = source.count("\n", 0, match.start()) + 1
                offenders.append(f"{template.name}:{line} hides {glyph!r} with no text equivalent")
    assert not offenders, "\n".join(offenders)
    assert inspected >= 1, (
        "no aria-hidden glyph was inspected, so this test proved nothing; the "
        "before/after arrow in the import preview is expected to be one"
    )
    # Non-vacuity: the same glyph with no text twin must be caught.
    naked = '<span aria-hidden="true">&rarr;</span><span>after</span>'
    assert "visually-hidden" not in naked[naked.index(">&rarr;<") :]


def test_visually_hidden_text_stays_in_the_accessibility_tree(stylesheet: str) -> None:
    """``display: none`` would remove the text equivalent from screen readers too."""
    rule = re.search(r"\.visually-hidden\s*\{([^}]*)\}", stylesheet)
    assert rule, ".visually-hidden is used in the templates but not defined in styles.css"
    body = rule.group(1)
    for banned in ("display: none", "visibility: hidden"):
        assert banned not in body, (
            f".visually-hidden must not use {banned}; that removes the text from "
            f"the accessibility tree, which is the opposite of its purpose"
        )
    assert "clip-path" in body or "clip:" in body, body


def test_no_focus_rule_anywhere_uses_a_translucent_outline(stylesheet: str) -> None:
    """Generalises the pinned form-control check to every focus indicator.

    ``.segmented-options input`` is ``opacity: 0``, so the outline on its sibling
    ``span`` was the only focus indicator the import-mode radios had, and it was
    ``rgba(0, 160, 181, 0.25)`` -- the same composite-to-1.2:1 defect that was
    already fixed on the form controls.
    """
    offenders = []
    for match in re.finditer(r"([^{}]*:focus[^{}]*)\{([^}]*)\}", stylesheet):
        selector, body = match.group(1).strip(), match.group(2)
        outline = re.search(r"outline\s*:\s*([^;]+);", body)
        if outline and "rgba(" in outline.group(1):
            line = stylesheet.count("\n", 0, match.start()) + 1
            offenders.append(f"styles.css:{line} {selector} -> {outline.group(1).strip()}")
    assert not offenders, (
        "a translucent outline is composited against the background before it is "
        "seen, so the declared colour is not the rendered one:\n" + "\n".join(offenders)
    )


def test_translucent_focus_detector_fires_on_the_pre_fix_rule() -> None:
    """Non-vacuity for the check above, against the exact pre-fix declaration."""
    pre_fix = (
        ".segmented-options input:focus-visible + span {\n"
        "  outline: 3px solid rgba(0, 160, 181, 0.25);\n}"
    )
    matched = []
    for rule in re.finditer(r"([^{}]*:focus[^{}]*)\{([^}]*)\}", pre_fix):
        outline = re.search(r"outline\s*:\s*([^;]+);", rule.group(2))
        if outline and "rgba(" in outline.group(1):
            matched.append(rule.group(1).strip())
    assert len(matched) == 1, f"the detector must fire on the pinned pre-fix rule; got {matched}"
    ring, alpha = (0, 160, 181), 0.25
    ratio = contrast_ratio(
        composite_over(ring, alpha, parse_hex("#eef1f4")), parse_hex("#eef1f4")
    )
    assert ratio < MINIMUM_FOCUS_CONTRAST, (
        f"the pre-fix segmented-option ring is expected to fail SC 1.4.11 against "
        f"its own track; measured {ratio:.2f}:1"
    )


def test_compliance_caveat_mechanism_is_intact() -> None:
    """ADR-0002 line 58, as narrowed by Ruling R2.

    R2 leaves Epic 5's copy changes unconstrained by the preserve clause but
    protects the caveat string and its render site, which is in base.html and
    therefore on every page.
    """
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    assert "{{ caveat }}" in source, (
        "base.html no longer renders the caveat, so it would disappear from every page"
    )
    services = (APP_DIR / "services.py").read_text(encoding="utf-8")
    assert f'CAVEAT = "{CAVEAT}"' in services or f"CAVEAT = '{CAVEAT}'" in services, (
        "app/services.py CAVEAT must stay byte-identical to the preserved string"
    )


# ==========================================================================
# E5-BANNER: the ADR-0005 D3.4 data-integrity banner
#
# app/main.py injects ``data_integrity_warning`` into every template context.
# Until this increment no template read it, so the entire signal that link
# enforcement had been withheld was one startup log line. These tests pin the
# markup; tests/test_data_integrity_banner_and_restore_affordance.py drives it
# through a real request in its non-None state, which is the part that proves
# the banner has actually been seen rendered.
# ==========================================================================

#: Words that would turn an operational report into a claim of repair or a
#: verdict on the operator's records. ADR-0005 D3.5 (no automatic repair, ever)
#: and the ADR-0005 Guardrails (operational information only).
BANNER_FORBIDDEN_COPY = (
    "repair",
    "fixed",
    "fix them",
    "corrected",
    "cleaned up",
    "removed for you",
    "we will",
    "invalid",
    "corrupt",
    "damaged",
)


def base_banner_block() -> str:
    """The rendered banner markup from base.html, comments stripped.

    A Jinja comment is never rendered, and the comment above this block names
    every identifier under test, so a source-level assertion made against the
    unstripped file would pass on the explanation alone.
    """
    source = re.sub(r"\{#.*?#\}", "", BASE_TEMPLATE.read_text(encoding="utf-8"), flags=re.S)
    match = re.search(
        r"\{%\s*if data_integrity_warning\s*%\}(.*?)\{%\s*endif\s*%\}", source, re.S
    )
    assert match, (
        "base.html does not render the ADR-0005 D3.4 banner. app/main.py injects "
        "data_integrity_warning into every context; with no template reading it the "
        "only signal that link enforcement was withheld is a startup log line."
    )
    return match.group(1)


def test_base_renders_the_injected_integrity_warning() -> None:
    block = base_banner_block()
    assert "{{ data_integrity_warning }}" in block, (
        "the banner must render the injected string itself, not a re-composed one; "
        f"got: {block.strip()!r}"
    )
    assert 'class="integrity-banner"' in block


def test_the_banner_detector_fires_on_the_pre_fix_base_template() -> None:
    """Non-vacuity: base.html as it stood before this increment must fail.

    Copied from app/templates/base.html:65-68 before E5-BANNER.
    """
    pre_fix = (
        '<main class="container" id="main-content" tabindex="-1">\n'
        '{% if flash_error %}<div class="flash-message error" role="alert">'
        "{{ flash_error }}</div>{% endif %}\n"
        "{% block content %}{% endblock %}\n</main>"
    )
    assert not re.search(r"\{%\s*if data_integrity_warning\s*%\}", pre_fix), (
        "the pre-fix template is expected to render nothing for the injected key"
    )
    assert "data_integrity_warning" not in pre_fix


def test_the_banner_is_guarded_so_a_clean_database_shows_nothing() -> None:
    """``INTEGRITY_WARNING`` is ``None`` on a clean scan, and None must render nothing.

    An unguarded ``{{ data_integrity_warning }}`` would print "None" on every page
    of a healthy workspace, which is worse than the defect it replaces.
    """
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    guard = source.index("{% if data_integrity_warning %}")
    render = source.index("{{ data_integrity_warning }}")
    assert guard < render, "the banner must be inside the truthiness guard"


def test_the_banner_is_inside_main_so_the_skip_link_cannot_skip_it() -> None:
    """The skip link is the first focusable element and it targets <main>.

    Anything rendered before <main> is exactly what one tab stop jumps over, so a
    banner placed there would be systematically invisible to keyboard and screen
    reader users -- the same class of defect (an invisible signal) that D3.4 exists
    to close.
    """
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    main_open = re.search(r"<main\b[^>]*>", source)
    main_close = source.index("</main>")
    assert main_open
    banner = source.index('class="integrity-banner"')
    assert main_open.end() < banner < main_close, (
        "the integrity banner must render inside <main>; it is at "
        f"{banner}, <main> spans {main_open.end()}..{main_close}"
    )


def test_the_banner_adds_no_tab_stop_and_no_focusable_control() -> None:
    """A persistent banner must not cost every user a keystroke on every page.

    It also carries no dismiss control on purpose: the condition is a standing
    one, and a dismissed banner would restore the silence D3.4 removes.
    """
    block = base_banner_block()
    parser = FocusableCollector()
    parser.feed(block)
    assert parser.focusables == [], f"the banner introduces focusable elements: {parser.focusables}"
    assert not unnamed_controls(block)
    # ...and the pinned whole-page metrics are unmoved.
    source = BASE_TEMPLATE.read_text(encoding="utf-8")
    assert tab_stops_to_reach_main(source) == 1
    assert len(focusables_before_main(source)) == 18


def test_the_banner_region_has_an_accessible_name() -> None:
    """role="region" without a name is not exposed as a landmark at all."""
    block = base_banner_block()
    region = re.search(r'<div class="integrity-banner"([^>]*)>', block)
    assert region, block
    labelledby = re.search(r'aria-labelledby="([^"]+)"', region.group(1))
    assert labelledby or re.search(r'aria-label="[^"]+"', region.group(1)), (
        f"the banner region needs an accessible name; got {region.group(1).strip()!r}"
    )
    if labelledby:
        assert f'id="{labelledby.group(1)}"' in block, (
            f"aria-labelledby points at #{labelledby.group(1)}, which the banner does not define"
        )
    assert 'role="alert"' not in block, (
        "role=alert interrupts assistive technology on every page load; this is a "
        "standing condition, not an event"
    )


def test_the_banner_copy_claims_no_repair_and_passes_no_verdict() -> None:
    """ADR-0005 D3.5 and the Guardrails.

    The Hub reports and withholds; it never modifies, quarantines or deletes. Copy
    that implies otherwise would be a lie about what the application does. Copy that
    called the records invalid would be a verdict, which ADR-0002 line 59 forbids.
    """
    block = base_banner_block()
    visible = " ".join(re.sub(r"<[^>]+>", " ", block).split()).lower()
    offenders = [word for word in BANNER_FORBIDDEN_COPY if word in visible]
    assert not offenders, f"banner copy {visible!r} contains {offenders}"
    for word in FORBIDDEN_VERDICT_WORDS:
        assert word not in visible, f"banner copy reads as a verdict: {word!r}"
    assert visible, "the banner has no visible text of its own"


def css_rule_body(selector: str, stylesheet: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}", stylesheet)
    assert match, f"{selector} is not defined in styles.css"
    return match.group(1)


def declared_colours(body: str) -> tuple[str, str]:
    fg = re.search(r"(?<![-\w])color\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", body)
    bg = re.search(r"background\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", body)
    assert fg and bg, f"expected an opaque colour pair; got {body.strip()!r}"
    return fg.group(1), bg.group(1)


def test_banner_text_meets_wcag_contrast(stylesheet: str) -> None:
    """SC 1.4.3, computed from the tokens actually declared."""
    fg, bg = declared_colours(css_rule_body(".integrity-banner", stylesheet))
    ratio = contrast_ratio(parse_hex(fg), parse_hex(bg))
    assert ratio >= 4.5, f"{fg} on {bg} measures {ratio:.2f}:1"


def test_the_banner_cannot_widen_the_page(stylesheet: str) -> None:
    """ADR-0002 line 70: no horizontal page overflow at 360px.

    A record display name pasted from a spreadsheet can be one unbroken token, and
    the banner text is not under the template's control -- it is composed from the
    orphan report.
    """
    body = css_rule_body(".integrity-banner", stylesheet)
    assert re.search(r"overflow-wrap\s*:\s*(anywhere|break-word)", body), (
        f"the banner needs a wrapping rule for unbroken tokens; got {body.strip()!r}"
    )
    assert not re.search(r"(?<!max-)width\s*:\s*\d", body), (
        f"a fixed width on a full-bleed banner is an overflow source; got {body.strip()!r}"
    )


def test_no_template_disables_autoescaping() -> None:
    """Confirmed-good before this increment: no |safe anywhere. Keep it that way."""
    offenders = []
    for template in template_files():
        source = template.read_text(encoding="utf-8")
        for match in re.finditer(r"\|\s*(safe|e\s*\(\s*false\s*\))|\{%\s*autoescape\s+false", source):
            offenders.append(f"{template.name}:{source.count(chr(10), 0, match.start()) + 1}")
    assert not offenders, f"autoescaping is bypassed at {offenders}"
