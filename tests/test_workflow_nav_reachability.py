"""E5-NAVMENU: the More menu is in the viewport at every width, and the strip that does scroll says so.

Epic EPIC-RDEC-2026-07-VERIFIED-FIXES.

G4 measured the header live in Chrome: at a 900px viewport the More button rendered
at left 889 / right 961 -- past the right edge of the screen. It was reachable by
scrolling the nav strip, and it opened correctly once reached, but nothing on the
screen said the strip could be scrolled.

Measured cause: ``.workflow-nav`` was itself the ``overflow-x: auto`` container and
the button was its last child, so the button scrolled away with the steps. Measured
in headless Chrome against the built image, ``summary`` bounding box, before -> after:

    viewport   summary right   in viewport      panel gap   panel aligned to button
     360px      870 ->  344    no  -> yes         6px       no  -> yes
     681px      961 ->  659    no  -> yes         6px       no  -> yes
     900px      961 ->  878    no  -> yes         6px       no  -> yes
    1100px     1078 -> 1078    yes -> yes         6px       yes -> yes
    1280px     1258 -> 1258    yes -> yes         6px       yes -> yes
    1480px     1458 -> 1458    yes -> yes         6px       yes -> yes

``documentElement.scrollWidth == clientWidth`` at all six widths, before and after,
and the skip link is still the first focusable element on the page.

The steps themselves still overflow a narrow screen (863px of content in a 781px
strip at 900px), and the container reserved no scrollbar height at any width
(``offsetHeight - clientHeight`` was 0 at all six), so at rest there was no mark on
the screen that anything could move. The local/scroll gradient pair now uncovers a
shadow at exactly the edge that has content beyond it: verified by screenshot at
900px showing the right shadow at rest, the left shadow (and no right shadow) once
scrolled to the end, and neither at 1280px where nothing overflows.

Template assertions strip Jinja comments first. The comment that explains this change
names ``.workflow-steps`` and ``.workflow-nav``, so an unstripped source would let the
markup be reverted while the note describing it kept the test green.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_TEMPLATE = ROOT / "app" / "templates" / "base.html"
STYLES = ROOT / "app" / "static" / "styles.css"

JINJA = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", re.S)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)

#: Every workflow destination that must stay reachable.
STEP_HREFS = ["/", "/companies", "/customers", "/projects", "/costs", "/final-review"]


class Ancestry(HTMLParser):
    """Record, for each element of interest, the chain of classes enclosing it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, tuple[str, ...]]] = []
        self.seen: list[tuple[str, tuple[str, ...], list[tuple[str, tuple[str, ...]]], dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        attributes = {k: (v or "") for k, v in attrs}
        classes = tuple(attributes.get("class", "").split())
        self.seen.append((tag, classes, list(self.stack), attributes))
        if tag not in {"img", "br", "input", "meta", "link", "hr"}:
            self.stack.append((tag, classes))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


@pytest.fixture(scope="module")
def parsed() -> Ancestry:
    parser = Ancestry()
    parser.feed(JINJA.sub("", BASE_TEMPLATE.read_text(encoding="utf-8")))
    return parser


@pytest.fixture(scope="module")
def stylesheet() -> str:
    return CSS_COMMENT.sub("", STYLES.read_text(encoding="utf-8"))


def rule_body(stylesheet: str, selector: str) -> str:
    """Concatenate every top-level (non-media) declaration block for *selector*."""
    bodies = []
    for match in re.finditer(r"(?m)^([^{}@\n][^{}]*)\{([^{}]*)\}", stylesheet):
        selectors = [" ".join(part.split()) for part in match.group(1).split(",")]
        if selector in selectors:
            bodies.append(match.group(2))
    return "\n".join(bodies)


def find(parsed: Ancestry, tag: str, css_class: str):
    return [entry for entry in parsed.seen if entry[0] == tag and css_class in entry[1]]


# --- the More button is outside the scroll container ------------------------------------------


def test_the_more_menu_is_not_inside_the_scrolling_strip(parsed):
    menus = find(parsed, "details", "nav-menu")
    assert len(menus) == 1, "expected exactly one More menu in the header"
    _tag, _classes, ancestors, _attrs = menus[0]
    ancestor_classes = {css_class for _t, classes in ancestors for css_class in classes}
    assert "workflow-steps" not in ancestor_classes, (
        "the More button is back inside the scroll container; measured at 900px that put it "
        "at right 961, off a 900px viewport"
    )
    assert "workflow-nav" in ancestor_classes, "the More button must stay in the workflow header"


def test_the_workflow_steps_are_inside_the_scrolling_strip(parsed):
    hrefs_in_strip = [
        attrs.get("href")
        for tag, _classes, ancestors, attrs in parsed.seen
        if tag == "a" and any("workflow-steps" in classes for _t, classes in ancestors)
    ]
    assert hrefs_in_strip == STEP_HREFS, f"the workflow steps changed: {hrefs_in_strip}"


def test_no_ancestor_between_the_more_button_and_the_header_scrolls(parsed, stylesheet):
    """A scrollable ancestor is what put the button off-screen, and would also clip its panel.

    Overflow clips a positioned descendant only when the descendant's containing block is the
    overflow element or below it, so this is also the guard on the panel staying anchored to the
    sticky ``.topbar``.
    """
    _tag, _classes, ancestors, _attrs = find(parsed, "details", "nav-menu")[0]
    between = []
    for _t, classes in reversed(ancestors):
        if "topbar" in classes:
            break
        between.extend(classes)
    assert between, "expected at least .workflow-nav between the More menu and .topbar"
    for css_class in between:
        body = rule_body(stylesheet, f".{css_class}")
        assert not re.search(r"overflow[a-z-]*\s*:\s*(auto|scroll)", body), (
            f".{css_class} encloses the More button and scrolls: {body.strip()!r}"
        )


def test_the_more_menu_is_not_a_containing_block(stylesheet):
    """``position`` on .nav-menu would move the panel's containing block off .topbar."""
    assert not re.search(r"position\s*:", rule_body(stylesheet, ".nav-menu"))


# --- the strip that does scroll announces it --------------------------------------------------


def test_the_steps_strip_is_the_scroll_container(stylesheet):
    assert re.search(r"overflow-x\s*:\s*auto", rule_body(stylesheet, ".workflow-steps"))
    assert not re.search(
        r"overflow[a-z-]*\s*:\s*(auto|scroll)", rule_body(stylesheet, ".workflow-nav")
    ), "the outer nav must not scroll, or the More button scrolls out of view with the steps"


def test_the_scroll_is_visually_announced(stylesheet):
    """The local/scroll pair is the affordance; without it the strip gives no sign it moves."""
    body = rule_body(stylesheet, ".workflow-steps")
    attachment = re.search(r"background-attachment\s*:\s*([^;]+);", body)
    assert attachment, (
        "no background-attachment on the steps strip: the container reserves no scrollbar "
        "height at any measured width, so without this there is no scroll affordance at all"
    )
    layers = [layer.strip() for layer in attachment.group(1).split(",")]
    assert layers.count("local") >= 1 and layers.count("scroll") >= 1, (
        f"the affordance needs a content-attached cover over a viewport-attached shadow: {layers}"
    )
    assert body.count("gradient(") == len(layers), (
        "every background-attachment layer must have a matching background image"
    )


def test_the_steps_strip_carries_no_focusable_element_of_its_own(parsed):
    """The pinned metric is one tab stop from the skip link to <main>; a wrapper must add none."""
    wrappers = find(parsed, "div", "workflow-steps")
    assert len(wrappers) == 1
    _tag, _classes, _ancestors, attrs = wrappers[0]
    assert "tabindex" not in attrs
    assert "onclick" not in attrs
