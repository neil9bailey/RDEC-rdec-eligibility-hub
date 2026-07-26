"""ADR-0002 Ruling R10: the back link on the validation error page must be a real destination.

R10 settled two things about this page. The skip-link and header findings do not hold on the facts
-- the page has no navigation to skip and its furniture is deliberate, and converting the one
hand-built HTML string in the Hub into a Jinja template would move the security boundary that
ADR-0004 D7 exists to protect. Header and nav parity is explicitly NOT authorised here.

What is authorised, and what this module pins, is the defect underneath: the default back link was
``javascript:history.back()``. That is not a URL. It does nothing when scripts are blocked or a CSP
is tightened, it gives assistive technology no destination to announce, and it was the default
every caller that passes no path inherited.

The invariant is stated positively and covers more than the default, because a default that is
correct today is not a control: no link this page renders may use a ``javascript:`` scheme, and
every caller must pass an origin-relative path. The call sites are read from the AST of
``app/main.py`` rather than listed, so a caller added tomorrow is checked tomorrow.

Escaping is not this module's subject -- ``tests/test_validation_error_escaping.py`` owns ADR-0004
D7 -- but the last assertion here proves this increment did not weaken it, because changing the
default is exactly the kind of edit that could.
"""

from __future__ import annotations

import ast
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.form_utils import DEFAULT_BACK_HREF, validation_error_response


REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN = REPO_ROOT / "app" / "main.py"
JAVASCRIPT_SCHEME = re.compile(r"javascript\s*:", re.IGNORECASE)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value or "" for name, value in attrs if name == "href")


def rendered_hrefs(response) -> list[str]:
    parser = _Links()
    parser.feed(response.body.decode("utf-8"))
    parser.close()
    return parser.hrefs


def call_site_back_hrefs() -> list[str]:
    """Every ``back_href`` app/main.py hands this page, read from its own source.

    An f-string call site (``f"/projects/{project_id}/costs"``) is reduced to its leading literal,
    which is the part that decides the scheme and the origin.
    """
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if name != "validation_error_response":
            continue
        argument = node.args[1] if len(node.args) > 1 else next(
            (keyword.value for keyword in node.keywords if keyword.arg == "back_href"), None
        )
        if argument is None:
            values.append(DEFAULT_BACK_HREF)
        elif isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            values.append(argument.value)
        elif isinstance(argument, ast.JoinedStr) and argument.values:
            head = argument.values[0]
            assert isinstance(head, ast.Constant) and isinstance(head.value, str), (
                f"app/main.py line {node.lineno}: back_href is an f-string that does not begin "
                f"with a literal, so its scheme cannot be checked here"
            )
            values.append(head.value)
        else:
            raise AssertionError(
                f"app/main.py line {node.lineno}: back_href is a computed expression. R10 requires "
                f"a real path; add the check for this shape rather than removing this assertion."
            )
    return values


def test_the_default_back_link_is_a_real_path():
    """R10: the default becomes a real path -- the workflow home."""
    assert DEFAULT_BACK_HREF == "/"

    hrefs = rendered_hrefs(validation_error_response(["Gross cost must be a number."]))

    assert hrefs, "the page rendered no link at all; a user has no way back"
    assert all(href.startswith("/") for href in hrefs), hrefs


@pytest.mark.parametrize(
    "back_href",
    [None, "/projects", "/framework-intelligence/sources", "/projects/1/costs"],
)
def test_no_rendered_document_uses_a_javascript_scheme(back_href):
    """R10: ``javascript:`` must never be the destination of a link the Hub renders."""
    errors = ["Customer is required."]
    response = (
        validation_error_response(errors)
        if back_href is None
        else validation_error_response(errors, back_href)
    )
    document = response.body.decode("utf-8")

    assert not JAVASCRIPT_SCHEME.search(document), "a javascript: scheme reached the rendered page"
    assert all(not JAVASCRIPT_SCHEME.match(href) for href in rendered_hrefs(response))
    assert response.status_code == 400


def test_every_call_site_passes_an_origin_relative_path():
    """A default that is correct today is not a control. Every caller is checked, from the AST.

    Derived rather than listed so a caller added tomorrow is checked tomorrow -- the same reason
    the export jargon sweep derives its vocabulary instead of enumerating it.
    """
    values = call_site_back_hrefs()

    assert len(values) >= 10, f"only {len(values)} call sites found; the AST scan has stopped seeing them"
    offenders = [value for value in values if not value.startswith("/")]
    assert not offenders, f"back_href values that are not origin-relative paths: {sorted(set(offenders))}"


def test_changing_the_default_did_not_weaken_the_escaping():
    """ADR-0004 D7 is owned by tests/test_validation_error_escaping.py; this proves R10 kept it.

    The increment that touches the back link carries its own proof that the boundary still holds,
    because that is the edit most likely to move it.
    """
    response = validation_error_response(["A."], '/projects/1" onmouseover="alert(1)')
    document = response.body.decode("utf-8")

    assert 'onmouseover="alert(1)"' not in document
    assert "&quot;" in document, "the back link is no longer being escaped with quote=True"
    assert rendered_hrefs(response) == ['/projects/1" onmouseover="alert(1)']
